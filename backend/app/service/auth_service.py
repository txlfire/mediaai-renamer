"""本地认证和权限服务。"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import sqlite3

from app.core.config import AppSettings

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 120_000
SESSION_TTL_HOURS = 24

ALL_PERMISSIONS: tuple[str, ...] = (
    "settings:write",
    "source:write",
    "scan:run",
    "metadata:submit",
    "rename:execute",
    "rollback:execute",
    "audit:read",
    "task:manage",
)


@dataclass(frozen=True)
class AuthUser:
    """已认证用户。"""

    id: int
    username: str
    display_name: str
    permissions: tuple[str, ...]
    enabled: bool
    last_login_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LoginResult:
    """登录结果。"""

    access_token: str
    token_type: str
    expires_at: str
    user: AuthUser


class AuthServiceError(ValueError):
    """认证服务错误。"""


class BootstrapUnavailableError(AuthServiceError):
    """已有用户时不允许再次 bootstrap。"""


class InvalidCredentialsError(AuthServiceError):
    """用户名或密码错误。"""


class InvalidSessionError(AuthServiceError):
    """会话无效。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_text() -> str:
    return _utc_now().isoformat()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _hash_password(password: str, salt_hex: str | None = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return (
        f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    parts = stored_hash.split("$")
    if len(parts) != 4:
        return False
    algorithm, iterations_text, salt_hex, digest_hex = parts
    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False
    try:
        iterations = int(iterations_text)
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            iterations,
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, digest_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_user(row: sqlite3.Row) -> AuthUser:
    raw_permissions = row["permissions_json"] if "permissions_json" in row.keys() else "[]"
    permissions = _parse_permissions(raw_permissions)
    return AuthUser(
        id=int(row["id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"]),
        permissions=permissions,
        enabled=bool(row["enabled"]),
        last_login_at=row["last_login_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _parse_permissions(raw_value: str) -> tuple[str, ...]:
    try:
        values = json.loads(raw_value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    normalized: list[str] = []
    for value in values:
        if isinstance(value, str) and value in ALL_PERMISSIONS and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _validate_username(username: str) -> str:
    value = username.strip()
    if len(value) < 3:
        raise AuthServiceError("用户名至少需要 3 个字符")
    if len(value) > 64:
        raise AuthServiceError("用户名不能超过 64 个字符")
    return value


def _validate_password(password: str) -> str:
    if len(password) < 8:
        raise AuthServiceError("密码至少需要 8 个字符")
    return password


def user_count(settings: AppSettings) -> int:
    """返回当前用户数量。"""

    with closing(_connect(settings)) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    return int(row["count"])


def bootstrap_admin(
    settings: AppSettings,
    username: str,
    display_name: str,
    password: str,
) -> AuthUser:
    """首次启动时创建本地管理员。"""

    username = _validate_username(username)
    password = _validate_password(password)
    display_name = display_name.strip() or username
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        existing_count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if existing_count:
            raise BootstrapUnavailableError("已存在用户，不能再次初始化管理员")
        cursor = connection.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, permissions_json, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                display_name,
                _hash_password(password),
                json.dumps(list(ALL_PERMISSIONS), ensure_ascii=False),
                1,
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_user(row)


def login(settings: AppSettings, username: str, password: str) -> LoginResult:
    """验证用户名密码并创建会话。"""

    username = username.strip()
    now = _utc_now()
    now_text = now.isoformat()
    expires_at = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ? AND enabled = 1",
            (username,),
        ).fetchone()
        if row is None or not _verify_password(password, str(row["password_hash"])):
            raise InvalidCredentialsError("用户名或密码错误")

        access_token = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO user_sessions "
            "(user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (int(row["id"]), _hash_token(access_token), expires_at, now_text),
        )
        connection.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now_text, now_text, int(row["id"])),
        )
        connection.commit()
        user_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(row["id"]),),
        ).fetchone()
    return LoginResult(
        access_token=access_token,
        token_type="bearer",
        expires_at=expires_at,
        user=_row_to_user(user_row),
    )


def get_user_by_token(settings: AppSettings, access_token: str) -> AuthUser:
    """通过访问 token 获取当前用户。"""

    if not access_token.strip():
        raise InvalidSessionError("登录状态无效或已过期")
    now_text = _utc_now_text()
    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT u.* FROM user_sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? "
            "AND s.revoked_at IS NULL "
            "AND s.expires_at > ? "
            "AND u.enabled = 1",
            (_hash_token(access_token), now_text),
        ).fetchone()
    if row is None:
        raise InvalidSessionError("登录状态无效或已过期")
    return _row_to_user(row)


def logout(settings: AppSettings, access_token: str) -> None:
    """吊销当前访问 token。"""

    now_text = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute(
            "UPDATE user_sessions SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (now_text, _hash_token(access_token)),
        )
        connection.commit()


def user_to_dict(user: AuthUser) -> dict[str, object]:
    """转换为 API 响应。"""

    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "enabled": user.enabled,
        "permissions": list(user.permissions),
        "lastLoginAt": user.last_login_at,
        "createdAt": user.created_at,
        "updatedAt": user.updated_at,
    }


def login_result_to_dict(result: LoginResult) -> dict[str, object]:
    """转换登录结果为 API 响应。"""

    return {
        "accessToken": result.access_token,
        "tokenType": result.token_type,
        "expiresAt": result.expires_at,
        "user": user_to_dict(result.user),
    }
