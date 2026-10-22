"""文件写任务共用持久化槽及租约；表名仅来自内部固定映射。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import sqlite3

from app.service.audit_service import record_audit_event
from app.service.operation_log_service import record_operation_log
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_directory_path_service import requireFreshFileTask

FILE_TASK_TIMEOUT_SECONDS = 3600
PROCESS_INSTANCE_ID = secrets.token_urlsafe(18)
PROCESS_INSTANCE_STARTED_AT = datetime.now(timezone.utc).isoformat()
TASKS = {
    "rename_operation": ("rename_operations", "重命名", "rename:execute", "rename"),
    "rollback_plan": ("rename_rollback_plans", "回滚", "rollback:execute", "rollback"),
    "pending_move": ("pending_move_tasks", "迁移", "rename:execute", "pending_move"),
}


class FileTaskLeaseError(ValueError):
    """授权失效必须中断整批，不能当普通单项失败后继续操作。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _expiry(seconds=60):
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).isoformat()


def _spec(kind):
    if kind not in TASKS:
        raise ValueError("文件任务类型不支持")
    return TASKS[kind]


def requireFileTaskActor(connection, kind, actorId):
    requireNoDirectoryExecution(connection)
    _, label, permission, _ = _spec(kind)
    if actorId is not None:
        actor = connection.execute("SELECT enabled,permissions_json FROM users WHERE id=?", (actorId,)).fetchone()
        if actor is None or not actor["enabled"] or permission not in json.loads(actor["permissions_json"]):
            raise FileTaskLeaseError(f"{label}发起用户已停用或权限被撤销")


def requireFileTaskLease(connection, kind, taskId, token, *, requireAuditIntent=True):
    requireNoDirectoryExecution(connection)
    table, label, _, _ = _spec(kind)
    auditCondition = " AND audit_intent_id IS NOT NULL" if requireAuditIntent else ""
    row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND status='running' AND execution_token=? "
        f"AND julianday(execution_expires_at)>julianday('now'){auditCondition}", (taskId, token)).fetchone()
    if row is None:
        raise FileTaskLeaseError(f"{label}执行授权已失效，请核对任务；不会自动重试")
    requireFreshFileTask(connection, kind, taskId)
    requireFileTaskActor(connection, kind, row["actor_id"])
    return row


def assertFileTaskSlotFree(connection, kind, taskId, *, includeQueued=True):
    _spec(kind)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    requireNoDirectoryExecution(connection)
    requireFreshFileTask(connection, kind, taskId)
    statuses = "('queued','running')" if includeQueued else "('running')"
    for otherKind, (table, _, _, _) in TASKS.items():
        exclude = " AND id<>?" if otherKind == kind else ""
        # 分开查询使状态索引和保留令牌部分索引分别生效，不扫已完成历史。
        for condition in ("execution_token IS NOT NULL", f"status IN {statuses}"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE {condition}" +
                                  exclude + " LIMIT 1", (taskId,) if exclude else ()).fetchone():
                raise ValueError("已有文件写任务待执行、运行中或原进程退出未确认，请查看现有任务")


def claimSynchronousFileTask(connection, kind, taskId, expectedStatus):
    """在调用线程执行文件动作前持久化归属；调用方必须在同一事务完成业务条件复核。"""

    table, _, _, _ = _spec(kind)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    assertFileTaskSlotFree(connection, kind, taskId)
    token = secrets.token_urlsafe(24)
    timestamp = now()
    changed = connection.execute(
        f"UPDATE {table} SET status='running',execution_token=?,execution_expires_at=?,execution_pid=?,"
        "execution_instance_id=?,execution_started_at=?,updated_at=? WHERE id=? AND status=?",
        (
            token,
            _expiry(FILE_TASK_TIMEOUT_SECONDS),
            os.getpid(),
            PROCESS_INSTANCE_ID,
            timestamp,
            timestamp,
            taskId,
            expectedStatus,
        ),
    ).rowcount
    if changed != 1:
        raise ValueError("文件任务状态已变化，未取得同步执行归属")
    return token


def releaseSynchronousFileTask(settings, kind, taskId, token):
    """同步任务成功提交终态后释放自身持久化槽。"""

    table, _, _, _ = _spec(kind)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            f"UPDATE {table} SET execution_token=NULL,execution_expires_at=NULL,execution_pid=NULL,"
            "execution_instance_id=NULL,execution_started_at=NULL WHERE id=? AND execution_token=? "
            "AND status NOT IN ('queued','running')",
            (taskId, token),
        ).rowcount
        connection.commit()
    if changed != 1:
        raise FileTaskLeaseError("同步文件任务终态或执行归属已变化，执行槽保留待核对")


def claimFileTask(settings, kind):
    table, _, _, _ = _spec(kind)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            assertFileTaskSlotFree(connection, kind, -1, includeQueued=False)
        except ValueError:
            return None
        # 每个类型只取一个索引队首，worker只认领全局最早受理的类型。
        row = connection.execute("WITH rename_head AS ("
            "SELECT id,updated_at FROM rename_operations WHERE status='queued' ORDER BY updated_at,id LIMIT 1), "
            "rollback_head AS (SELECT id,updated_at FROM rename_rollback_plans WHERE status='queued' ORDER BY updated_at,id LIMIT 1) "
            ", move_head AS (SELECT id,updated_at FROM pending_move_tasks WHERE status='queued' ORDER BY updated_at,id LIMIT 1) "
            "SELECT kind,id FROM (SELECT 'rename_operation' AS kind,id,updated_at AS accepted_at FROM rename_head "
            "UNION ALL SELECT 'rollback_plan',id,updated_at FROM rollback_head "
            "UNION ALL SELECT 'pending_move',id,updated_at FROM move_head) "
            "ORDER BY accepted_at,kind,id LIMIT 1").fetchone()
        if row is None or row[0] != kind:
            return None
        token = secrets.token_urlsafe(24)
        timestamp = now()
        connection.execute(f"UPDATE {table} SET status='running',execution_token=?,execution_expires_at=?,"
                           "execution_instance_id=?,execution_started_at=?,updated_at=? "
                           "WHERE id=? AND status='queued'",
                           (token, _expiry(), PROCESS_INSTANCE_ID, timestamp, timestamp, row[1]))
        auditIntent = connection.execute(
            f"SELECT audit_intent_id FROM {table} WHERE id=?",
            (row[1],),
        ).fetchone()
        if auditIntent is not None and auditIntent[0] is not None:
            connection.execute(
                "UPDATE file_action_audit_intents SET request_phase='executing',updated_at=? "
                "WHERE id=? AND status='pending'",
                (timestamp, auditIntent[0]),
            )
        connection.commit()
        return int(row[1]), token


def heartbeatFileTask(settings, kind, taskId, token):
    table, _, _, _ = _spec(kind)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        changed = connection.execute(f"UPDATE {table} SET execution_expires_at=? WHERE id=? AND status='running' "
            "AND execution_token=? AND julianday(execution_expires_at)>julianday('now')", (_expiry(), taskId, token)).rowcount
        connection.commit()
        return changed == 1


def recoverExpiredFileTasks(settings, kind):
    table, label, _, stage = _spec(kind)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(f"SELECT id FROM {table} WHERE status='running' AND execution_token IS NOT NULL "
            "AND (julianday(execution_expires_at) IS NULL OR julianday(execution_expires_at)<=julianday('now')) "
            "ORDER BY id LIMIT 1000").fetchall()
        reason = f"{label}执行授权过期；原进程退出未确认，执行槽保留，不会自动重试"
        for row in rows:
            connection.execute(f"UPDATE {table} SET status='interrupted',error_message=?,updated_at=? WHERE id=?", (reason, now(), row[0]))
            record_operation_log(settings, task_type=kind, task_id=row[0], level="error", stage=f"{stage}_interrupted", message=reason, connection=connection)
        connection.commit()
        return len(rows)


def _getFileTask(settings, kind, taskId):
    if kind == "rename_operation":
        from app.service.rename_operation_service import get_rename_operation
        return get_rename_operation(settings, taskId)
    if kind == "pending_move":
        from app.service.pending_move_service import getPendingMove
        return getPendingMove(settings, taskId)
    from app.service.rename_rollback_service import get_rename_rollback_plan
    return get_rename_rollback_plan(settings, taskId)


def finishInterruptedFileTask(settings, kind, taskId, token, reason, *, processExited, localRecovery=False):
    table, label, _, stage = _spec(kind)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND execution_token=?", (taskId, token)).fetchone()
        if row is None:
            return
        if localRecovery:
            from app.service.scan_recovery_service import isProcessAlive
            if row["status"] in {"queued", "running"} or row["execution_pid"] is None or isProcessAlive(int(row["execution_pid"])):
                raise ValueError("执行进程仍存在、PID未知或任务未中断，拒绝释放槽位")
        if row["status"] in {"running", "interrupted"}:
            connection.execute(f"UPDATE {table} SET status='interrupted',error_message=?,updated_at=? WHERE id=?", (reason, now(), taskId))
            record_operation_log(settings, task_type=kind, task_id=taskId, level="error",
                stage=f"{stage}_interrupted", message=reason, connection=connection)
        connection.commit()
    if processExited and row["audit_intent_id"] is not None:
        from app.service.file_action_audit_service import (
            completeFileActionAuditIntent,
            markFileActionAuditIntentReviewRequired,
        )
        result = _getFileTask(settings, kind, taskId)
        if result.status == "interrupted":
            markFileActionAuditIntentReviewRequired(
                settings,
                row["audit_intent_id"],
                requestPhase="process_interrupted",
                reason=reason,
            )
        else:
            completeFileActionAuditIntent(
                settings,
                row["audit_intent_id"],
                executionResult=result,
            )
    if processExited:
        if kind == "pending_move":
            from app.service.pending_move_service import releasePendingMoveReservations
            releasePendingMoveReservations(settings, taskId, token)
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(f"UPDATE {table} SET execution_token=NULL,execution_expires_at=NULL,execution_pid=NULL,"
                                         "execution_instance_id=NULL,execution_started_at=NULL "
                                         "WHERE id=? AND execution_token=?", (taskId, token)).rowcount
            if localRecovery and changed:
                record_audit_event(settings, event_type={"rename_operation": "rename.execute", "rollback_plan": "rename.rollback", "pending_move": "pending.move"}[kind],
                    action=f"release_{stage}_slot", result="success", summary=f"本机核对并释放{label}遗留槽，不重放文件动作",
                    target_type=kind, target_id=taskId, actor_name=f"local_{stage}_recovery",
                    detail={"process_exited": True, "replayed": False}, connection=connection)
            connection.commit()


def inspectInterruptedFileTasks(settings, kind, *, limit=100, afterId=0):
    table, _, _, _ = _spec(kind)
    if not 1 <= limit <= 1000 or afterId < 0:
        raise ValueError("核对数量或游标无效")
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(f"SELECT id,status,execution_pid,execution_expires_at,execution_instance_id,"
            f"execution_started_at,execution_token IS NOT NULL AS has_execution_token FROM {table} "
            "WHERE ((status IN ('interrupted','completed','executed','partial_failed','failed') "
            "AND execution_token IS NOT NULL) OR (status='running' AND (execution_token IS NULL OR execution_pid IS NULL))) "
            "AND id>? ORDER BY id LIMIT ?", (afterId, limit + 1)).fetchall()
    return {"items": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_id": rows[min(limit, len(rows)) - 1]["id"] if rows else afterId}


def releaseInterruptedFileTask(settings, kind, taskId, *, confirmStopped):
    table, label, _, stage = _spec(kind)
    if not confirmStopped:
        raise ValueError("释放执行槽必须显式确认服务已停止")
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND ("
            "(status IN ('interrupted','completed','executed','partial_failed','failed') AND execution_token IS NOT NULL) "
            "OR (status='running' AND (execution_token IS NULL OR execution_pid IS NULL)))", (taskId,)).fetchone()
    if row is None:
        raise ValueError("任务不存在或没有待核对的执行槽")
    if row["status"] == "running":
        reason = f"已确认相关服务停止；{label}归属信息不完整，未知文件动作仍需独立核对"
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(f"SELECT * FROM {table} WHERE id=? AND status='running' "
                "AND (execution_token IS NULL OR execution_pid IS NULL)", (taskId,)).fetchone()
            if current is None:
                raise ValueError("任务归属已变化，拒绝释放")
            connection.execute(
                f"UPDATE {table} SET status='interrupted',error_message=?,execution_token=NULL,"
                "execution_expires_at=NULL,execution_pid=NULL,execution_instance_id=NULL,"
                "execution_started_at=NULL,updated_at=? WHERE id=?",
                (reason, now(), taskId),
            )
            record_operation_log(
                settings,
                task_type=kind,
                task_id=taskId,
                level="error",
                stage=f"{stage}_interrupted",
                message=reason,
                connection=connection,
            )
            record_audit_event(
                settings,
                event_type={"rename_operation": "rename.execute", "rollback_plan": "rename.rollback", "pending_move": "pending.move"}[kind],
                action=f"release_{stage}_unknown_slot",
                result="success",
                summary=f"停服核对后释放{label}未知归属槽",
                target_type=kind,
                target_id=taskId,
                actor_name=f"local_{stage}_recovery",
                detail={"service_stopped": True, "replayed": False, "outcome": "unknown"},
                connection=connection,
            )
            auditIntentId = current["audit_intent_id"]
            connection.commit()
        if auditIntentId is not None:
            from app.service.file_action_audit_service import markFileActionAuditIntentReviewRequired
            markFileActionAuditIntentReviewRequired(
                settings,
                auditIntentId,
                requestPhase="ownership_unknown",
                reason=reason,
            )
        return
    finishInterruptedFileTask(settings, kind, taskId, row["execution_token"],
        f"原{label}进程已退出，执行槽已释放；不自动重试，请核对未知文件动作", processExited=True, localRecovery=True)
