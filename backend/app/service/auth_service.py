"""本地认证和权限服务。"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.service.audit_service import AuditWriteContext

from app.core.config import AppSettings
from app.service.settings_service import get_effective_settings, SETTING_DEFINITIONS

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
    must_change_password: bool
    password_changed_at: str | None
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


class ResetAdminPasswordDisabledError(AuthServiceError):
    """未启用 admin 密码重置。"""


class UserNotFoundError(AuthServiceError):
    """用户不存在。"""


class UserConflictError(AuthServiceError):
    """用户数据冲突。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_text() -> str:
    return _utc_now().isoformat()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def cleanupExpiredSessions(settings: AppSettings, *, limit: int = 200) -> dict:
    """有界删除明确过期会话，不删审计、用户或尚未过期的撤销记录。"""
    if not 1 <= limit <= 1000:
        raise ValueError("会话清理数量必须为1至1000")
    cutoff = _utc_now_text()
    with closing(sqlite3.connect(settings.database_path, timeout=0.25)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidates = connection.execute(
            "SELECT id FROM user_sessions WHERE julianday(expires_at) <= julianday(?) "
            "ORDER BY julianday(expires_at), id LIMIT ?", (cutoff, limit + 1),
        ).fetchall()
        ids = [int(row[0]) for row in candidates[:limit]]
        removed = 0
        if ids:
            placeholders = ",".join("?" for _ in ids)
            removed = connection.execute(
                f"DELETE FROM user_sessions WHERE id IN ({placeholders}) "
                "AND julianday(expires_at) <= julianday(?)", [*ids, cutoff],
            ).rowcount
        connection.commit()
    return {"removed_count": removed, "has_more": len(candidates) > limit}


def _user_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(users)")}


def _insert_user(
    connection: sqlite3.Connection,
    username: str,
    display_name: str,
    password: str,
    permissions: tuple[str, ...],
    must_change_password: bool,
    password_changed_at: str | None,
    now: str,
    enabled: bool = True,
) -> int:
    """插入用户并兼容早期库中残留的 role 列。"""

    values: dict[str, object] = {
        "username": username,
        "display_name": display_name,
        "password_hash": _hash_password(password),
        "permissions_json": json.dumps(list(permissions), ensure_ascii=False),
        "must_change_password": 1 if must_change_password else 0,
        "password_changed_at": password_changed_at,
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    }
    if "role" in _user_columns(connection):
        values["role"] = "admin"
    columns = list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    cursor = connection.execute(
        f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    return int(cursor.lastrowid)


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
        must_change_password=bool(row["must_change_password"])
        if "must_change_password" in row.keys()
        else False,
        password_changed_at=row["password_changed_at"]
        if "password_changed_at" in row.keys()
        else None,
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


def _validate_permissions(permissions: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for permission in permissions:
        if permission not in ALL_PERMISSIONS:
            raise AuthServiceError(f"未知权限：{permission}")
        if permission not in normalized:
            normalized.append(permission)
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


def _validate_default_password(password: str) -> str:
    if len(password) < 6:
        raise AuthServiceError("默认管理员密码至少需要 6 个字符")
    return password


def _validate_new_password(password: str, default_password: str) -> str:
    if password == default_password:
        raise AuthServiceError("新密码不能继续使用默认密码")
    return _validate_password(password)


def user_count(settings: AppSettings) -> int:
    """返回当前用户数量。"""

    with closing(_connect(settings)) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    return int(row["count"])


def list_users(settings: AppSettings) -> list[AuthUser]:
    """返回本地用户列表。"""

    with closing(_connect(settings)) as connection:
        rows = connection.execute(
            "SELECT * FROM users ORDER BY enabled DESC, username ASC"
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def list_permission_options() -> tuple[str, ...]:
    """返回可分配权限列表。"""

    return ALL_PERMISSIONS


def _enabled_settings_writer_count(
    connection: sqlite3.Connection,
    exclude_user_id: int | None = None,
) -> int:
    rows = connection.execute(
        "SELECT id, permissions_json FROM users WHERE enabled = 1"
    ).fetchall()
    count = 0
    for row in rows:
        if exclude_user_id is not None and int(row["id"]) == exclude_user_id:
            continue
        if "settings:write" in _parse_permissions(str(row["permissions_json"])):
            count += 1
    return count


def _ensure_settings_writer_remains(
    connection: sqlite3.Connection,
    user_id: int,
    next_enabled: bool,
    next_permissions: tuple[str, ...],
) -> None:
    if next_enabled and "settings:write" in next_permissions:
        return
    if _enabled_settings_writer_count(connection, exclude_user_id=user_id) == 0:
        raise AuthServiceError("至少需要保留一个启用且具备系统设置权限的用户")


def _recordUserMaintenanceAudit(settings: AppSettings, connection: sqlite3.Connection, userId: int,
                                action: str, label: str, auditContext: "AuditWriteContext | None") -> None:
    # 审计服务依赖 AuthUser，此处延迟导入避免模块初始化循环。
    from app.service.audit_service import AuditWriteContext, record_audit_event
    context = auditContext or AuditWriteContext()
    row = connection.execute("SELECT * FROM users WHERE id = ?", (userId,)).fetchone()
    detail = {"username": row["username"]}
    if action != "reset_user_password":
        detail.update(display_name=row["display_name"], enabled=bool(row["enabled"]),
                      permissions=list(_parse_permissions(row["permissions_json"])))
    record_audit_event(settings, event_type="user.manage", action=action, result="success",
                       summary=f"{label}：{row['username']}", target_type="user", target_id=userId,
                       actor=context.actor, actor_name="system" if context.actor is None else None,
                       ip_address=context.ipAddress, user_agent=context.userAgent, detail=detail,
                       connection=connection)


def _recordAuthAudit(settings: AppSettings, connection: sqlite3.Connection, user: AuthUser | None,
                     eventType: str, action: str, label: str, auditContext: "AuditWriteContext | None",
                     *, detail: dict | None = None, actorName: str | None = None) -> None:
    from app.service.audit_service import AuditWriteContext, record_audit_event
    context = auditContext or AuditWriteContext()
    actor = None if actorName else (context.actor or user)
    record_audit_event(settings, event_type=eventType, action=action, result="success",
                       summary=f"{label}：{user.username if user else 'unknown'}", target_type="user",
                       target_id=user.id if user else None, actor=actor, actor_name=actorName or ("unknown" if actor is None else None),
                       ip_address=context.ipAddress, user_agent=context.userAgent,
                       detail=detail or ({"username": user.username} if user else None), connection=connection)


def create_user(
    settings: AppSettings,
    username: str,
    display_name: str,
    password: str,
    permissions: list[str],
    enabled: bool,
    *,
    auditContext: "AuditWriteContext | None" = None,
) -> AuthUser:
    """创建本地用户。"""

    username = _validate_username(username)
    password = _validate_password(password)
    display_name = display_name.strip() or username
    normalized_permissions = _validate_permissions(permissions)
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        try:
            user_id = _insert_user(
                connection,
                username,
                display_name,
                password,
                normalized_permissions,
                True,
                None,
                now,
                enabled,
            )
        except sqlite3.IntegrityError as exc:
            raise UserConflictError("用户名已存在") from exc
        _recordUserMaintenanceAudit(settings, connection, user_id, "create_user", "创建用户", auditContext)
        connection.commit()
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def update_user(
    settings: AppSettings,
    user_id: int,
    display_name: str,
    permissions: list[str],
    enabled: bool,
    *,
    auditContext: "AuditWriteContext | None" = None,
) -> AuthUser:
    """更新用户显示名称、启用状态和权限。"""

    display_name = display_name.strip()
    normalized_permissions = _validate_permissions(permissions)
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise UserNotFoundError("用户不存在")
        next_display_name = display_name or str(row["username"])
        _ensure_settings_writer_remains(connection, user_id, enabled, normalized_permissions)
        connection.execute(
            "UPDATE users SET display_name = ?, permissions_json = ?, enabled = ?, "
            "updated_at = ? WHERE id = ?",
            (
                next_display_name,
                json.dumps(list(normalized_permissions), ensure_ascii=False),
                1 if enabled else 0,
                now,
                user_id,
            ),
        )
        if not enabled:
            connection.execute(
                "UPDATE user_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        _recordUserMaintenanceAudit(settings, connection, user_id, "update_user", "更新用户", auditContext)
        connection.commit()
        updated_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_user(updated_row)


def reset_user_password(settings: AppSettings, user_id: int, new_password: str, *,
                        auditContext: "AuditWriteContext | None" = None) -> AuthUser:
    """重置用户密码，并要求用户下次登录后修改。"""

    password = _validate_password(new_password)
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise UserNotFoundError("用户不存在")
        connection.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, "
            "password_changed_at = NULL, updated_at = ? WHERE id = ?",
            (_hash_password(password), now, user_id),
        )
        connection.execute(
            "UPDATE user_sessions SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        _recordUserMaintenanceAudit(settings, connection, user_id, "reset_user_password", "重置用户密码", auditContext)
        connection.commit()
        updated_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_user(updated_row)


def bootstrap_admin(
    settings: AppSettings,
    username: str,
    display_name: str,
    password: str,
    *,
    auditContext: "AuditWriteContext | None" = None,
) -> AuthUser:
    """首次启动时创建本地管理员。"""

    username = _validate_username(username)
    password = _validate_password(password)
    display_name = display_name.strip() or username
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if existing_count:
            raise BootstrapUnavailableError("已存在用户，不能再次初始化管理员")
        bootstrap_enabled = bool(
            get_effective_settings(settings)["auth.admin_bootstrap_enabled"]
        )
        if not bootstrap_enabled:
            raise BootstrapUnavailableError("初始化管理员功能已关闭")
        user_id = _insert_user(
            connection,
            username,
            display_name,
            password,
            ALL_PERMISSIONS,
            False,
            now,
            now,
        )
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        user = _row_to_user(row)
        definition = SETTING_DEFINITIONS["auth.admin_bootstrap_enabled"]
        connection.execute(
            "INSERT INTO system_settings (key, category, value, description, sensitive, operator, created_at, updated_at) "
            "VALUES (?, ?, 'false', ?, 0, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = 'false', operator = excluded.operator, updated_at = excluded.updated_at",
            (definition.key, definition.category, definition.description, user.username, now, now),
        )
        _recordAuthAudit(settings, connection, user, "auth.bootstrap", "bootstrap_admin", "初始化管理员", auditContext,
                         detail={"username": user.username, "display_name": user.display_name, "bootstrap_enabled": False})
        connection.commit()
    return user


def is_admin_bootstrap_available(settings: AppSettings) -> bool:
    """判断当前实例是否允许初始化首个管理员。"""

    enabled = bool(get_effective_settings(settings)["auth.admin_bootstrap_enabled"])
    return enabled and user_count(settings) == 0


def ensure_default_admin(settings: AppSettings) -> AuthUser | None:
    """按配置在空用户库中创建默认 admin。

    默认密码来自配置文件。创建后的账号会标记为必须修改密码。
    """

    if not settings.auth.default_admin_enabled:
        return None
    username = _validate_username(settings.auth.default_admin_username)
    password = _validate_default_password(settings.auth.default_admin_password)
    display_name = settings.auth.default_admin_display_name.strip() or username
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if existing_count:
            return None
        user_id = _insert_user(
            connection,
            username,
            display_name,
            password,
            ALL_PERMISSIONS,
            True,
            None,
            now,
        )
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        _recordAuthAudit(settings, connection, _row_to_user(row), "auth.bootstrap", "create_default_admin",
                         "创建默认管理员", None, actorName="local_initialization")
        connection.commit()
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_user(row)


def login(
    settings: AppSettings,
    username: str,
    password: str,
    remember_login: bool = False,
    *,
    auditContext: "AuditWriteContext | None" = None,
) -> LoginResult:
    """验证用户名密码并创建会话。"""

    username = username.strip()
    now = _utc_now()
    now_text = now.isoformat()
    if remember_login:
        remember_login_days = int(get_effective_settings(settings)["auth.remember_login_days"])
        expires_at = (now + timedelta(days=remember_login_days)).isoformat()
    else:
        expires_at = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
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
        user_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(row["id"]),),
        ).fetchone()
        _recordAuthAudit(settings, connection, _row_to_user(user_row), "auth.login", "login", "登录成功", auditContext,
                         detail={"username": username, "remember_login": remember_login})
        connection.commit()
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


def logout(settings: AppSettings, access_token: str, *, auditContext: "AuditWriteContext | None" = None) -> None:
    """吊销当前访问 token。"""

    now_text = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT u.* FROM users u JOIN user_sessions s ON s.user_id = u.id WHERE s.token_hash = ?",
                                 (_hash_token(access_token),)).fetchone()
        connection.execute(
            "UPDATE user_sessions SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (now_text, _hash_token(access_token)),
        )
        _recordAuthAudit(settings, connection, _row_to_user(row) if row else None, "auth.logout", "logout", "退出登录", auditContext)
        connection.commit()


def change_password(
    settings: AppSettings,
    user_id: int,
    current_password: str,
    new_password: str,
    *,
    auditContext: "AuditWriteContext | None" = None,
) -> AuthUser:
    """修改当前用户密码。"""

    new_password = _validate_new_password(new_password, settings.auth.default_admin_password)
    now_text = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM users WHERE id = ? AND enabled = 1",
            (user_id,),
        ).fetchone()
        if row is None or not _verify_password(current_password, str(row["password_hash"])):
            raise InvalidCredentialsError("当前密码错误")
        connection.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, "
            "password_changed_at = ?, updated_at = ? WHERE id = ?",
            (_hash_password(new_password), now_text, now_text, user_id),
        )
        connection.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now_text, user_id),
        )
        updated_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        _recordAuthAudit(settings, connection, _row_to_user(updated_row), "auth.password", "change_password", "用户修改密码", auditContext)
        connection.commit()
    return _row_to_user(updated_row)


def reset_admin_password(settings: AppSettings) -> AuthUser:
    """隐藏恢复功能：将 admin 密码重置为配置中的默认密码。"""

    if not settings.auth.admin_password_reset_enabled:
        raise ResetAdminPasswordDisabledError("未启用 admin 密码重置功能")
    username = _validate_username(settings.auth.default_admin_username)
    password = _validate_default_password(settings.auth.default_admin_password)
    display_name = settings.auth.default_admin_display_name.strip() or username
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            user_id = _insert_user(
                connection,
                username,
                display_name,
                password,
                ALL_PERMISSIONS,
                True,
                None,
                now,
            )
        else:
            user_id = int(row["id"])
            connection.execute(
                "UPDATE users SET password_hash = ?, permissions_json = ?, "
                "must_change_password = 1, password_changed_at = NULL, "
                "enabled = 1, updated_at = ? WHERE id = ?",
                (
                    _hash_password(password),
                    json.dumps(list(ALL_PERMISSIONS), ensure_ascii=False),
                    now,
                    user_id,
                ),
            )
        connection.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        updated_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        _recordAuthAudit(settings, connection, _row_to_user(updated_row), "auth.password", "reset_admin_password",
                         "本机已恢复管理员密码并撤销旧会话", None, actorName="local-recovery")
        connection.commit()
    return _row_to_user(updated_row)


def user_to_dict(user: AuthUser) -> dict[str, object]:
    """转换为 API 响应。"""

    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "enabled": user.enabled,
        "permissions": list(user.permissions),
        "mustChangePassword": user.must_change_password,
        "passwordChangedAt": user.password_changed_at,
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
