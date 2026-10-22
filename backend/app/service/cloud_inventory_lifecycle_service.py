"""盘点观察生命周期：仅操作本地缓存，预算、批准记录和审计不清除。"""

import time

from app.service.audit_service import record_audit_event
from app.service.cloud_drive_budget_service import requireCloudActor
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_inventory_service import (
    CloudInventoryError, _limit, _row, cloudInventorySummary, inventoryDb, inventoryId,
)


def _owned(connection, jobId, context):
    job = _row(connection, jobId)
    if context.actor is None or job["actor_id"] != context.actor.id:
        raise CloudInventoryError("job_not_found")
    requireCloudActor(connection, job["actor_id"])
    return job


def _audit(settings, connection, job, context, action, detail):
    record_audit_event(settings, event_type="cloud_drive.inventory", action=action, result="success",
        summary="CD2本地观察维护", target_type="cloud_inventory_job", target_id=job["id"], actor=context.actor,
        detail=detail, ip_address=context.ipAddress, user_agent=context.userAgent, connection=connection)


def setCloudInventoryKeep(settings, jobId, entryId, *, kept, expectedRevision, auditContext):
    inventoryId(jobId)
    _limit(entryId, 1, 2**63 - 1)
    _limit(expectedRevision, 0, 2**63 - 1)
    if not isinstance(kept, bool):
        raise ValueError("保留状态须为布尔值")
    with inventoryDb(settings, write=True) as connection:
        requireNoDirectoryExecution(connection)
        job = _owned(connection, jobId, auditContext)
        if job["cache_state"] != "available":
            raise CloudInventoryError("observations_" + job["cache_state"])
        if job["revision"] != expectedRevision:
            raise CloudInventoryError("revision_conflict")
        if not connection.execute("SELECT 1 FROM cloud_drive_inventory_entries WHERE job_id=? AND id=?", (jobId, entryId)).fetchone():
            raise CloudInventoryError("entry_not_found")
        if kept:
            changed = connection.execute("INSERT OR IGNORE INTO cloud_drive_inventory_keeps(entry_id,job_id,created_at) VALUES(?,?,?)",
                                         (entryId, jobId, time.time())).rowcount
        else:
            changed = connection.execute("DELETE FROM cloud_drive_inventory_keeps WHERE job_id=? AND entry_id=?", (jobId, entryId)).rowcount
        if changed:
            connection.execute("UPDATE cloud_drive_inventory_jobs SET revision=revision+1,updated_at=? WHERE id=?", (time.time(), jobId))
            _audit(settings, connection, job, auditContext, "inventory_keep_set", {"entryId": entryId, "kept": kept})
    return cloudInventorySummary(settings, jobId, auditContext.actor.id)


def clearCloudInventoryCache(settings, jobId, *, expectedRevision, confirmClear, auditContext):
    """每次最多4000条，每1000条独立提交；响应丢失后先刷新摘要再显式继续。"""
    inventoryId(jobId)
    _limit(expectedRevision, 0, 2**63 - 1)
    if confirmClear is not True:
        raise ValueError("清理本地观察须显式确认")
    for _ in range(4):
        with inventoryDb(settings, write=True) as connection:
            requireNoDirectoryExecution(connection)
            job = _owned(connection, jobId, auditContext)
            if job["cache_state"] == "cleared":
                break
            if job["revision"] != expectedRevision:
                raise CloudInventoryError("revision_conflict")
            if (job["status"] not in {"completed", "cancelled", "interrupted"} or job["worker_token"] is not None
                    or connection.execute("SELECT 1 FROM cloud_drive_rpc_lease WHERE session_id=?", (job["session_id"],)).fetchone()):
                raise CloudInventoryError("cache_clear_blocked")
            if job["cache_state"] == "available":
                _audit(settings, connection, job, auditContext, "inventory_cache_clear_start",
                       {"storedEntries": job["stored_entries"], "storedBytes": job["stored_bytes"]})
            rows = connection.execute("SELECT id,logical_bytes FROM cloud_drive_inventory_entries WHERE job_id=? ORDER BY id LIMIT 1000", (jobId,)).fetchall()
            if rows:
                connection.execute("DELETE FROM cloud_drive_inventory_entries WHERE job_id=? AND id<=?", (jobId, rows[-1]["id"]))
            else:
                # 条目先删、目录后删；外键启用，保留例外随对应观察删除，审计不级联。
                connection.execute("DELETE FROM cloud_drive_inventory_directories WHERE id IN "
                    "(SELECT id FROM cloud_drive_inventory_directories WHERE job_id=? ORDER BY id LIMIT 1000)", (jobId,))
            remaining = connection.execute("SELECT 1 FROM cloud_drive_inventory_entries WHERE job_id=? LIMIT 1", (jobId,)).fetchone()
            directories = connection.execute("SELECT 1 FROM cloud_drive_inventory_directories WHERE job_id=? LIMIT 1", (jobId,)).fetchone()
            done = not remaining and not directories
            connection.execute("UPDATE cloud_drive_inventory_jobs SET cache_state=?,cleared_at=?,"
                "stored_entries=stored_entries-?,stored_bytes=stored_bytes-?,revision=revision+1,updated_at=? WHERE id=?",
                ("cleared" if done else "clearing", time.time() if done else None, len(rows),
                 sum(row["logical_bytes"] for row in rows), time.time(), jobId))
            expectedRevision += 1
            if done:
                _audit(settings, connection, job, auditContext, "inventory_cache_clear_complete",
                       {"receivedEntries": job["received_entries"], "budgetPreserved": True})
        if done:
            break
    return cloudInventorySummary(settings, jobId, auditContext.actor.id)
