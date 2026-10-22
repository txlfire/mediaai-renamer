"""安全重命名受理及兼容入口，复用共用文件队列协调器。"""

from contextlib import closing
import json
import sqlite3

from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import createFileActionAuditIntent
from app.service.operation_log_service import record_operation_log
from app.service.rename_operation_service import execute_rename_operation, get_rename_operation
from app.service.file_task_queue_service import (
    FILE_TASK_TIMEOUT_SECONDS, FileTaskLeaseError, now as _now, requireFileTaskLease,
    claimFileTask, heartbeatFileTask, finishInterruptedFileTask, recoverExpiredFileTasks,
    inspectInterruptedFileTasks, releaseInterruptedFileTask,
)

RENAME_TASK_TIMEOUT_SECONDS = FILE_TASK_TIMEOUT_SECONDS
RenameLeaseError = FileTaskLeaseError


def requireRenameLease(connection, operationId: int, token: str, *, requireAuditIntent: bool = True):
    return requireFileTaskLease(
        connection,
        "rename_operation",
        operationId,
        token,
        requireAuditIntent=requireAuditIntent,
    )


def acceptRename(settings, operationId: int, context: AuditWriteContext):
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM rename_operations WHERE id=?", (operationId,)).fetchone()
        if row is None:
            raise ValueError("重命名批次不存在")
        if row["status"] != "dry_run":
            if row["audit_intent_id"] is None:
                raise ValueError("批次不是可受理的干跑状态，请查看现有执行结果")
            return get_rename_operation(settings, operationId)
        if not row["ready_count"]:
            raise ValueError("批次没有可执行项")
        from app.service.cloud_directory_guard import requireNoDirectoryExecution
        from app.service.cloud_directory_path_service import requireFreshFileTask
        requireNoDirectoryExecution(connection)
        requireFreshFileTask(connection, "rename_operation", operationId)
        if connection.execute(
            "SELECT 1 FROM rename_operation_items mine JOIN rename_operation_items other "
            "ON other.rename_preview_id=mine.rename_preview_id JOIN rename_operations active ON active.id=other.operation_id "
            "WHERE mine.operation_id=? AND mine.status='ready' AND active.id<>? "
            "AND (active.status IN ('queued','running') OR active.execution_token IS NOT NULL) LIMIT 1",
            (operationId, operationId)).fetchone():
            raise ValueError("所选预览已有待执行、运行中或待核对批次")
        # 受理不访问媒体目录；来源存在性/停用由数据库核对，真实身份仍在动作前复核。
        if connection.execute(
            "SELECT 1 FROM rename_operation_items mine JOIN rename_operation_items original "
            "ON original.rename_preview_id=mine.rename_preview_id JOIN rename_rollback_items ri ON ri.operation_item_id=original.id "
            "JOIN rename_rollback_plans active ON active.id=ri.plan_id WHERE mine.operation_id=? AND mine.status='ready' "
            "AND (active.status IN ('queued','running') OR active.execution_token IS NOT NULL) LIMIT 1", (operationId,)).fetchone():
            raise ValueError("所选预览已有待执行、运行中或待核对回滚计划")
        invalid = connection.execute(
            "SELECT 1 FROM rename_operation_items i LEFT JOIN rename_previews p ON p.id=i.rename_preview_id "
            "LEFT JOIN media_files f ON f.id=p.media_file_id LEFT JOIN media_sources s ON s.id=f.media_source_id "
            "WHERE i.operation_id=? AND i.status='ready' "
            "AND (s.id IS NULL OR s.deleted_at IS NOT NULL OR s.enabled=0) LIMIT 1", (operationId,)).fetchone()
        if invalid:
            raise ValueError("批次媒体源已变化或停用，请重新干跑")
        if context.actor is not None:
            actor = connection.execute("SELECT enabled,permissions_json FROM users WHERE id=?", (context.actor.id,)).fetchone()
            if actor is None or not actor["enabled"] or "rename:execute" not in json.loads(actor["permissions_json"]):
                raise ValueError("重命名发起用户已停用或权限被撤销")
        intentId = createFileActionAuditIntent(settings, "rename_operation", operationId, context, connection=connection)
        connection.execute("UPDATE rename_operations SET status='queued',actor_id=?,actor_name=?,audit_context_json=?,"
            "audit_intent_id=?,error_message=NULL,updated_at=? WHERE id=?",
            (context.actor.id if context.actor else None, context.actor.username if context.actor else "system",
             json.dumps({"ipAddress": context.ipAddress, "userAgent": context.userAgent}, ensure_ascii=False),
             intentId, _now(), operationId))
        record_operation_log(settings, task_type="rename_operation", task_id=operationId, level="info",
            stage="rename_queued", message="重命名任务已受理，等待后台执行", connection=connection)
        connection.commit()
    return get_rename_operation(settings, operationId)


def claimRename(settings):
    return claimFileTask(settings, "rename_operation")


def heartbeatRename(settings, operationId, token):
    return heartbeatFileTask(settings, "rename_operation", operationId, token)


def finishInterruptedRename(settings, operationId, token, reason, *, processExited: bool, localRecovery: bool = False):
    return finishInterruptedFileTask(settings, "rename_operation", operationId, token, reason,
                                     processExited=processExited, localRecovery=localRecovery)


def recoverExpiredRenames(settings):
    return recoverExpiredFileTasks(settings, "rename_operation")


def executeAcceptedRename(settings, operationId, token):
    return execute_rename_operation(settings, operationId, executionToken=token)


def inspectInterruptedRenames(settings, *, limit=100, afterId=0):
    return inspectInterruptedFileTasks(settings, "rename_operation", limit=limit, afterId=afterId)


def releaseInterruptedRename(settings, operationId, *, confirmStopped: bool):
    return releaseInterruptedFileTask(settings, "rename_operation", operationId, confirmStopped=confirmStopped)
