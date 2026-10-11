"""回滚受理只保存数据库意图，实际文件动作由隔离worker执行。"""

from contextlib import closing
import json
import sqlite3

from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import createFileActionAuditIntent
from app.service.operation_log_service import record_operation_log
from app.service.rename_rollback_service import execute_rename_rollback_plan, get_rename_rollback_plan
from app.service.file_task_queue_service import (
    FILE_TASK_TIMEOUT_SECONDS, FileTaskLeaseError, now, requireFileTaskActor, requireFileTaskLease,
    claimFileTask, heartbeatFileTask, finishInterruptedFileTask, recoverExpiredFileTasks,
    inspectInterruptedFileTasks, releaseInterruptedFileTask,
)

ROLLBACK_TASK_TIMEOUT_SECONDS = FILE_TASK_TIMEOUT_SECONDS
RollbackLeaseError = FileTaskLeaseError


def requireRollbackLease(connection, planId, token):
    return requireFileTaskLease(connection, "rollback_plan", planId, token)


def acceptRollback(settings, planId, context: AuditWriteContext):
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM rename_rollback_plans WHERE id=?", (planId,)).fetchone()
        if row is None:
            raise ValueError("回滚计划不存在")
        if row["status"] != "checked":
            if row["audit_intent_id"] is None:
                raise ValueError("请先执行回滚 dry-run，并查看现有执行结果")
            return get_rename_rollback_plan(settings, planId)
        if row["executable_count"] <= 0:
            raise ValueError("回滚计划没有可执行条目")
        if connection.execute(
            "SELECT 1 FROM rename_rollback_items mine JOIN rename_rollback_items other "
            "ON other.operation_item_id=mine.operation_item_id JOIN rename_rollback_plans active ON active.id=other.plan_id "
            "WHERE mine.plan_id=? AND mine.status='ready' AND active.id<>? "
            "AND (active.status IN ('queued','running') OR active.execution_token IS NOT NULL) LIMIT 1", (planId, planId)).fetchone():
            raise ValueError("所选文件已有待执行、运行中或待核对回滚计划")
        invalid = connection.execute(
            "SELECT 1 FROM rename_rollback_items i LEFT JOIN rename_operation_items oi ON oi.id=i.operation_item_id "
            "LEFT JOIN rename_operations op ON op.id=oi.operation_id LEFT JOIN rename_previews p ON p.id=oi.rename_preview_id "
            "LEFT JOIN media_files f ON f.id=p.media_file_id LEFT JOIN media_sources s ON s.id=f.media_source_id "
            "WHERE i.plan_id=? AND i.status='ready' AND (s.id IS NULL OR s.deleted_at IS NOT NULL OR s.enabled=0 "
            "OR op.status IN ('queued','running') OR op.execution_token IS NOT NULL OR oi.status<>'renamed' "
            "OR p.status<>'renamed' OR f.file_path<>i.current_path OR i.snapshot_json IS NULL "
            "OR EXISTS (SELECT 1 FROM scan_jobs j WHERE j.id=f.scan_job_id AND j.source_invalidated_at IS NOT NULL)) LIMIT 1", (planId,)).fetchone()
        if invalid:
            raise ValueError("回滚关联媒体源、文件记录或安全快照已变化，请重新干跑")
        if connection.execute(
            "SELECT 1 FROM rename_rollback_items mine JOIN rename_operation_items original ON original.id=mine.operation_item_id "
            "JOIN rename_operation_items other ON other.rename_preview_id=original.rename_preview_id "
            "JOIN rename_operations active ON active.id=other.operation_id WHERE mine.plan_id=? AND mine.status='ready' "
            "AND (active.status IN ('queued','running') OR active.execution_token IS NOT NULL) LIMIT 1", (planId,)).fetchone():
            raise ValueError("所选预览已有待执行、运行中或待核对重命名批次")
        requireFileTaskActor(connection, "rollback_plan", context.actor.id if context.actor else None)
        intentId = createFileActionAuditIntent(settings, "rollback_plan", planId, context, connection=connection)
        connection.execute("UPDATE rename_rollback_plans SET status='queued',actor_id=?,actor_name=?,audit_context_json=?,"
            "audit_intent_id=?,error_message=NULL,updated_at=? WHERE id=?",
            (context.actor.id if context.actor else None, context.actor.username if context.actor else "system",
             json.dumps({"ipAddress": context.ipAddress, "userAgent": context.userAgent}, ensure_ascii=False), intentId, now(), planId))
        record_operation_log(settings, task_type="rollback_plan", task_id=planId, level="info", stage="rollback_queued",
                             message="回滚任务已受理，等待后台执行", connection=connection)
        connection.commit()
    return get_rename_rollback_plan(settings, planId)


def claimRollback(settings):
    return claimFileTask(settings, "rollback_plan")


def heartbeatRollback(settings, planId, token):
    return heartbeatFileTask(settings, "rollback_plan", planId, token)


def finishInterruptedRollback(settings, planId, token, reason, *, processExited, localRecovery=False):
    return finishInterruptedFileTask(settings, "rollback_plan", planId, token, reason,
                                     processExited=processExited, localRecovery=localRecovery)


def recoverExpiredRollbacks(settings):
    return recoverExpiredFileTasks(settings, "rollback_plan")


def executeAcceptedRollback(settings, planId, token):
    return execute_rename_rollback_plan(settings, planId, executionToken=token)


def inspectInterruptedRollbacks(settings, *, limit=100, afterId=0):
    return inspectInterruptedFileTasks(settings, "rollback_plan", limit=limit, afterId=afterId)


def releaseInterruptedRollback(settings, planId, *, confirmStopped):
    return releaseInterruptedFileTask(settings, "rollback_plan", planId, confirmStopped=confirmStopped)
