"""远程文件操作失败恢复服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3
from typing import Any

from app.core.config import AppSettings
from app.service.media_source_service import get_media_source_protocol_context
from app.service.remote_operation_service import (
    RemoteOperationItem,
    acquire_remote_operation_lock,
    get_remote_operation_item,
    release_remote_operation_lock,
    update_remote_operation_item_status,
)
from app.service.rename_operation_service import _update_successful_remote_media_record
from app.service.rename_rollback_service import _update_remote_media_record_after_rollback
from app.service.shared_protocols.registry import get_protocol

SUPPORTED_OPERATION_TYPES = {"rename", "rollback"}
RECOVERABLE_STATUSES = {"pending", "failed", "recovering", "recovery_required"}


class RemoteOperationRecoveryConflict(RuntimeError):
    """远端文件状态不明确，必须人工处理。"""


@dataclass(frozen=True)
class RemoteOperationRecoveryResult:
    """远程操作恢复结果。"""

    item: RemoteOperationItem
    action: str
    message: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recovery_dict(item: RemoteOperationItem) -> dict[str, Any]:
    return dict(item.recovery) if isinstance(item.recovery, dict) else {}


def _int_value(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rename_identifiers(item: RemoteOperationItem) -> tuple[int, int, int]:
    recovery = _recovery_dict(item)
    operation_id = _int_value(recovery.get("operation_id"))
    operation_item_id = _int_value(recovery.get("operation_item_id"))
    rename_preview_id = _int_value(recovery.get("rename_preview_id"))
    key_match = re.fullmatch(r"rename-operation:(\d+):item:(\d+)", item.idempotency_key)
    if key_match:
        operation_id = operation_id or int(key_match.group(1))
        operation_item_id = operation_item_id or int(key_match.group(2))
    if operation_id is None or operation_item_id is None or rename_preview_id is None:
        raise ValueError("远程重命名恢复信息不完整")
    return operation_id, operation_item_id, rename_preview_id


def _rollback_identifiers(item: RemoteOperationItem) -> tuple[int, int, int]:
    recovery = _recovery_dict(item)
    rollback_plan_id = _int_value(recovery.get("rollback_plan_id"))
    rollback_item_id = _int_value(recovery.get("rollback_item_id"))
    operation_item_id = _int_value(recovery.get("operation_item_id"))
    key_match = re.fullmatch(r"rollback-plan:(\d+):item:(\d+)", item.idempotency_key)
    if key_match:
        rollback_plan_id = rollback_plan_id or int(key_match.group(1))
        rollback_item_id = rollback_item_id or int(key_match.group(2))
    if rollback_plan_id is None or rollback_item_id is None or operation_item_id is None:
        raise ValueError("远程回滚恢复信息不完整")
    return rollback_plan_id, rollback_item_id, operation_item_id


def _sync_rename_records(
    connection: sqlite3.Connection,
    item: RemoteOperationItem,
    updated_at: str,
) -> dict[str, int]:
    operation_id, operation_item_id, rename_preview_id = _rename_identifiers(item)
    operation_item = connection.execute(
        "SELECT source_path, target_path FROM rename_operation_items "
        "WHERE id = ? AND operation_id = ? AND rename_preview_id = ?",
        (operation_item_id, operation_id, rename_preview_id),
    ).fetchone()
    if operation_item is None:
        raise ValueError("关联的重命名条目不存在")
    if str(operation_item["source_path"]) != item.source_path or str(operation_item["target_path"]) != item.target_path:
        raise ValueError("远程操作路径与重命名条目不一致")

    _update_successful_remote_media_record(
        connection,
        rename_preview_id,
        str(item.target_path),
        updated_at,
    )
    connection.execute(
        "UPDATE rename_operation_items SET status = 'renamed', message = NULL, updated_at = ? WHERE id = ?",
        (updated_at, operation_item_id),
    )
    counts = {
        str(row["status"]): int(row["item_count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) AS item_count FROM rename_operation_items "
            "WHERE operation_id = ? GROUP BY status",
            (operation_id,),
        ).fetchall()
    }
    renamed_count = counts.get("renamed", 0)
    failed_count = counts.get("failed", 0)
    ready_count = counts.get("ready", 0)
    conflict_count = counts.get("conflict", 0)
    final_status = "completed"
    if failed_count:
        final_status = "partial_failed" if renamed_count else "failed"
    connection.execute(
        "UPDATE rename_operations SET status = ?, ready_count = ?, conflict_count = ?, "
        "renamed_count = ?, failed_count = ?, updated_at = ? WHERE id = ?",
        (
            final_status,
            ready_count,
            conflict_count,
            renamed_count,
            failed_count,
            updated_at,
            operation_id,
        ),
    )
    return {
        "operation_id": operation_id,
        "operation_item_id": operation_item_id,
        "rename_preview_id": rename_preview_id,
    }


def _sync_rollback_records(
    connection: sqlite3.Connection,
    item: RemoteOperationItem,
    updated_at: str,
) -> dict[str, int]:
    rollback_plan_id, rollback_item_id, operation_item_id = _rollback_identifiers(item)
    rollback_item = connection.execute(
        "SELECT current_path, rollback_path FROM rename_rollback_items "
        "WHERE id = ? AND plan_id = ? AND operation_item_id = ?",
        (rollback_item_id, rollback_plan_id, operation_item_id),
    ).fetchone()
    if rollback_item is None:
        raise ValueError("关联的回滚条目不存在")
    if str(rollback_item["current_path"]) != item.source_path or str(rollback_item["rollback_path"]) != item.target_path:
        raise ValueError("远程操作路径与回滚条目不一致")

    _update_remote_media_record_after_rollback(
        connection,
        operation_item_id,
        item.source_path,
        str(item.target_path),
        updated_at,
    )
    connection.execute(
        "UPDATE rename_rollback_items SET status = 'rolled_back', message = NULL, "
        "executed_at = ?, updated_at = ? WHERE id = ?",
        (updated_at, updated_at, rollback_item_id),
    )
    counts = {
        str(row["status"]): int(row["item_count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) AS item_count FROM rename_rollback_items "
            "WHERE plan_id = ? GROUP BY status",
            (rollback_plan_id,),
        ).fetchall()
    }
    rolled_back_count = counts.get("rolled_back", 0)
    failed_count = counts.get("failed", 0)
    final_status = "executed"
    if failed_count:
        final_status = "partial_failed" if rolled_back_count else "failed"
    connection.execute(
        "UPDATE rename_rollback_plans SET status = ?, executable_count = ?, conflict_count = ?, "
        "updated_at = ? WHERE id = ?",
        (final_status, rolled_back_count, failed_count, updated_at, rollback_plan_id),
    )
    return {
        "rollback_plan_id": rollback_plan_id,
        "rollback_item_id": rollback_item_id,
        "operation_item_id": operation_item_id,
    }


def _sync_business_records(settings: AppSettings, item: RemoteOperationItem) -> dict[str, int]:
    updated_at = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        if item.operation_type == "rename":
            recovery = _sync_rename_records(connection, item, updated_at)
        else:
            recovery = _sync_rollback_records(connection, item, updated_at)
        connection.commit()
    return recovery


def recover_remote_operation(
    settings: AppSettings,
    item_id: int,
    *,
    owner: str | None = None,
) -> RemoteOperationRecoveryResult:
    """恢复单个 WebDAV 远程重命名或回滚操作。"""

    item = get_remote_operation_item(settings, item_id)
    if item.status == "completed":
        return RemoteOperationRecoveryResult(item, "already_completed", "远程操作已完成，无需重复恢复")
    if item.operation_type not in SUPPORTED_OPERATION_TYPES:
        raise ValueError("当前远程操作类型不支持恢复")
    if item.status not in RECOVERABLE_STATUSES:
        raise ValueError(f"当前远程操作状态不允许恢复：{item.status}")
    if not item.target_path:
        raise ValueError("远程操作目标路径为空，无法恢复")

    with closing(sqlite3.connect(settings.database_path)) as connection:
        source_row = connection.execute(
            "SELECT path_type FROM media_sources WHERE id = ?",
            (item.media_source_id,),
        ).fetchone()
    if source_row is None:
        raise ValueError("远程媒体源不存在")
    path_type = str(source_row[0])
    if path_type != "webdav":
        raise ValueError("当前仅支持 WebDAV 远程操作恢复")

    lock_key = f"media-source:{item.media_source_id}:write"
    lock = acquire_remote_operation_lock(
        settings,
        media_source_id=item.media_source_id,
        lock_key=lock_key,
        owner=owner or "system",
        task_type="remote_operation_recovery",
        task_id=item.id,
        ttl_seconds=300,
    )
    try:
        recovery = _recovery_dict(item)
        recovery.update({"recovery_started_at": _utc_now(), "recovery_owner": owner or "system"})
        item = update_remote_operation_item_status(
            settings,
            item.id,
            "recovering",
            error_message=None,
            recovery=recovery,
        )
        context = get_media_source_protocol_context(settings, item.media_source_id)
        protocol = get_protocol(path_type)
        forward_readiness = protocol.check_rename_ready(item.source_path, item.target_path, context)
        action = "retried"
        if forward_readiness.success:
            move_result = protocol.move_file(item.source_path, item.target_path, context)
            if not move_result.success:
                update_remote_operation_item_status(
                    settings,
                    item.id,
                    "failed",
                    error_message=move_result.message,
                    recovery=recovery,
                )
                raise ValueError(move_result.message)
        else:
            reverse_readiness = protocol.check_rename_ready(item.target_path, item.source_path, context)
            if not reverse_readiness.success:
                conflict_message = (
                    "无法确认远端文件状态，已停止自动恢复："
                    f"正向校验 {forward_readiness.message}；反向校验 {reverse_readiness.message}"
                )
                update_remote_operation_item_status(
                    settings,
                    item.id,
                    "recovery_required",
                    error_message=conflict_message,
                    recovery=recovery,
                )
                raise RemoteOperationRecoveryConflict(conflict_message)
            action = "reconciled"

        business_recovery = _sync_business_records(settings, item)
        recovery.update(business_recovery)
        recovery.update({"recovery_action": action, "recovered_at": _utc_now()})
        completed_item = update_remote_operation_item_status(
            settings,
            item.id,
            "completed",
            error_message=None,
            recovery=recovery,
        )
        message = "远程 MOVE 已安全重试并完成恢复" if action == "retried" else "远端文件已移动，数据库状态已补齐"
        return RemoteOperationRecoveryResult(completed_item, action, message)
    except RemoteOperationRecoveryConflict:
        raise
    except Exception as exc:
        current_item = get_remote_operation_item(settings, item.id)
        if current_item.status == "recovering":
            update_remote_operation_item_status(
                settings,
                item.id,
                "failed",
                error_message=str(exc),
                recovery=_recovery_dict(current_item),
            )
        raise
    finally:
        release_remote_operation_lock(settings, lock_key, lock.lease_token)
