"""Media source service."""

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import string

from app.core.config import AppSettings
from app.core.logger import get_batch_logger
from app.schema.media import LocalDirectoryEntry, LocalDirectoryListing, MediaSource
from app.service.media_source_secret import encrypt_secret, has_secret
from app.service.shared_protocols.base import ConnectionTestResult, SharedPathContext
from app.service.shared_protocols.registry import get_protocol
from app.service.settings_service import get_effective_settings

VALID_PATH_TYPES = {"local", "unc", "mounted_nfs"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_media_source(row: sqlite3.Row) -> MediaSource:
    return MediaSource(
        id=int(row["id"]),
        name=str(row["name"]),
        path=str(row["path"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        path_type=str(row["path_type"]),
        protocol=str(row["protocol"]),
        host=row["host"],
        share_name=row["share_name"],
        domain=row["domain"],
        username=row["username"],
        secret=None,
        has_secret=has_secret(row["encrypted_secret"]),
        port=row["port"],
        remark=row["remark"],
        nfs_host=row["nfs_host"],
        nfs_export=row["nfs_export"],
        nfs_version=row["nfs_version"],
        nfs_options=row["nfs_options"],
        local_mount_path=row["local_mount_path"],
    )


def _fetch_media_source(connection: sqlite3.Connection, source_id: int) -> MediaSource:
    row = connection.execute(
        "SELECT id, name, path, path_type, protocol, host, share_name, domain, username, "
        "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, nfs_options, "
        "local_mount_path, enabled, created_at, updated_at "
        "FROM media_sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise ValueError("媒体源不存在")
    return _row_to_media_source(row)


def _media_source_context(source: MediaSource) -> SharedPathContext:
    return SharedPathContext(
        path_type=source.path_type,
        username=source.username,
        has_secret=source.has_secret,
        host=source.host,
        share_name=source.share_name,
        domain=source.domain,
        port=source.port,
        nfs_host=source.nfs_host,
        nfs_export=source.nfs_export,
        nfs_version=source.nfs_version,
        nfs_options=source.nfs_options,
        local_mount_path=source.local_mount_path,
    )


def get_media_source_protocol_context(settings: AppSettings, source_id: int) -> SharedPathContext:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        source = _fetch_media_source(connection, source_id)
        row = connection.execute(
            "SELECT encrypted_secret FROM media_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
    return SharedPathContext(
        path_type=source.path_type,
        username=source.username,
        has_secret=has_secret(row["encrypted_secret"] if row else None),
        host=source.host,
        share_name=source.share_name,
        domain=source.domain,
        port=source.port,
        nfs_host=source.nfs_host,
        nfs_export=source.nfs_export,
        nfs_version=source.nfs_version,
        nfs_options=source.nfs_options,
        local_mount_path=source.local_mount_path,
    )


def _validate_media_source_path(path: Path) -> Path:
    if not str(path).strip():
        raise ValueError("媒体源路径不能为空")
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        raise ValueError("媒体源路径不存在")
    if not resolved_path.is_dir():
        raise ValueError("媒体源路径必须是目录")
    return resolved_path


def _normalize_media_source_path(path: str | Path) -> str:
    raw_path = str(path).strip()
    if not raw_path:
        raise ValueError("媒体源路径不能为空")
    return str(Path(raw_path).expanduser())


def _validate_unc_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("媒体源路径不能为空")
    if not normalized.startswith("\\\\"):
        raise ValueError("UNC 路径必须以 \\\\ 开头")
    parts = [part for part in normalized.split("\\") if part]
    if len(parts) < 2:
        raise ValueError("UNC 路径必须包含主机和共享名")
    return normalized


def _normalize_path_type(path_type: str | None) -> str:
    normalized = (path_type or "local").strip()
    if normalized not in VALID_PATH_TYPES:
        raise ValueError("不支持的路径类型")
    return normalized


def _protocol_for_path_type(path_type: str) -> str:
    if path_type == "unc":
        return "smb"
    return path_type


def _validate_media_source_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("媒体源名称不能为空")
    return normalized


def _validate_port(port: int | None) -> int | None:
    if port is None:
        return None
    if port < 0 or port > 65535:
        raise ValueError("端口必须在 0-65535 范围内")
    return port


def _empty_cleanup_summary() -> dict[str, int]:
    return {
        "rename_operation_items": 0,
        "rename_operations": 0,
        "rename_previews": 0,
        "media_files": 0,
        "scan_jobs": 0,
    }


def _merge_cleanup_summary(
    target: dict[str, int],
    source: dict[str, int],
) -> dict[str, int]:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value
    return target


def _delete_related_history(
    connection: sqlite3.Connection,
    source_id: int,
) -> dict[str, int]:
    summary = _empty_cleanup_summary()
    media_file_rows = connection.execute(
        "SELECT id FROM media_files WHERE media_source_id = ?",
        (source_id,),
    ).fetchall()
    media_file_ids = [int(row[0]) for row in media_file_rows]

    preview_ids: list[int] = []
    operation_ids: list[int] = []
    if media_file_ids:
        file_placeholders = ", ".join("?" for _ in media_file_ids)
        preview_rows = connection.execute(
            f"SELECT id FROM rename_previews WHERE media_file_id IN ({file_placeholders})",
            media_file_ids,
        ).fetchall()
        preview_ids = [int(row[0]) for row in preview_rows]

    if preview_ids:
        preview_placeholders = ", ".join("?" for _ in preview_ids)
        operation_rows = connection.execute(
            "SELECT DISTINCT operation_id FROM rename_operation_items "
            f"WHERE rename_preview_id IN ({preview_placeholders})",
            preview_ids,
        ).fetchall()
        operation_ids = [int(row[0]) for row in operation_rows]

        summary["rename_operation_items"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM rename_operation_items "
                f"WHERE rename_preview_id IN ({preview_placeholders})",
                preview_ids,
            ).fetchone()[0]
        )
        connection.execute(
            "DELETE FROM rename_operation_items "
            f"WHERE rename_preview_id IN ({preview_placeholders})",
            preview_ids,
        )

        summary["rename_previews"] = int(
            connection.execute(
                f"SELECT COUNT(*) FROM rename_previews WHERE id IN ({preview_placeholders})",
                preview_ids,
            ).fetchone()[0]
        )
        connection.execute(
            f"DELETE FROM rename_previews WHERE id IN ({preview_placeholders})",
            preview_ids,
        )

    if operation_ids:
        operation_placeholders = ", ".join("?" for _ in operation_ids)
        removable_rows = connection.execute(
            "SELECT ro.id FROM rename_operations ro "
            f"WHERE ro.id IN ({operation_placeholders}) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM rename_operation_items roi WHERE roi.operation_id = ro.id"
            ")",
            operation_ids,
        ).fetchall()
        removable_ids = [int(row[0]) for row in removable_rows]
        if removable_ids:
            removable_placeholders = ", ".join("?" for _ in removable_ids)
            summary["rename_operations"] = len(removable_ids)
            connection.execute(
                f"DELETE FROM rename_operations WHERE id IN ({removable_placeholders})",
                removable_ids,
            )

    summary["media_files"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM media_files WHERE media_source_id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    connection.execute("DELETE FROM media_files WHERE media_source_id = ?", (source_id,))

    summary["scan_jobs"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM scan_jobs WHERE media_source_id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    connection.execute("DELETE FROM scan_jobs WHERE media_source_id = ?", (source_id,))

    return summary


def _windows_drive_entries() -> list[LocalDirectoryEntry]:
    entries: list[LocalDirectoryEntry] = []
    for letter in string.ascii_uppercase:
        drive_path = Path(f"{letter}:\\")
        if drive_path.exists():
            entries.append(
                LocalDirectoryEntry(
                    name=f"{letter}:",
                    path=str(drive_path),
                    is_directory=True,
                    readable=os.access(drive_path, os.R_OK),
                    writable=os.access(drive_path, os.W_OK),
                )
            )
    return entries


def list_local_directories(path: str | None = None) -> LocalDirectoryListing:
    if path is None or not path.strip():
        return LocalDirectoryListing(
            current_path=None,
            parent_path=None,
            entries=_windows_drive_entries(),
        )

    current_path = Path(path).expanduser().resolve()
    if not current_path.exists():
        raise ValueError("目录不存在")
    if not current_path.is_dir():
        raise ValueError("路径不是目录")

    entries = [
        LocalDirectoryEntry(
            name=item.name,
            path=str(item),
            is_directory=True,
            readable=os.access(item, os.R_OK),
            writable=os.access(item, os.W_OK),
        )
        for item in current_path.iterdir()
        if item.is_dir()
    ]
    entries.sort(key=lambda item: item.name.lower())

    parent_path = str(current_path.parent) if current_path.parent != current_path else None
    return LocalDirectoryListing(
        current_path=str(current_path),
        parent_path=parent_path,
        entries=entries,
    )


def list_source_directories(
    settings: AppSettings,
    source_id: int,
    path: str | None = None,
) -> LocalDirectoryListing:
    source = get_media_source(settings, source_id)
    protocol = get_protocol(source.path_type)
    listing = protocol.list_directories(path or source.path, _media_source_context(source))
    browse_limit = int(get_effective_settings(settings).get("shared.directory_browse_limit") or 500)
    return LocalDirectoryListing(
        current_path=listing.current_path,
        parent_path=listing.parent_path,
        entries=[
            LocalDirectoryEntry(
                name=entry.name,
                path=entry.path,
                is_directory=entry.is_directory,
                readable=entry.readable,
                writable=entry.writable,
            )
            for entry in listing.entries[:browse_limit]
        ],
    )


def test_media_source_connection(
    settings: AppSettings,
    source_id: int,
) -> ConnectionTestResult:
    source = get_media_source(settings, source_id)
    protocol = get_protocol(source.path_type)
    result = protocol.test_connection(source.path, _media_source_context(source))
    status = "成功" if result.success else "失败"
    get_batch_logger().info(
        "媒体源连接测试%s source_id=%s path_type=%s path=%s message=%s suggestion=%s",
        status,
        source.id,
        source.path_type,
        source.path,
        result.message,
        result.suggestion or "",
    )
    return result


def test_media_source_connection_payload(
    path_type: str,
    path: str | Path,
    username: str | None = None,
    secret: str | None = None,
    host: str | None = None,
    share_name: str | None = None,
    domain: str | None = None,
    port: int | None = None,
    nfs_host: str | None = None,
    nfs_export: str | None = None,
    nfs_version: str | None = None,
    nfs_options: str | None = None,
    local_mount_path: str | None = None,
) -> ConnectionTestResult:
    source_path_type = _normalize_path_type(path_type)
    port = _validate_port(port)
    if source_path_type == "unc":
        source_path = _validate_unc_path(str(path))
    else:
        source_path = _normalize_media_source_path(path)
    protocol = get_protocol(source_path_type)
    context = SharedPathContext(
        path_type=source_path_type,
        username=username if source_path_type == "unc" else None,
        has_secret=bool(secret) if source_path_type == "unc" else False,
        host=host,
        share_name=share_name,
        domain=domain if source_path_type == "unc" else None,
        port=port,
        nfs_host=nfs_host if source_path_type == "mounted_nfs" else None,
        nfs_export=nfs_export if source_path_type == "mounted_nfs" else None,
        nfs_version=nfs_version if source_path_type == "mounted_nfs" else None,
        nfs_options=nfs_options if source_path_type == "mounted_nfs" else None,
        local_mount_path=local_mount_path if source_path_type == "mounted_nfs" else None,
    )
    result = protocol.test_connection(str(source_path), context)
    status = "成功" if result.success else "失败"
    get_batch_logger().info(
        "媒体源临时连接测试%s path_type=%s path=%s message=%s suggestion=%s",
        status,
        source_path_type,
        source_path,
        result.message,
        result.suggestion or "",
    )
    return result


def create_media_source(
    settings: AppSettings,
    name: str,
    path: str | Path,
    enabled: bool = True,
    path_type: str = "local",
    host: str | None = None,
    share_name: str | None = None,
    domain: str | None = None,
    username: str | None = None,
    secret: str | None = None,
    port: int | None = None,
    remark: str | None = None,
    nfs_host: str | None = None,
    nfs_export: str | None = None,
    nfs_version: str | None = None,
    nfs_options: str | None = None,
    local_mount_path: str | None = None,
) -> MediaSource:
    source_name = _validate_media_source_name(name)
    source_path_type = _normalize_path_type(path_type)
    port = _validate_port(port)
    if source_path_type == "unc":
        source_path = _validate_unc_path(str(path))
        encrypted_secret = encrypt_secret(settings, secret)
    else:
        source_path = _normalize_media_source_path(path)
        username = None
        domain = None
        encrypted_secret = None
    protocol = _protocol_for_path_type(source_path_type)
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(
                "INSERT INTO media_sources "
                "(name, path, path_type, protocol, host, share_name, domain, username, "
                "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, "
                "nfs_options, local_mount_path, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_name,
                    str(source_path),
                    source_path_type,
                    protocol,
                    host,
                    share_name,
                    domain,
                    username,
                    encrypted_secret,
                    port,
                    remark,
                    nfs_host,
                    nfs_export,
                    nfs_version,
                    nfs_options,
                    local_mount_path,
                    int(enabled),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("媒体源路径已存在") from exc
        row = connection.execute(
            "SELECT id, name, path, path_type, protocol, host, share_name, domain, username, "
            "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, nfs_options, "
            "local_mount_path, enabled, created_at, updated_at "
            "FROM media_sources WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        connection.commit()
    return _row_to_media_source(row)


def list_media_sources(settings: AppSettings) -> list[MediaSource]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, path, path_type, protocol, host, share_name, domain, username, "
            "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, nfs_options, "
            "local_mount_path, enabled, created_at, updated_at "
            "FROM media_sources ORDER BY id"
        ).fetchall()
    return [_row_to_media_source(row) for row in rows]


def get_media_source(settings: AppSettings, source_id: int) -> MediaSource:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _fetch_media_source(connection, source_id)


def update_media_source(
    settings: AppSettings,
    source_id: int,
    name: str,
    path: str | Path,
    enabled: bool,
    clear_history_on_path_change: bool = False,
    username: str | None = None,
    secret: str | None = None,
    nfs_host: str | None = None,
    nfs_export: str | None = None,
) -> dict[str, object]:
    source_name = _validate_media_source_name(name)
    cleanup_summary = _empty_cleanup_summary()
    now = _utc_now()

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        current = _fetch_media_source(connection, source_id)
        if current.path_type == "unc":
            source_path = _validate_unc_path(str(path))
        else:
            source_path = _normalize_media_source_path(path)
        path_changed = str(source_path) != current.path
        if path_changed and not clear_history_on_path_change:
            raise ValueError("修改路径将清空历史数据，请确认后重试")
        if path_changed:
            cleanup_summary = _delete_related_history(connection, source_id)
        update_username = username if current.path_type == "unc" else None
        update_secret = encrypt_secret(settings, secret) if current.path_type == "unc" and secret else None
        update_nfs_host = nfs_host if current.path_type == "mounted_nfs" else None
        update_nfs_export = nfs_export if current.path_type == "mounted_nfs" else None
        try:
            connection.execute(
                "UPDATE media_sources SET name = ?, path = ?, username = ?, "
                "encrypted_secret = CASE WHEN ? IS NULL THEN encrypted_secret ELSE ? END, "
                "nfs_host = ?, nfs_export = ?, enabled = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    source_name,
                    str(source_path),
                    update_username,
                    update_secret,
                    update_secret,
                    update_nfs_host,
                    update_nfs_export,
                    int(enabled),
                    now,
                    source_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("媒体源路径已存在") from exc
        source = _fetch_media_source(connection, source_id)
        connection.commit()

    return {"source": source, "cleanup_summary": cleanup_summary}


def set_media_source_enabled(
    settings: AppSettings,
    source_id: int,
    enabled: bool,
) -> MediaSource:
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _fetch_media_source(connection, source_id)
        connection.execute(
            "UPDATE media_sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), now, source_id),
        )
        source = _fetch_media_source(connection, source_id)
        connection.commit()
    return source


def delete_media_source(settings: AppSettings, source_id: int) -> dict[str, object]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _fetch_media_source(connection, source_id)
        cleanup_summary = _delete_related_history(connection, source_id)
        connection.execute("DELETE FROM media_sources WHERE id = ?", (source_id,))
        connection.commit()
    return {"deleted_ids": [source_id], "cleanup_summary": cleanup_summary}


def bulk_delete_media_sources(
    settings: AppSettings,
    source_ids: list[int],
) -> dict[str, object]:
    normalized_ids = list(dict.fromkeys(source_ids))
    if not normalized_ids:
        raise ValueError("请选择要删除的媒体源")

    cleanup_summary = _empty_cleanup_summary()
    deleted_ids: list[int] = []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for source_id in normalized_ids:
            _fetch_media_source(connection, source_id)
            _merge_cleanup_summary(
                cleanup_summary,
                _delete_related_history(connection, source_id),
            )
            connection.execute("DELETE FROM media_sources WHERE id = ?", (source_id,))
            deleted_ids.append(source_id)
        connection.commit()
    return {"deleted_ids": deleted_ids, "cleanup_summary": cleanup_summary}
