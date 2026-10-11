"""独立凭据密钥；密钥不由数据库位置推导，也不回显内容。"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.fernet import Fernet

from app.core.config import AppSettings


class CredentialError(ValueError):
    """凭据密钥或密文不可用，继续处理同批条目不会自行恢复。"""


def _hasProtectedCredentials(settings: AppSettings) -> bool:
    if not settings.database_path.exists():
        return False
    with closing(sqlite3.connect(settings.database_path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in (
            ("media_sources", "encrypted_secret"),
            ("metadata_provider_configs", "api_key_encrypted"),
            ("system_settings", "value"),
        ):
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column in columns and connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} LIKE 'v3:%' LIMIT 1"
            ).fetchone():
                return True
    return False


def credentialCipher(settings: AppSettings, *, create: bool = False) -> Fernet:
    envKey = os.environ.get("MEDIAAI_CREDENTIAL_KEY")
    if envKey is not None:
        key = envKey.encode("ascii", errors="replace")
    else:
        keyPath = settings.credential_key_file or settings.data_dir / "secrets" / "credentials.key"
        if keyPath.is_symlink():
            raise CredentialError("凭据密钥文件不能使用符号链接")
        if not keyPath.exists():
            if not create or _hasProtectedCredentials(settings):
                raise CredentialError("凭据密钥缺失，请恢复原密钥；不会生成替代密钥或清空凭据")
            keyPath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # 完整写入临时文件后原子发布；并发创建不能覆盖先成功的密钥。
            fd, tempPath = tempfile.mkstemp(dir=keyPath.parent, prefix=".credential-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(tempPath, keyPath)
                except FileExistsError:
                    pass
            finally:
                Path(tempPath).unlink(missing_ok=True)
        try:
            key = keyPath.read_bytes().strip()
        except OSError as exc:
            raise CredentialError("凭据密钥文件不可读，请检查挂载与文件权限") from exc
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialError("凭据密钥格式无效，请检查环境秘密或密钥文件") from exc
