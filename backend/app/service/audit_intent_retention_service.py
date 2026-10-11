"""审计辅助记录有界清理；未完成或未知记录保留，容量不足在副作用前拒绝。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3

from app.service.settings_service import get_effective_settings


def _usage(connection):
    # UTF-8负载加固定行开销仅为逻辑估算，不等于SQLite/WAL文件物理大小。
    rows = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(256+length(CAST(context_json AS BLOB))+"
        "COALESCE(length(CAST(outcome_json AS BLOB)),0)),0) FROM file_action_audit_intents "
        "UNION ALL SELECT COUNT(*),COALESCE(SUM(256+length(CAST(snapshot_json AS BLOB))+"
        "length(CAST(context_json AS BLOB))+COALESCE(length(CAST(outcome_json AS BLOB)),0)),0) "
        "FROM external_submission_authorizations"
    ).fetchall()
    return sum(row[0] for row in rows), sum(row[1] for row in rows)


def ensureAuditIntentCapacity(settings, connection, payloadBytes: int = 0):
    """必须处于业务写事务内，不能由独立连接检查后再插入。"""
    if not connection.in_transaction:
        raise RuntimeError("审计容量检查需要业务事务")
    effective = get_effective_settings(settings)
    count, size = _usage(connection)
    if (count + 1 > int(effective["audit_intents.max_records"])
            or size + max(0, payloadBytes) + 256 > int(effective["audit_intents.max_total_mb"]) * 1024 * 1024):
        raise ValueError("审计辅助记录容量已满，操作尚未执行；请清理已完成记录或核查未完成记录")


def getAuditIntentCapacity(settings) -> dict:
    effective = get_effective_settings(settings)
    maxRecords = int(effective["audit_intents.max_records"])
    maxBytes = int(effective["audit_intents.max_total_mb"]) * 1024 * 1024
    with closing(sqlite3.connect(settings.database_path)) as connection:
        records, approxBytes = _usage(connection)
        statuses = dict(connection.execute(
            "SELECT status,COUNT(*) FROM file_action_audit_intents GROUP BY status"
        ).fetchall())
    recordRatio = records / maxRecords if maxRecords else 1.0
    byteRatio = approxBytes / maxBytes if maxBytes else 1.0
    ratio = max(recordRatio, byteRatio)
    return {
        "records": records,
        "max_records": maxRecords,
        "approx_bytes": approxBytes,
        "max_bytes": maxBytes,
        "usage_ratio": min(1.0, ratio),
        "warning": ratio >= 0.8,
        "capacity_exhausted": ratio >= 1.0,
        "pending": int(statuses.get("pending", 0)),
        "review_required": int(statuses.get("review_required", 0)),
        "manually_closed": int(statuses.get("manually_closed", 0)),
    }


def cleanupAuditIntentRecords(settings):
    effective = get_effective_settings(settings)
    maxRecords = int(effective["audit_intents.max_records"])
    maxBytes = int(effective["audit_intents.max_total_mb"]) * 1024 * 1024
    budget = min(1000, int(effective["operation_logs.cleanup_batch_size"]))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=int(effective["logging.retention_days"]))).isoformat()
    deleted = 0
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        count, size = _usage(connection)
        pressure = count >= maxRecords or size >= maxBytes
        rows = connection.execute(
            "SELECT 'grant',id,created_at FROM external_submission_authorizations "
            "WHERE (status='prepared' AND expires_at<?) OR (status='completed' "
            "AND json_extract(CASE WHEN json_valid(outcome_json) THEN outcome_json ELSE '{}' END,'$.status') IN ('completed','failed') "
            "AND (? OR completed_at<?)) "
            "UNION ALL SELECT 'file',id,created_at FROM file_action_audit_intents "
            "WHERE status IN ('completed','manually_closed') AND (? OR updated_at<?) ORDER BY 3,2 LIMIT ?",
            (now.isoformat(), pressure, cutoff, pressure, cutoff, budget),
        ).fetchall()
        # 白名单决定表名；未知结果与待补审计从不进入删除集合。
        for kind, recordId, _ in rows:
            table = "external_submission_authorizations" if kind == 'grant' else "file_action_audit_intents"
            connection.execute(f"DELETE FROM {table} WHERE id=?", (recordId,))
            deleted += 1
        connection.commit()
        count, size = _usage(connection)
    return {"deleted": deleted, "records": count, "approx_bytes": size,
            "capacity_exhausted": count >= maxRecords or size >= maxBytes, "budget_exhausted": deleted == budget}
