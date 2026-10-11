"""文件动作审计意图；初始失败拒绝执行，完成审计失败保留可补写意图。"""

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from typing import Callable, Any

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.service.audit_intent_retention_service import ensureAuditIntentCapacity
from app.service.audit_service import AuditWriteContext, executionAuditResult, record_audit_event, sanitize_audit_detail
from app.service.rename_operation_service import get_rename_operation
from app.service.rename_rollback_service import get_rename_rollback_plan
from app.service.remote_operation_service import get_remote_operation_item
from app.utils.redaction import redactUrlCredentials

logger = get_logger(__name__)
TASKS = {
    "rename_operation": ("rename.execute", "execute_rename_operation", "重命名"),
    "rollback_plan": ("rename.rollback", "execute_rollback_plan", "回滚"),
    "remote_operation_recovery": ("remote_operation.recover", "recover_remote_operation", "远端恢复"),
    "pending_move": ("pending.move", "execute_pending_move", "迁移"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _taskResult(settings: AppSettings, taskType: str, taskId: int):
    if taskType == "rename_operation":
        return get_rename_operation(settings, taskId)
    if taskType == "rollback_plan":
        return get_rename_rollback_plan(settings, taskId)
    if taskType == "remote_operation_recovery":
        return get_remote_operation_item(settings, taskId)
    if taskType == "pending_move":
        from app.service.pending_move_service import getPendingMove
        return getPendingMove(settings, taskId)
    raise ValueError("文件动作审计任务类型不支持")


def createFileActionAuditIntent(settings: AppSettings, taskType: str, taskId: int, context: AuditWriteContext,
                              *, connection: sqlite3.Connection | None = None) -> int:
    if taskType not in TASKS:
        raise ValueError("文件动作审计任务类型不支持")
    if taskType == "pending_move" and connection is not None:
        if connection.execute("SELECT 1 FROM pending_move_tasks WHERE id=?", (taskId,)).fetchone() is None:
            raise ValueError("迁移任务不存在")
    else:
        _taskResult(settings, taskType, taskId)
    eventType, action, label = TASKS[taskType]
    payload = sanitize_audit_detail({
        "event_type": eventType, "action": action,
        "target_type": "remote_operation" if taskType == "remote_operation_recovery" else taskType, "target_id": taskId,
        "actor_id": context.actor.id if context.actor else None,
        "actor_name": context.actor.username if context.actor else "system",
        "ip_address": context.ipAddress, "user_agent": context.userAgent,
    })
    now = _now()
    def persist(target):
        ensureAuditIntentCapacity(settings, target, len(json.dumps(payload, ensure_ascii=False).encode('utf-8')))
        cursor = target.execute(
            "INSERT INTO file_action_audit_intents "
            "(task_type, task_id, context_json, request_phase, created_at, updated_at) "
            "VALUES (?, ?, ?, 'accepted', ?, ?)",
            (taskType, taskId, json.dumps(payload, ensure_ascii=False), now, now),
        )
        intentId = int(cursor.lastrowid)
        record_audit_event(settings, **payload, result="pending", summary=f"{label}执行请求已记录，等待实际结果",
                           detail={"audit_intent_id": intentId, "task_id": taskId}, connection=target)
        return intentId
    if connection is not None:
        return persist(connection)
    with closing(sqlite3.connect(settings.database_path)) as ownedConnection:
        ownedConnection.execute("BEGIN IMMEDIATE")
        intentId = persist(ownedConnection)
        ownedConnection.commit()
    return intentId


def completeFileActionAuditIntent(settings: AppSettings, intentId: int, executionResult=None, failureReason: str | None = None) -> bool:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        intent = connection.execute("SELECT * FROM file_action_audit_intents WHERE id = ?", (intentId,)).fetchone()
        if intent is None:
            raise ValueError("文件动作审计意图不存在")
        if intent["status"] == "completed":
            return True
        taskType, taskId = str(intent["task_type"]), int(intent["task_id"])
        savedOutcome = json.loads(intent["outcome_json"]) if intent["outcome_json"] else None
        if savedOutcome is not None:
            return _writeFinalAudit(settings, connection, intent, savedOutcome)
        # 任务状态可能属于旧请求或另一执行者，缺少本次结果时不能提前补写。
        if executionResult is None and failureReason is None:
            return False
        if taskType == "remote_operation_recovery":
            outcome = _remoteRecoveryOutcome(settings, intentId, taskId, executionResult, failureReason)
            if outcome is None:
                return False
            return _persistFinalOutcome(settings, connection, intent, outcome)
        result = executionResult or _taskResult(settings, taskType, taskId)
        if result.id != taskId:
            raise ValueError("审计结果与任务不一致")
        if failureReason is None and result.status in {"queued", "running", "dry_run", "draft", "checked"}:
            return False
        succeeded = result.moved_count if taskType == "pending_move" else result.renamed_count if taskType == "rename_operation" else sum(item.status == "rolled_back" for item in result.items)
        failed = result.failed_count if taskType == "rename_operation" else sum(item.status == "failed" for item in result.items)
        conflict = result.conflict_count if taskType == "rename_operation" else sum(item.status == "conflict" for item in result.items)
        outcome = executionAuditResult(result.status, succeededCount=succeeded, failedCount=failed, conflictCount=conflict)
        if failureReason is not None:
            outcome = "failed"
        detail = {
            "audit_intent_id": intentId, "task_id": taskId, "status": result.status,
            "item_count": len(result.items), "succeeded_count": succeeded, "failed_count": failed,
            "conflict_count": conflict,
            "failure_examples": [{"item_id": item.id, "status": item.status, "message": item.message}
                                 for item in result.items if item.status in {"failed", "conflict"}][:10],
        }
        if taskType == "rename_operation":
            detail.update(operation_id=taskId, total_count=result.total_count, ready_count=result.ready_count, renamed_count=succeeded)
        elif taskType == "rollback_plan":
            detail.update(plan_id=taskId, rolled_back_count=succeeded, executable_count=result.executable_count)
        else:
            detail.update(moved_count=succeeded, unprocessed_count=max(0, result.total_count - succeeded - failed))
        if failureReason:
            detail["request_error"] = failureReason
            detail["counts_scope"] = "current_business_snapshot"
        label = TASKS[taskType][2]
        savedOutcome = sanitize_audit_detail({
            "result": outcome,
            "summary": f"{label}请求 {taskId} 失败；业务当前状态：{result.status}" if failureReason is not None else
                       f"{label}任务 {taskId}：成功 {succeeded} 项，失败 {failed} 项，冲突 {conflict} 项",
            "detail": detail,
        })
        return _persistFinalOutcome(settings, connection, intent, savedOutcome)


def _remoteRecoveryOutcome(settings: AppSettings, intentId: int, taskId: int, executionResult, failureReason):
    # 业务终态可能属于旧请求；维护不能抢在当前请求结束前猜测结果。
    if executionResult is None and failureReason is None:
        return None
    item = executionResult.item if executionResult is not None else _taskResult(settings, "remote_operation_recovery", taskId)
    if item.id != taskId:
        raise ValueError("审计结果与任务不一致")
    action = executionResult.action if executionResult is not None else "request_failed"
    detail = {"audit_intent_id": intentId, "task_id": taskId, "item_id": taskId,
              "status": item.status, "operation_type": item.operation_type, "action": action}
    if failureReason:
        detail["request_error"] = failureReason
    succeeded = failureReason is None and item.status == "completed"
    return sanitize_audit_detail({"result": "success" if succeeded else "failed",
        "summary": f"远端恢复任务 {taskId}：{'已完成' if succeeded else '未完成'}", "detail": detail})


def _persistFinalOutcome(settings: AppSettings, connection: sqlite3.Connection, intent, outcome: dict) -> bool:
    # 先固定本次请求结果，补写时不把后来重试的状态误记成原请求结果。
    connection.execute("UPDATE file_action_audit_intents SET outcome_json = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(outcome, ensure_ascii=False), _now(), intent["id"]))
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    intent = connection.execute("SELECT * FROM file_action_audit_intents WHERE id = ?", (intent["id"],)).fetchone()
    if intent["status"] == "completed":
        return True
    return _writeFinalAudit(settings, connection, intent, outcome)


def _writeFinalAudit(settings: AppSettings, connection: sqlite3.Connection, intent, outcome: dict) -> bool:
    payload = json.loads(intent["context_json"])
    record_audit_event(settings, **payload, **outcome, connection=connection)
    connection.execute("UPDATE file_action_audit_intents SET status = 'completed', request_phase='completed', "
                       "last_error_type = NULL, updated_at = ? WHERE id = ?",
                       (_now(), intent["id"]))
    connection.commit()
    return True


def _tryComplete(settings: AppSettings, intentId: int, executionResult=None, failureReason: str | None = None) -> bool:
    try:
        return completeFileActionAuditIntent(settings, intentId, executionResult, failureReason)
    except Exception as exc:  # noqa: BLE001 - 初始意图已可靠提交，完成审计可补写，不伪报文件未生效。
        try:
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("UPDATE file_action_audit_intents SET last_error_type = ?, updated_at = ? WHERE id = ?", (type(exc).__name__, _now(), intentId))
                connection.commit()
            logger.warning("文件动作审计待补写 intent_id=%s error_type=%s", intentId, type(exc).__name__)
        except Exception:  # noqa: BLE001 - 数据库不可用时仍保留先前持久化的意图，不覆盖主异常。
            pass
        return False


def runAuditedFileAction(settings: AppSettings, *, taskType: str, taskId: int, context: AuditWriteContext,
                         execute: Callable[[], Any], response):
    intentId = createFileActionAuditIntent(settings, taskType, taskId, context)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute(
            "UPDATE file_action_audit_intents SET request_phase='executing',updated_at=? "
            "WHERE id=? AND status='pending'",
            (_now(), intentId),
        )
        connection.commit()
    try:
        result = execute()
    except Exception as exc:
        _tryComplete(settings, intentId, failureReason=str(exc))
        raise
    if not _tryComplete(settings, intentId, executionResult=result):
        response.headers["X-MediaAI-Audit-State"] = "pending"
    return result


def markFileActionAuditIntentReviewRequired(
    settings: AppSettings,
    intentId: int,
    *,
    requestPhase: str,
    reason: str,
) -> bool:
    """把无法证明结果的请求转为人工核对，不推断业务成功或失败。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status FROM file_action_audit_intents WHERE id=?",
            (intentId,),
        ).fetchone()
        if row is None:
            raise ValueError("文件动作审计意图不存在")
        if row[0] in {"completed", "manually_closed"}:
            return False
        changed = connection.execute(
            "UPDATE file_action_audit_intents SET status='review_required',request_phase=?,"
            "last_error_type=?,updated_at=? WHERE id=? AND status IN ('pending','review_required')",
            (requestPhase, reason[:200], _now(), intentId),
        ).rowcount
        connection.commit()
    return changed == 1


def _publicAuditIntent(row: sqlite3.Row) -> dict:
    fields = (
        "id", "task_type", "task_id", "status", "request_phase", "last_error_type",
        "closed_at", "closed_by", "close_reason", "close_evidence", "created_at", "updated_at",
    )
    return {field: row[field] for field in fields} | {"has_outcome": row["outcome_json"] is not None}


def listFileActionAuditIntents(
    settings: AppSettings,
    *,
    page: int = 1,
    pageSize: int = 50,
    status: str | None = None,
) -> dict:
    allowedStatuses = {"pending", "review_required", "completed", "manually_closed"}
    if page < 1 or not 1 <= pageSize <= 200:
        raise ValueError("页码或每页数量无效")
    if status is not None and status not in allowedStatuses:
        raise ValueError("审计意图状态无效")
    where = " WHERE status=?" if status else ""
    params = (status,) if status else ()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        total = int(connection.execute(
            "SELECT COUNT(*) FROM file_action_audit_intents" + where,
            params,
        ).fetchone()[0])
        pages = max(1, (total + pageSize - 1) // pageSize)
        normalizedPage = min(page, pages)
        rows = connection.execute(
            "SELECT id,task_type,task_id,status,request_phase,last_error_type,outcome_json,"
            "closed_at,closed_by,close_reason,close_evidence,created_at,updated_at "
            "FROM file_action_audit_intents" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, pageSize, (normalizedPage - 1) * pageSize),
        ).fetchall()
    return {
        "items": [_publicAuditIntent(row) for row in rows],
        "page": normalizedPage,
        "page_size": pageSize,
        "total": total,
        "pages": pages,
    }


def closeFileActionAuditIntent(
    settings: AppSettings,
    intentId: int,
    *,
    reason: str,
    evidence: str,
    context: AuditWriteContext,
) -> dict:
    reason = redactUrlCredentials(reason.strip())
    evidence = redactUrlCredentials(evidence.strip())
    if not reason or len(reason) > 500:
        raise ValueError("关闭原因不能为空且不能超过500个字符")
    if not evidence or len(evidence) > 2000:
        raise ValueError("核查证据不能为空且不能超过2000个字符")
    now = _now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM file_action_audit_intents WHERE id=?",
            (intentId,),
        ).fetchone()
        if row is None:
            raise ValueError("文件动作审计意图不存在")
        if row["status"] == "manually_closed":
            connection.rollback()
            return _publicAuditIntent(row)
        if row["status"] not in {"pending", "review_required"}:
            raise ValueError("只有待补写或待核查意图可以人工关闭")
        outcome = sanitize_audit_detail({
            "action_outcome": "unknown",
            "status": "manually_closed",
            "reason": reason,
            "evidence": evidence,
        })
        changed = connection.execute(
            "UPDATE file_action_audit_intents SET status='manually_closed',request_phase='manually_closed',"
            "outcome_json=?,closed_at=?,closed_by=?,close_reason=?,close_evidence=?,updated_at=? "
            "WHERE id=? AND status IN ('pending','review_required')",
            (json.dumps(outcome, ensure_ascii=False), now,
             context.actor.username if context.actor else "system", reason, evidence, now, intentId),
        ).rowcount
        if changed != 1:
            raise ValueError("审计意图状态已变化，拒绝覆盖关闭证据")
        record_audit_event(
            settings,
            event_type="audit.intent",
            action="close_file_action_intent",
            result="success",
            summary=f"人工关闭未知文件动作审计意图：{intentId}",
            target_type="file_action_audit_intent",
            target_id=intentId,
            actor=context.actor,
            detail={"reason": reason, "evidence": evidence, "action_outcome": "unknown"},
            ip_address=context.ipAddress,
            user_agent=context.userAgent,
            connection=connection,
        )
        connection.commit()
        closed = connection.execute(
            "SELECT * FROM file_action_audit_intents WHERE id=?",
            (intentId,),
        ).fetchone()
    return _publicAuditIntent(closed)


def inspectFileActionAuditIntents(settings: AppSettings, *, limit: int = 200, afterId: int = 0) -> dict:
    if not 1 <= limit <= 5000 or afterId < 0:
        raise ValueError("审计核对数量必须为 1-5000，游标不能为负数")
    uri = settings.database_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        rows = connection.execute(
            "SELECT id, task_type, task_id, outcome_json IS NOT NULL, last_error_type "
            "FROM file_action_audit_intents WHERE status = 'pending' AND id > ? ORDER BY id LIMIT ?",
            (afterId, limit + 1),
        ).fetchall()
    return {
        "items": [{"id": row[0], "taskType": row[1], "taskId": row[2], "hasOutcome": bool(row[3]), "errorType": row[4]}
                  for row in rows[:limit]],
        "has_more": int(len(rows) > limit), "next_id": rows[min(len(rows), limit) - 1][0] if rows else afterId,
    }


def flushFileActionAuditIntents(settings: AppSettings, *, limit: int = 200, afterId: int = 0) -> dict[str, int]:
    report = inspectFileActionAuditIntents(settings, limit=limit, afterId=afterId)
    rows = report["items"]
    completed = sum(_tryComplete(settings, row["id"]) for row in rows)
    return {"completed": completed, "pending": len(rows) - completed,
            "has_more": report["has_more"], "next_id": report["next_id"]}
