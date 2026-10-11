"""Media source service."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import string
import sys

from app.core.config import AppSettings
from app.core.logger import get_batch_logger
from app.schema.media import LocalDirectoryEntry, LocalDirectoryListing, MediaSource
from app.service.media_source_secret import CURRENT_CREDENTIAL_VERSION, decrypt_secret, encrypt_secret, has_secret
from app.service.shared_protocols.base import ConnectionTestResult, SharedPathContext
from app.service.shared_protocols.registry import get_protocol
from app.service.remote_operation_service import acquire_remote_operation_lock, release_remote_operation_lock
from app.service.settings_service import get_effective_settings
from app.utils.redaction import redactUrlCredentials
from app.service.audit_service import AuditWriteContext, record_audit_event

VALID_PATH_TYPES = {"local", "unc", "mounted_nfs", "webdav"}


def _conditionalMoveStatus(source: MediaSource) -> str:
    if source.path_type != "webdav" or not source.capability_snapshot_json:
        return "unknown"
    try:
        snapshot = json.loads(source.capability_snapshot_json)
        conditionalMove = snapshot.get("conditional_move") if isinstance(snapshot, dict) else None
        if not isinstance(conditionalMove, dict):
            return "unknown"
        if conditionalMove.get("endpoint") != source.path:
            return "unknown"
        if conditionalMove.get("credential_version") != source.credential_version:
            return "unknown"
        status = conditionalMove.get("status")
        return status if status in {"verified", "unsupported"} else "unknown"
    except (TypeError, ValueError, json.JSONDecodeError):
        return "unknown"


def _recordSourceAudit(settings: AppSettings, connection: sqlite3.Connection, context: AuditWriteContext | None,
                       action: str, targetId: int | None, detail: dict) -> None:
    auditContext = context or AuditWriteContext()
    labels = {"create_media_source": "新增媒体源", "update_media_source": "更新媒体源",
              "set_media_source_enabled": "更新媒体源启用状态", "delete_media_source": "退役媒体源",
              "bulk_delete_media_sources": "批量退役媒体源",
              "verify_webdav_conditional_move": "验证 WebDAV 条件移动能力"}
    record_audit_event(
        settings, event_type="media_source.change", action=action, result="success", summary=labels[action],
        target_type="media_source", target_id=targetId, detail=detail, actor=auditContext.actor,
        actor_name="system" if auditContext.actor is None else None,
        ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent, connection=connection,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_media_source(row: sqlite3.Row) -> MediaSource:
    return MediaSource(
        id=int(row["id"]),
        name=str(row["name"]),
        path=redactUrlCredentials(str(row["path"])) if row["path_type"] == "webdav" else str(row["path"]),
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
        protocol_endpoint=redactUrlCredentials(row["protocol_endpoint"]) if row["protocol_endpoint"] else None,
        auth_type=row["auth_type"],
        credential_version=int(row["credential_version"]),
        remote_root=row["remote_root"],
        capability_snapshot_json=row["capability_snapshot_json"],
        trusted_certificate_fingerprint=row["trusted_certificate_fingerprint"],
    )


def _fetch_media_source(connection: sqlite3.Connection, source_id: int) -> MediaSource:
    row = connection.execute(
        "SELECT id, name, path, path_type, protocol, host, share_name, domain, username, "
        "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, nfs_options, "
        "local_mount_path, protocol_endpoint, auth_type, credential_version, remote_root, "
        "capability_snapshot_json, trusted_certificate_fingerprint, enabled, created_at, updated_at "
        "FROM media_sources WHERE id = ? AND deleted_at IS NULL",
        (source_id,),
    ).fetchone()
    if row is None:
        raise ValueError("媒体源不存在")
    return _row_to_media_source(row)


def _media_source_context(source: MediaSource, effective_settings: dict[str, object] | None = None) -> SharedPathContext:
    effective = effective_settings or {}
    return SharedPathContext(
        path_type=source.path_type,
        username=source.username,
        secret=None,
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
        connection_timeout_seconds=int(effective.get("shared.connection_timeout_seconds") or 5),
        nfs_operation_timeout_seconds=int(effective.get("shared.nfs_operation_timeout_seconds") or 30),
        root_url=source.path if source.path_type == "webdav" else None,
        conditional_move_status=_conditionalMoveStatus(source),
    )


def _media_source_context_with_secret(settings: AppSettings, source: MediaSource) -> SharedPathContext:
    return get_media_source_protocol_context(settings, source.id)


def get_media_source_protocol_context(settings: AppSettings, source_id: int) -> SharedPathContext:
    effective_settings = get_effective_settings(settings)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        source = _fetch_media_source(connection, source_id)
        row = connection.execute(
            "SELECT encrypted_secret FROM media_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
    secret = decrypt_secret(settings, row["encrypted_secret"]) if row and row["encrypted_secret"] else None
    return SharedPathContext(
        path_type=source.path_type,
        username=source.username,
        secret=secret,
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
        connection_timeout_seconds=int(effective_settings.get("shared.connection_timeout_seconds") or 5),
        nfs_operation_timeout_seconds=int(effective_settings.get("shared.nfs_operation_timeout_seconds") or 30),
        root_url=source.path if source.path_type == "webdav" else None,
        conditional_move_status=_conditionalMoveStatus(source),
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
    if raw_path.lower().startswith("retired://"):
        raise ValueError("不允许使用系统保留路径")
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


def _normalize_remote_protocol_path(path_type: str, path: str | Path) -> str:
    protocol = get_protocol(path_type)
    result = protocol.validate_config(str(path))
    if not result.success:
        raise ValueError(result.message)
    return protocol.normalize_path(str(path))


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


def _invalidateSourceHistory(
    connection: sqlite3.Connection,
    source_id: int,
    eventType: str,
) -> dict[str, int]:
    """保留追溯数据，只失效执行入口和旧索引，不操作真实媒体。"""
    summary = _empty_cleanup_summary()
    source = _fetch_media_source(connection, source_id)
    now = _utc_now()
    snapshot = {
        "id": source.id, "name": source.name, "path": source.path, "pathType": source.path_type,
        "protocol": source.protocol, "enabled": source.enabled,
        "createdAt": source.created_at, "updatedAt": source.updated_at,
    }
    connection.execute(
        "INSERT INTO media_source_history (media_source_id, event_type, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
        (source_id, eventType, json.dumps(snapshot, ensure_ascii=False), now),
    )
    connection.execute(
        "UPDATE scan_jobs SET source_invalidated_at = COALESCE(source_invalidated_at, ?) WHERE media_source_id = ?",
        (now, source_id),
    )
    summary["pending_files_invalidated"] = connection.execute(
        "UPDATE pending_files SET status = 'invalidated' WHERE media_source_id = ? AND status = 'pending'", (source_id,),
    ).rowcount
    previewIds = "SELECT rp.id FROM rename_previews rp JOIN media_files mf ON mf.id = rp.media_file_id WHERE mf.media_source_id = ?"
    summary["rename_plans_invalidated"] = connection.execute(
        "UPDATE rename_operations SET status = 'invalidated', ready_count = 0, updated_at = ? WHERE status IN ('dry_run', 'draft', 'checked', 'interrupted') "
        f"AND id IN (SELECT operation_id FROM rename_operation_items WHERE rename_preview_id IN ({previewIds}))",
        (now, source_id),
    ).rowcount
    connection.execute(
        "UPDATE rename_operation_items SET status = 'invalidated', message = '媒体源已变更或退役，旧计划不可执行', updated_at = ? "
        f"WHERE status IN ('pending', 'ready', 'conflict') AND (rename_preview_id IN ({previewIds}) "
        "OR operation_id IN (SELECT operation_id FROM rename_operation_items "
        f"WHERE rename_preview_id IN ({previewIds})))", (now, source_id, source_id),
    )
    connection.execute(
        "UPDATE rename_rollback_plans SET status = 'invalidated', executable_count = 0, updated_at = ? WHERE status IN ('draft', 'checked', 'interrupted') "
        "AND id IN (SELECT plan_id FROM rename_rollback_items WHERE operation_item_id IN "
        f"(SELECT id FROM rename_operation_items WHERE rename_preview_id IN ({previewIds})))", (now, source_id),
    )
    connection.execute(
        "UPDATE rename_rollback_items SET status = 'invalidated', message = '媒体源已变更或退役，旧计划不可执行', updated_at = ? "
        "WHERE status IN ('pending', 'ready', 'conflict') AND plan_id IN (SELECT plan_id FROM rename_rollback_items "
        "WHERE operation_item_id IN (SELECT id FROM rename_operation_items "
        f"WHERE rename_preview_id IN ({previewIds})))", (now, source_id),
    )
    connection.execute(
        "UPDATE rename_previews SET status = 'invalidated', message = '媒体源已变更或退役，请重新扫描', updated_at = ? "
        f"WHERE status NOT IN ('renamed', 'rolled_back') AND id IN ({previewIds})", (now, source_id),
    )
    summary["scan_index_removed"] = connection.execute(
        "DELETE FROM scan_file_index WHERE media_source_id = ?", (source_id,),
    ).rowcount
    return summary


def _retireMediaSource(connection: sqlite3.Connection, sourceId: int) -> dict[str, int]:
    summary = _invalidateSourceHistory(connection, sourceId, "retired")
    # 保留源关联和原根快照，以系统保留键释放旧 UNIQUE 路径，不复用历史源 ID。
    connection.execute(
        "UPDATE media_sources SET retired_root = ?, path = ?, deleted_at = ?, enabled = 0, "
        "username = NULL, encrypted_secret = NULL, protocol_endpoint = NULL, auth_type = 'none', updated_at = ? WHERE id = ?",
        (_fetch_media_source(connection, sourceId).path, f"retired://{sourceId}", _utc_now(), _utc_now(), sourceId),
    )
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


def _runtime_environment() -> str:
    configured = os.environ.get("MEDIAAI_RUNTIME", "").strip().lower()
    if configured in {"container", "windows", "linux", "macos"}:
        return configured
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _runtime_root_label(runtime_environment: str) -> str:
    return {
        "container": "容器媒体目录",
        "windows": "此电脑",
        "linux": "Linux 文件系统",
        "macos": "macOS 文件系统",
    }.get(runtime_environment, "文件系统")


def _default_local_directory(runtime_environment: str) -> Path | None:
    configured_root = os.environ.get("MEDIAAI_MEDIA_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    if runtime_environment == "container":
        media_root = Path("/app/media")
        return media_root if media_root.is_dir() else Path("/")
    if runtime_environment in {"linux", "macos"}:
        return Path("/")
    return None


def list_local_directories(path: str | None = None) -> LocalDirectoryListing:
    runtime_environment = _runtime_environment()
    root_label = _runtime_root_label(runtime_environment)
    default_directory = _default_local_directory(runtime_environment)
    requested_path = path.strip() if path is not None else ""

    if not requested_path and default_directory is None:
        return LocalDirectoryListing(
            current_path=None,
            parent_path=None,
            entries=_windows_drive_entries(),
            runtime_environment=runtime_environment,
            root_label=root_label,
        )

    current_path = Path(requested_path).expanduser().resolve() if requested_path else default_directory
    if current_path is None:
        raise ValueError("无法确定可浏览目录")
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

    is_default_root = not requested_path and default_directory == current_path
    parent_path = (
        str(current_path.parent)
        if not is_default_root and current_path.parent != current_path
        else None
    )
    return LocalDirectoryListing(
        current_path=str(current_path),
        parent_path=parent_path,
        entries=entries,
        runtime_environment=runtime_environment,
        root_label=root_label,
    )


def list_source_directories(
    settings: AppSettings,
    source_id: int,
    path: str | None = None,
) -> LocalDirectoryListing:
    source = get_media_source(settings, source_id)
    protocol = get_protocol(source.path_type)
    effective_settings = get_effective_settings(settings)
    listing = protocol.list_directories(path or source.path, _media_source_context_with_secret(settings, source))
    browse_limit = int(effective_settings.get("shared.directory_browse_limit") or 500)
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
    result = protocol.test_connection(source.path, _media_source_context_with_secret(settings, source))
    status = "成功" if result.success else "失败"
    get_batch_logger().info(
        "媒体源连接测试%s source_id=%s path_type=%s path=%s message=%s suggestion=%s",
        status,
        source.id,
        source.path_type,
        redactUrlCredentials(source.path),
        result.message,
        result.suggestion or "",
    )
    return result


def verifyWebDavConditionalMove(
    settings: AppSettings,
    sourceId: int,
    auditContext: AuditWriteContext | None = None,
):
    """对已保存媒体源执行隔离写入验证，并原子保存能力快照与审计。"""

    source = get_media_source(settings, sourceId)
    if source.path_type != "webdav":
        raise ValueError("仅 WebDAV 媒体源支持条件 MOVE 安全验证")
    lockKey = f"media-source:{sourceId}:write"
    lock = acquire_remote_operation_lock(
        settings,
        media_source_id=sourceId,
        lock_key=lockKey,
        owner="capability-verification",
        task_type="webdav_capability_verification",
        task_id=sourceId,
        ttl_seconds=300,
    )
    try:
        protocol = get_protocol("webdav")
        verifier = getattr(protocol, "verify_conditional_move", None)
        if verifier is None:
            raise RuntimeError("WebDAV 条件 MOVE 验证器不可用")
        result = verifier(source.path, get_media_source_protocol_context(settings, sourceId))
        now = _utc_now()
        snapshot = {
            "conditional_move": {
                "status": result.status,
                "endpoint": source.path,
                "credential_version": source.credential_version,
                "verified_at": now,
                "cleanup_succeeded": result.cleanup_succeeded,
                "evidence": result.evidence or {},
            }
        }
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            current = _fetch_media_source(connection, sourceId)
            if current.path != source.path or current.credential_version != source.credential_version:
                raise ValueError("验证期间媒体源配置已变化，结果未保存")
            activeLock = connection.execute(
                "SELECT 1 FROM remote_operation_locks WHERE lock_key=? AND lease_token=? AND status='active' "
                "AND julianday(expires_at)>julianday('now')",
                (lockKey, lock.lease_token),
            ).fetchone()
            if activeLock is None:
                raise ValueError("验证租约已失效，结果未保存")
            connection.execute(
                "UPDATE media_sources SET capability_snapshot_json=?, updated_at=? WHERE id=?",
                (json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), now, sourceId),
            )
            _recordSourceAudit(
                settings,
                connection,
                auditContext,
                "verify_webdav_conditional_move",
                sourceId,
                {
                    "status": result.status,
                    "cleanup_succeeded": result.cleanup_succeeded,
                    "evidence": result.evidence or {},
                },
            )
            connection.commit()
        return result
    finally:
        if not release_remote_operation_lock(settings, lockKey, lock.lease_token):
            get_batch_logger().warning("WebDAV能力验证写锁释放失败 source_id=%s", sourceId)


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
    elif source_path_type == "webdav":
        source_path = _normalize_remote_protocol_path(source_path_type, path)
    else:
        source_path = _normalize_media_source_path(path)
    protocol = get_protocol(source_path_type)
    context = SharedPathContext(
        path_type=source_path_type,
        username=username if source_path_type in {"unc", "webdav"} else None,
        secret=secret if source_path_type in {"unc", "webdav"} else None,
        has_secret=bool(secret) if source_path_type in {"unc", "webdav"} else False,
        host=host,
        share_name=share_name,
        domain=domain if source_path_type == "unc" else None,
        port=port,
        nfs_host=nfs_host if source_path_type == "mounted_nfs" else None,
        nfs_export=nfs_export if source_path_type == "mounted_nfs" else None,
        nfs_version=nfs_version if source_path_type == "mounted_nfs" else None,
        nfs_options=nfs_options if source_path_type == "mounted_nfs" else None,
        local_mount_path=local_mount_path if source_path_type == "mounted_nfs" else None,
        root_url=str(source_path) if source_path_type == "webdav" else None,
    )
    result = protocol.test_connection(str(source_path), context)
    status = "成功" if result.success else "失败"
    get_batch_logger().info(
        "媒体源临时连接测试%s path_type=%s path=%s message=%s suggestion=%s",
        status,
        source_path_type,
        redactUrlCredentials(str(source_path)),
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
    auditContext: AuditWriteContext | None = None,
) -> MediaSource:
    source_name = _validate_media_source_name(name)
    source_path_type = _normalize_path_type(path_type)
    port = _validate_port(port)
    if source_path_type == "unc":
        source_path = _validate_unc_path(str(path))
        encrypted_secret = encrypt_secret(settings, secret)
        auth_type = "basic" if username or secret else "none"
        credential_version = CURRENT_CREDENTIAL_VERSION if encrypted_secret else 1
    elif source_path_type == "webdav":
        source_path = _normalize_remote_protocol_path(source_path_type, path)
        domain = None
        host = host or None
        share_name = None
        encrypted_secret = encrypt_secret(settings, secret)
        auth_type = "basic" if username else ("bearer" if secret else "none")
        credential_version = CURRENT_CREDENTIAL_VERSION if encrypted_secret else 1
    else:
        source_path = _normalize_media_source_path(path)
        username = None
        domain = None
        encrypted_secret = None
        auth_type = "none"
        credential_version = 1
    protocol = _protocol_for_path_type(source_path_type)
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(
                "INSERT INTO media_sources "
                "(name, path, path_type, protocol, host, share_name, domain, username, "
                "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, "
                "nfs_options, local_mount_path, protocol_endpoint, auth_type, credential_version, "
                "remote_root, capability_snapshot_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    str(source_path) if source_path_type == "webdav" else None,
                    auth_type,
                    credential_version,
                    "/" if source_path_type == "webdav" else None,
                    None,
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
            "local_mount_path, protocol_endpoint, auth_type, credential_version, remote_root, "
            "capability_snapshot_json, trusted_certificate_fingerprint, enabled, created_at, updated_at "
            "FROM media_sources WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        _recordSourceAudit(settings, connection, auditContext, "create_media_source", int(row["id"]),
                           {"name": source_name, "path": str(source_path), "path_type": source_path_type, "enabled": enabled})
        connection.commit()
    return _row_to_media_source(row)


def list_media_sources(settings: AppSettings) -> list[MediaSource]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, path, path_type, protocol, host, share_name, domain, username, "
            "encrypted_secret, port, remark, nfs_host, nfs_export, nfs_version, nfs_options, "
            "local_mount_path, protocol_endpoint, auth_type, credential_version, remote_root, "
            "capability_snapshot_json, trusted_certificate_fingerprint, enabled, created_at, updated_at "
            "FROM media_sources WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
    return [_row_to_media_source(row) for row in rows]


def listMediaSourcesPage(settings: AppSettings, *, page: int = 1, pageSize: int = 50,
                         query: str = "", enabledOnly: bool = False) -> dict:
    if page < 1 or not 1 <= pageSize <= 200:
        raise ValueError("分页参数超出允许范围")
    if len(query) > 200:
        raise ValueError("查询文字最多200个字符")
    clauses, values = ["deleted_at IS NULL"], []
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("name LIKE ? ESCAPE '\\'")
        values.append("%" + escaped + "%")
    if enabledOnly:
        clauses.append("enabled=1")
    where = " WHERE " + " AND ".join(clauses)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        total = int(connection.execute("SELECT COUNT(*) FROM media_sources" + where, values).fetchone()[0])
        actualPage = min(page, max(1, (total + pageSize - 1) // pageSize))
        rows = connection.execute("SELECT * FROM media_sources" + where + " ORDER BY id LIMIT ? OFFSET ?",
                                  [*values, pageSize, (actualPage - 1) * pageSize]).fetchall()
    return {"items": [_row_to_media_source(row) for row in rows], "total": total,
            "page": actualPage, "page_size": pageSize}


def get_media_source(settings: AppSettings, source_id: int) -> MediaSource:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _fetch_media_source(connection, source_id)


def _assertMediaSourceIdle(connection: sqlite3.Connection, sourceId: int) -> None:
    # 检查与配置写入保持同一事务，不能在检查后让执行认领插入。
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    checks = (
        "SELECT 1 FROM scan_jobs WHERE media_source_id = ? AND status IN ('pending', 'queued', 'running', 'scanning', 'cancelling') LIMIT 1",
        "SELECT 1 FROM rename_operations operation JOIN rename_operation_items item ON item.operation_id = operation.id "
         "JOIN rename_previews preview ON preview.id = item.rename_preview_id JOIN media_files file ON file.id = preview.media_file_id "
         "WHERE file.media_source_id = ? AND (operation.status IN ('queued','running') OR operation.execution_token IS NOT NULL) LIMIT 1",
        "SELECT 1 FROM rename_rollback_plans plan JOIN rename_rollback_items rollback ON rollback.plan_id = plan.id "
         "JOIN rename_operation_items item ON item.id = rollback.operation_item_id "
         "JOIN rename_previews preview ON preview.id = item.rename_preview_id JOIN media_files file ON file.id = preview.media_file_id "
         "WHERE file.media_source_id = ? AND (plan.status IN ('queued','running') OR plan.execution_token IS NOT NULL) LIMIT 1",
        "SELECT 1 FROM remote_operation_items WHERE media_source_id = ? AND status <> 'completed' LIMIT 1",
        "SELECT 1 FROM pending_move_tasks task JOIN pending_move_items item ON item.task_id=task.id "
         "WHERE item.media_source_id=? AND (task.status IN ('queued','running') OR task.execution_token IS NOT NULL) LIMIT 1",
        "SELECT 1 FROM local_file_actions WHERE media_source_id = ? AND status IN ('prepared', 'applied', 'recovery_required') LIMIT 1",
        "SELECT 1 FROM remote_operation_locks WHERE media_source_id = ? AND status = 'active' "
         "AND (julianday(expires_at) IS NULL OR julianday(expires_at) > julianday('now')) LIMIT 1",
    )
    for query in checks:
        if connection.execute(query, (sourceId,)).fetchone():
            raise ValueError("媒体源存在运行中任务或未完成恢复，请先完成任务和恢复后再修改或删除")


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
    auditContext: AuditWriteContext | None = None,
) -> dict[str, object]:
    source_name = _validate_media_source_name(name)
    cleanup_summary = _empty_cleanup_summary()
    now = _utc_now()

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _assertMediaSourceIdle(connection, source_id)
        current = _fetch_media_source(connection, source_id)
        if current.path_type == "unc":
            source_path = _validate_unc_path(str(path))
        elif current.path_type == "webdav":
            source_path = _normalize_remote_protocol_path(current.path_type, path)
        else:
            source_path = _normalize_media_source_path(path)
        path_changed = str(source_path) != current.path
        credentials_changed = (
            current.path_type == "webdav"
            and (username != current.username or bool(secret))
        )
        if path_changed and not clear_history_on_path_change:
            raise ValueError("修改路径将使旧执行计划和待处理记录失效，历史记录保留，请确认后重试")
        if path_changed:
            cleanup_summary = _invalidateSourceHistory(connection, source_id, "root_changed")
        update_username = username if current.path_type in {"unc", "webdav"} else None
        update_secret = encrypt_secret(settings, secret) if current.path_type in {"unc", "webdav"} and secret else None
        if current.path_type in {"unc", "webdav"}:
            update_auth_type = (
                "basic"
                if update_username or update_secret or current.has_secret
                else "none"
            )
        else:
            update_auth_type = "none"
        update_nfs_host = nfs_host if current.path_type == "mounted_nfs" else None
        update_nfs_export = nfs_export if current.path_type == "mounted_nfs" else None
        update_protocol_endpoint = str(source_path) if current.path_type == "webdav" else current.protocol_endpoint
        update_remote_root = "/" if current.path_type == "webdav" else current.remote_root
        try:
            connection.execute(
                "UPDATE media_sources SET name = ?, path = ?, username = ?, "
                "encrypted_secret = CASE WHEN ? IS NULL THEN encrypted_secret ELSE ? END, "
                "credential_version = CASE WHEN ? IS NULL THEN credential_version ELSE ? END, "
                "auth_type = ?, "
                "nfs_host = ?, nfs_export = ?, protocol_endpoint = ?, remote_root = ?, "
                "capability_snapshot_json = CASE WHEN ? THEN NULL ELSE capability_snapshot_json END, "
                "enabled = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    source_name,
                    str(source_path),
                    update_username,
                    update_secret,
                    update_secret,
                    update_secret,
                    CURRENT_CREDENTIAL_VERSION,
                    update_auth_type,
                    update_nfs_host,
                    update_nfs_export,
                    update_protocol_endpoint,
                    update_remote_root,
                    int(path_changed or credentials_changed),
                    int(enabled),
                    now,
                    source_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("媒体源路径已存在") from exc
        source = _fetch_media_source(connection, source_id)
        _recordSourceAudit(settings, connection, auditContext, "update_media_source", source_id,
                           {"name": source_name, "path": source.path, "enabled": enabled,
                            "path_changed": path_changed, "cleanup_summary": cleanup_summary})
        connection.commit()

    return {"source": source, "cleanup_summary": cleanup_summary}


def set_media_source_enabled(
    settings: AppSettings,
    source_id: int,
    enabled: bool,
    auditContext: AuditWriteContext | None = None,
) -> MediaSource:
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _assertMediaSourceIdle(connection, source_id)
        _fetch_media_source(connection, source_id)
        connection.execute(
            "UPDATE media_sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), now, source_id),
        )
        source = _fetch_media_source(connection, source_id)
        _recordSourceAudit(settings, connection, auditContext, "set_media_source_enabled", source_id, {"enabled": enabled})
        connection.commit()
    return source


def delete_media_source(settings: AppSettings, source_id: int, auditContext: AuditWriteContext | None = None) -> dict[str, object]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _assertMediaSourceIdle(connection, source_id)
        _fetch_media_source(connection, source_id)
        cleanup_summary = _retireMediaSource(connection, source_id)
        _recordSourceAudit(settings, connection, auditContext, "delete_media_source", source_id, {"cleanup_summary": cleanup_summary})
        connection.commit()
    return {"deleted_ids": [source_id], "cleanup_summary": cleanup_summary}


def bulk_delete_media_sources(
    settings: AppSettings,
    source_ids: list[int],
    auditContext: AuditWriteContext | None = None,
) -> dict[str, object]:
    normalized_ids = list(dict.fromkeys(source_ids))
    if not normalized_ids:
        raise ValueError("请选择要删除的媒体源")

    cleanup_summary = _empty_cleanup_summary()
    deleted_ids: list[int] = []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for source_id in normalized_ids:
            _assertMediaSourceIdle(connection, source_id)
            _fetch_media_source(connection, source_id)
            _merge_cleanup_summary(
                cleanup_summary,
                _retireMediaSource(connection, source_id),
            )
            deleted_ids.append(source_id)
        _recordSourceAudit(settings, connection, auditContext, "bulk_delete_media_sources", None,
                           {"ids": deleted_ids, "cleanup_summary": cleanup_summary})
        connection.commit()
    return {"deleted_ids": deleted_ids, "cleanup_summary": cleanup_summary}
