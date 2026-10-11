"""持久化扫描受理与执行租约，请求线程不访问媒体目录。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3

from app.core.config import AppSettings
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.media_source_service import get_media_source
from app.service.operation_log_service import record_operation_log
from app.service.scan_service import SCAN_MODES, _fetch_scan_job, buildScanPolicy, requireScanLease, run_full_scan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()


def _audit(settings, connection, row, action: str, result: str, detail: dict) -> None:
    context = json.loads(row["audit_context_json"] or "{}")
    record_audit_event(settings, event_type="scan.execute", action=action, result=result,
        summary="扫描队列状态变更", target_type="scan_job", target_id=row["id"],
        actor_id=row["actor_id"], actor_name=row["actor_name"],
        ip_address=context.get("ipAddress"), user_agent=context.get("userAgent"), detail=detail, connection=connection)


def acceptScan(settings: AppSettings, sourceId: int, scanMode: str, context: AuditWriteContext):
    if scanMode not in SCAN_MODES:
        raise ValueError("扫描模式不受支持")
    source = get_media_source(settings, sourceId)
    if not source.enabled:
        raise ValueError("媒体源已停用，无法发起扫描")
    policy = buildScanPolicy(settings, source, scanMode)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM media_sources WHERE id=? AND deleted_at IS NULL", (sourceId,)).fetchone()
        if current is None or not current["enabled"] or current["updated_at"] != source.updated_at:
            raise ValueError("媒体源已变化，请刷新后重新发起扫描")
        if connection.execute("SELECT id FROM scan_jobs WHERE media_source_id=? AND status IN ('queued','running')",
                              (sourceId,)).fetchone():
            raise ValueError("该媒体源已有待执行或运行中扫描，请查看现有任务")
        cursor = connection.execute(
            "INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,scan_mode,created_at,"
            "policy_json,actor_id,actor_name,audit_context_json) VALUES (?,'queued',?,?,?,?,?,?,?,?)",
            (sourceId, policy["batch_size"], policy["batch_interval_seconds"], scanMode, _now(),
             json.dumps(policy, ensure_ascii=False), context.actor.id if context.actor else None,
             context.actor.username if context.actor else "system",
             json.dumps({"ipAddress": context.ipAddress, "userAgent": context.userAgent}, ensure_ascii=False)),
        )
        jobId = int(cursor.lastrowid)
        row = connection.execute("SELECT * FROM scan_jobs WHERE id=?", (jobId,)).fetchone()
        _audit(settings, connection, row, "accept_scan", "pending", {"media_source_id": sourceId, "scan_mode": scanMode})
        connection.commit()
        job = _fetch_scan_job(connection, jobId)
    record_operation_log(settings, task_type="scan_job", task_id=jobId, level="info", stage="scan_queued",
                         message="扫描任务已受理，等待后台执行")
    return job


def getScan(settings: AppSettings, jobId: int):
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _fetch_scan_job(connection, jobId)


def scanTimeoutSeconds(settings: AppSettings, jobId: int) -> int:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        row = connection.execute("SELECT policy_json FROM scan_jobs WHERE id=?", (jobId,)).fetchone()
    return int(json.loads(row[0])["task_timeout_seconds"])


def claimScan(settings: AppSettings) -> tuple[int, str] | None:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        # 业务终态不代表进程已退出，所有未释放的执行令牌都占用槽位。
        if connection.execute("SELECT 1 FROM scan_jobs WHERE execution_token IS NOT NULL LIMIT 1").fetchone():
            return None
        row = connection.execute("SELECT * FROM scan_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        token = secrets.token_urlsafe(24)
        connection.execute("UPDATE scan_jobs SET status='running', execution_token=?, execution_expires_at=?, started_at=? "
                           "WHERE id=? AND status='queued'", (token, _expiry(), _now(), row["id"]))
        _audit(settings, connection, row, "claim_scan", "pending", {})
        connection.commit()
        return int(row["id"]), token


def heartbeatScan(settings: AppSettings, jobId: int, token: str) -> bool:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        cursor = connection.execute("UPDATE scan_jobs SET execution_expires_at=? WHERE id=? AND execution_token=? "
                                    "AND status='running' AND execution_expires_at>?", (_expiry(), jobId, token, _now()))
        connection.commit()
        return cursor.rowcount == 1


def finishInterruptedScan(settings: AppSettings, jobId: int, token: str, reason: str, *, processExited: bool) -> None:
    interrupted = False
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM scan_jobs WHERE id=? AND execution_token=?", (jobId, token)).fetchone()
        if row is None:
            return
        if row["status"] in ("running", "interrupted"):
            interrupted = True
            connection.execute(
                "UPDATE scan_jobs SET status='interrupted',error_message="
                "CASE WHEN error_message IS NULL OR TRIM(error_message)='' THEN ? "
                "WHEN INSTR(error_message, ?) > 0 THEN error_message ELSE error_message || '；' || ? END,"
                "ended_at=? WHERE id=?",
                (reason, reason, reason, _now(), jobId),
            )
            _audit(settings, connection, row, "interrupt_scan", "failed", {"reason": reason, "process_exited": processExited})
        if processExited:
            connection.execute("UPDATE scan_jobs SET execution_token=NULL, execution_expires_at=NULL,execution_pid=NULL WHERE id=?", (jobId,))
        connection.commit()
    if interrupted:
        record_operation_log(settings, task_type="scan_job", task_id=jobId, level="error", stage="scan_interrupted", message=reason)


def recoverExpiredScans(settings: AppSettings) -> int:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT * FROM scan_jobs WHERE status='running' AND execution_token IS NOT NULL "
                                  "AND execution_expires_at<=?", (_now(),)).fetchall()
        for row in rows:
            reason = "扫描执行授权过期，任务已中断；执行槽保留，需确认原进程退出"
            connection.execute(
                "UPDATE scan_jobs SET status='interrupted',error_message="
                "CASE WHEN error_message IS NULL OR TRIM(error_message)='' THEN ? "
                "WHEN INSTR(error_message, ?) > 0 THEN error_message ELSE error_message || '；' || ? END,"
                "ended_at=? WHERE id=?",
                (reason, reason, reason, _now(), row["id"]),
            )
            _audit(settings, connection, row, "expire_scan", "failed", {"process_exited": False})
        connection.commit()
        return len(rows)


def executeAcceptedScan(settings: AppSettings, jobId: int, token: str):
    from app.service.auth_service import _row_to_user
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = requireScanLease(connection, jobId, token)
        actor = None
        if row["actor_id"] is not None:
            user = connection.execute("SELECT * FROM users WHERE id=?", (row["actor_id"],)).fetchone()
            if user is None or not user["enabled"] or "scan:run" not in json.loads(user["permissions_json"]):
                raise ValueError("扫描发起用户已停用或权限被撤销")
            actor = _row_to_user(user)
        context = json.loads(row["audit_context_json"] or "{}")
    return run_full_scan(settings, row["media_source_id"], row["scan_mode"], acceptedJobId=jobId,
                         executionToken=token, auditContext=AuditWriteContext(actor, context.get("ipAddress"), context.get("userAgent")))
