"""远程文件操作失败恢复服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from dataclasses import replace
import json
import re
import sqlite3
from threading import Event, Thread
from typing import Any

from app.core.config import AppSettings
from app.service.media_source_service import get_media_source_protocol_context
from app.service.remote_operation_service import (
    RemoteOperationItem,
    RemoteOperationLockError,
    claim_remote_operation_recovery,
    get_remote_operation_item,
    heartbeat_remote_operation_recovery,
    release_remote_operation_lock,
    update_remote_operation_recovery_state,
)
from app.service.rename_operation_service import _update_successful_remote_media_record
from app.service.rename_rollback_service import _update_remote_media_record_after_rollback
from app.service.shared_protocols.registry import get_protocol
from app.service.shared_protocols.webdav import isStrongEtag
from app.service.shared_protocols.base import RemoteFileEntry
from app.service.remote_file_index_service import syncRemoteFileIndex
from app.service.scan_service import _remote_normalized_index_path

SUPPORTED_OPERATION_TYPES = {"rename", "rollback"}
RECOVERABLE_STATUSES = {"pending", "failed", "recovering", "recovery_required", "remote_applied"}
RECOVERY_LEASE_TTL_SECONDS = 300
RECOVERY_HEARTBEAT_INTERVAL_SECONDS = 30.0


class RemoteOperationRecoveryConflict(RuntimeError):
    """远端文件状态不明确，必须人工处理。"""


@dataclass(frozen=True)
class RemoteOperationRecoveryResult:
    """远程操作恢复结果。"""

    item: RemoteOperationItem
    action: str
    message: str


class _RecoveryHeartbeat:
    """在阻塞式远端请求期间持续刷新恢复租约。"""

    def __init__(
        self,
        settings: AppSettings,
        item: RemoteOperationItem,
        recoveryToken: str,
        lockKey: str,
    ) -> None:
        self.settings = settings
        self.itemId = item.id
        self.generation = item.recovery_generation
        self.recoveryToken = recoveryToken
        self.lockKey = lockKey
        self.stopEvent = Event()
        self.failure: BaseException | None = None
        self.thread = Thread(target=self._run, name=f"remote-recovery-heartbeat-{item.id}", daemon=True)

    def _run(self) -> None:
        while not self.stopEvent.wait(RECOVERY_HEARTBEAT_INTERVAL_SECONDS):
            try:
                heartbeat_remote_operation_recovery(
                    self.settings,
                    self.itemId,
                    self.generation,
                    self.recoveryToken,
                    self.lockKey,
                    ttl_seconds=RECOVERY_LEASE_TTL_SECONDS,
                )
            except BaseException as exc:  # pragma: no cover - 由主线程在边界处重新抛出
                self.failure = exc
                self.stopEvent.set()

    def __enter__(self) -> _RecoveryHeartbeat:
        self.thread.start()
        return self

    def ensureActive(self) -> None:
        if self.failure is not None:
            raise RemoteOperationLockError("远程操作恢复心跳失败，已停止后续写入") from self.failure

    def __exit__(self, excType, excValue, traceback) -> None:
        self.stopEvent.set()
        self.thread.join(timeout=max(1.0, RECOVERY_HEARTBEAT_INTERVAL_SECONDS + 1.0))
        if excType is None:
            self.ensureActive()


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
    final_status = "interrupted" if ready_count else "completed"
    if failed_count and not ready_count:
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
    final_status = "interrupted" if counts.get("ready", 0) else "executed"
    if failed_count and not counts.get("ready", 0):
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


def _sync_business_records(
    settings: AppSettings,
    item: RemoteOperationItem,
    targetInfo: RemoteFileEntry,
    recoveryToken: str,
    recovery: dict[str, Any],
) -> dict[str, int]:
    """在同一事务内同步业务记录并以恢复令牌完成远程操作。"""

    updated_at = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        if item.operation_type == "rename":
            businessRecovery = _sync_rename_records(connection, item, updated_at)
        else:
            businessRecovery = _sync_rollback_records(connection, item, updated_at)
        previewRow = connection.execute(
            "SELECT rename_preview_id FROM rename_operation_items WHERE id = ?",
            (businessRecovery["operation_item_id"],),
        ).fetchone()
        if previewRow is None:
            raise ValueError("恢复关联的预览不存在")
        syncRemoteFileIndex(
            connection, int(previewRow[0]), item.source_path, targetInfo,
            "renamed" if item.operation_type == "rename" else "rolled_back", updated_at,
        )
        completedRecovery = dict(recovery)
        completedRecovery.update(businessRecovery)
        completedRecovery["recovered_at"] = updated_at
        cursor = connection.execute(
            "UPDATE remote_operation_items SET status = 'completed', error_message = NULL, "
            "target_version = ?, recovery_json = ?, recovery_phase = 'completed', "
            "recovery_heartbeat_at = ?, recovery_token = NULL, updated_at = ? "
            "WHERE id = ? AND recovery_generation = ? AND recovery_token = ? "
            "AND recovery_phase IN ('remote_applied', 'reconciling')",
            (
                targetInfo.version,
                json.dumps(completedRecovery, ensure_ascii=False, separators=(",", ":")),
                updated_at,
                updated_at,
                item.id,
                item.recovery_generation,
                recoveryToken,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RemoteOperationLockError("远程操作恢复租约已失效，业务状态未写入")
        connection.commit()
    return businessRecovery


def _assertCompletedBusinessConsistency(settings: AppSettings, item: RemoteOperationItem) -> None:
    """已完成历史只读核对，不重新移动文件或覆盖后续业务状态。"""
    with closing(sqlite3.connect(f"{settings.database_path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if item.operation_type == "rename":
            operationId, operationItemId, previewId = _rename_identifiers(item)
            record = connection.execute(
                "SELECT status, source_path, target_path FROM rename_operation_items "
                "WHERE id = ? AND operation_id = ? AND rename_preview_id = ?",
                (operationItemId, operationId, previewId),
            ).fetchone()
            expectedStatus = "renamed"
        elif item.operation_type == "rollback":
            planId, rollbackItemId, operationItemId = _rollback_identifiers(item)
            record = connection.execute(
                "SELECT status, current_path AS source_path, rollback_path AS target_path FROM rename_rollback_items "
                "WHERE id = ? AND plan_id = ? AND operation_item_id = ?",
                (rollbackItemId, planId, operationItemId),
            ).fetchone()
            preview = connection.execute(
                "SELECT rename_preview_id FROM rename_operation_items WHERE id = ?", (operationItemId,),
            ).fetchone()
            previewId = int(preview[0]) if preview else None
            expectedStatus = "rolled_back"
        else:
            raise ValueError("当前远程操作类型不支持核对")
        media = connection.execute(
            "SELECT rp.status, mf.file_path, mf.media_source_id, ms.path FROM rename_previews rp "
            "JOIN media_files mf ON mf.id = rp.media_file_id JOIN media_sources ms ON ms.id = mf.media_source_id "
            "WHERE rp.id = ?", (previewId,),
        ).fetchone()
        if (record is None or record["status"] != expectedStatus
                or record["source_path"] != item.source_path or record["target_path"] != item.target_path
                or media is None or media["status"] != expectedStatus
                or media["file_path"] != item.target_path or media["media_source_id"] != item.media_source_id):
            raise RemoteOperationRecoveryConflict("已完成历史与当前业务记录不一致或已发生后续操作；保留历史，不自动重放，请人工核对")
        index = connection.execute(
            "SELECT status, rename_preview_id FROM scan_file_index WHERE media_source_id = ? AND normalized_path = ?",
            (item.media_source_id, _remote_normalized_index_path(str(item.target_path), str(media["path"]))),
        ).fetchone()
        if index is None or index["status"] != "active" or index["rename_preview_id"] != previewId:
            raise RemoteOperationRecoveryConflict("已完成历史的目标索引缺失或不一致；保留历史，不自动重放，请人工核对")


def recover_remote_operation(
    settings: AppSettings,
    item_id: int,
    *,
    owner: str | None = None,
) -> RemoteOperationRecoveryResult:
    """恢复单个 WebDAV 远程重命名或回滚操作。"""

    item = get_remote_operation_item(settings, item_id)
    if item.status == "completed":
        _assertCompletedBusinessConsistency(settings, item)
        return RemoteOperationRecoveryResult(item, "already_completed", "远程操作已完成，当前业务记录与索引核对一致；未重新移动文件")
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
    item, lock = claim_remote_operation_recovery(
        settings,
        item.id,
        lock_key=lock_key,
        owner=owner or "system",
        allowed_statuses=RECOVERABLE_STATUSES,
        ttl_seconds=RECOVERY_LEASE_TTL_SECONDS,
    )
    recoveryToken = lock.lease_token
    try:
        recovery = _recovery_dict(item)
        recovery.update({"recovery_started_at": _utc_now(), "recovery_owner": owner or "system"})
        item = update_remote_operation_recovery_state(
            settings,
            item.id,
            item.recovery_generation,
            recoveryToken,
            status="recovering",
            recovery_phase=item.recovery_phase,
            error_message=None,
            recovery=recovery,
            expected_phases={item.recovery_phase},
        )
        context = get_media_source_protocol_context(settings, item.media_source_id)
        protocol = get_protocol(path_type)
        action = "reconciled" if item.recovery_phase == "reconciling" else "retried"
        with _RecoveryHeartbeat(settings, item, recoveryToken, lock_key) as heartbeat:
            if item.recovery_phase == "probing":
                forward_readiness = protocol.check_rename_ready(item.source_path, item.target_path, context)
                heartbeat.ensureActive()
                if forward_readiness.success:
                    sourceInfo = protocol.get_file_info(item.source_path, context)
                    heartbeat.ensureActive()
                    if not isStrongEtag(item.source_version) or sourceInfo.version != item.source_version:
                        raise RemoteOperationRecoveryConflict("远程源文件版本不明或已变化，停止自动恢复")
                    context = replace(context, expected_source_version=item.source_version)
                    item = update_remote_operation_recovery_state(
                        settings,
                        item.id,
                        item.recovery_generation,
                        recoveryToken,
                        status="recovering",
                        recovery_phase="move_issued",
                        recovery=recovery,
                        expected_phases={"probing"},
                    )
                    heartbeat.ensureActive()
                    move_result = protocol.move_file(item.source_path, item.target_path, context)
                    heartbeat.ensureActive()
                    if not move_result.success:
                        raise RemoteOperationRecoveryConflict(
                            f"远程 MOVE 结果不明确，已停止自动重试：{move_result.message}"
                        )
                    item = update_remote_operation_recovery_state(
                        settings,
                        item.id,
                        item.recovery_generation,
                        recoveryToken,
                        status="remote_applied",
                        recovery_phase="remote_applied",
                        recovery=recovery,
                        expected_phases={"move_issued"},
                    )
                else:
                    reverse_readiness = protocol.check_rename_ready(item.target_path, item.source_path, context)
                    heartbeat.ensureActive()
                    if not reverse_readiness.success:
                        conflict_message = (
                            "无法确认远端文件状态，已停止自动恢复："
                            f"正向校验 {forward_readiness.message}；反向校验 {reverse_readiness.message}"
                        )
                        raise RemoteOperationRecoveryConflict(conflict_message)
                    action = "reconciled"
                    item = update_remote_operation_recovery_state(
                        settings,
                        item.id,
                        item.recovery_generation,
                        recoveryToken,
                        status="recovering",
                        recovery_phase="reconciling",
                        recovery=recovery,
                        expected_phases={"probing"},
                    )

            targetInfo = protocol.get_file_info(str(item.target_path), context)
            heartbeat.ensureActive()
            expectedVersion = item.target_version or item.source_version
            if not isStrongEtag(expectedVersion) or targetInfo.version != expectedVersion:
                raise RemoteOperationRecoveryConflict("远程目标文件版本不明或已变化，停止自动补齐")

        recovery.update({"recovery_action": action})
        business_recovery = _sync_business_records(
            settings,
            item,
            targetInfo,
            recoveryToken,
            recovery,
        )
        recovery.update(business_recovery)
        completed_item = get_remote_operation_item(settings, item.id)
        message = "远程 MOVE 已安全重试并完成恢复" if action == "retried" else "远端文件已移动，数据库状态已补齐"
        return RemoteOperationRecoveryResult(completed_item, action, message)
    except RemoteOperationRecoveryConflict as exc:
        try:
            update_remote_operation_recovery_state(
                settings,
                item.id,
                item.recovery_generation,
                recoveryToken,
                status="recovery_required",
                recovery_phase="review_required",
                error_message=str(exc),
                recovery=_recovery_dict(item),
                expected_phases={"probing", "move_issued", "remote_applied", "reconciling"},
                release_claim=True,
            )
        except RemoteOperationLockError:
            pass
        raise
    except Exception as exc:
        try:
            current_phase = item.recovery_phase
            uncertain = current_phase in {"move_issued", "remote_applied", "reconciling"}
            update_remote_operation_recovery_state(
                settings,
                item.id,
                item.recovery_generation,
                recoveryToken,
                status="recovery_required" if uncertain else "failed",
                recovery_phase="review_required" if uncertain else "idle",
                error_message=str(exc),
                recovery=_recovery_dict(item),
                expected_phases={current_phase},
                release_claim=True,
            )
        except RemoteOperationLockError:
            pass
        raise
    finally:
        release_remote_operation_lock(settings, lock_key, recoveryToken)
