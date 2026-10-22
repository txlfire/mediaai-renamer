"""只读盘点队列和有界观察；不读取凭据、不发RPC、不判定文件消失。"""

from contextlib import closing, contextmanager
from hashlib import sha256
import json
import math
import os
import sqlite3
import time
from uuid import UUID, uuid4

from app.core.config import CloudDriveSettings
from app.core.logger import get_logger
from app.service.audit_service import record_audit_event
from app.service.cloud_drive_budget_service import requireCloudActor, requireCloudSessionForWork, cloudDriveHostKey
from app.service.cloud_drive_error_codes import CD2_RPC_FAILURE_CODES
from app.service.cloud_drive_planning_service import buildCloudScanAdvice
from app.utils.cloud_drive_paths import canonicalCloudPath, isWithinCloudRoot
from app.service.cloud_directory_guard import directoryExecutionHeld, requireNoDirectoryExecution
from app.service.cloud_directory_path_service import cloudPathEpoch

MAX_CACHE_ENTRIES = 1000000
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_DIRECTORY_ROWS = 10000
_REASONS = {"quantum_boundary", "entry_budget", "byte_budget", "directory_budget", "depth_budget",
    "cache_capacity", "queue_capacity", "cancel_requested", "process_interrupted", "worker_failed",
    "session_not_active", "session_expired", "session_exhausted", "pool_not_ready", "pool_paused",
    "actor_permission_denied", "scope_revoked", "wait_timeout", "deadline_exceeded", "unavailable",
    "unauthenticated", "permission_denied", "entry_limit", "byte_limit", "message_limit",
    "duplicate_entry", "out_of_scope_or_unsupported_entry", "invalid_entry_path", "storage_unavailable",
    "scope_complete", "directory_incomplete", "worker_ownership_lost", "token_root_mismatch", "unknown_token_permissions",
    "configuration_changed", "configuration_unavailable", "configuration_unbound",
    "overprivileged_or_missing_list_permission", "invalid_runtime_version", "invalid_response", "resource_exhausted"} | CD2_RPC_FAILURE_CODES


class CloudInventoryError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"CD2盘点停止：{code}；不会自动重扫或扩大范围")
        self.code = code


def inventoryId(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("盘点标识须为规范UUID") from None
    return value


def _limit(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("盘点预算超出允许范围")


@contextmanager
def inventoryDb(settings, *, write=False):
    try:
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=" + mode,
                                    uri=True, timeout=0.2)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
                from app.service.cloud_drive_configuration_service import requireCloudConfigurationRevision
                requireCloudConfigurationRevision(settings, connection)
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                if write:
                    connection.commit()
    except (sqlite3.Error, OSError) as exc:
        code = getattr(exc, "sqlite_errorcode", None)
        get_logger(__name__).warning("CD2本地事务停止 mode=%s error_type=%s sqlite_code=%s",
                                    "write" if write else "read", type(exc).__name__, code if type(code) is int else None)
        raise CloudInventoryError("storage_unavailable") from None


def _audit(settings, connection, row, action, *, context=None, result="success"):
    actor = context.actor if context else None
    record_audit_event(settings, event_type="cloud_drive.inventory", action=action, result=result,
        summary="CD2只读盘点状态变更", target_type="cloud_inventory_job", target_id=row["id"],
        actor=actor, actor_id=row["actor_id"], actor_name=None if actor else "cloud_inventory_worker",
        detail={"status": row["status"], "reason": row["reason"], "receivedEntries": row["received_entries"],
                "storedEntries": row["stored_entries"], "completedDirectories": row["completed_directories"]},
        ip_address=context.ipAddress if context else None, user_agent=context.userAgent if context else None,
        connection=connection)


def _row(connection, jobId):
    row = connection.execute("SELECT * FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,)).fetchone()
    if row is None:
        raise CloudInventoryError("job_not_found")
    return row


def _active(connection, jobId, workerToken):
    row = _row(connection, jobId)
    if row["status"] != "running" or row["worker_token"] != workerToken or row["worker_pid"] != os.getpid():
        raise CloudInventoryError("worker_ownership_lost")
    requireCloudActor(connection, row["actor_id"])
    if row["cancel_requested"]:
        raise CloudInventoryError("cancel_requested")
    now = time.time()
    if not math.isfinite(now) or now < row["created_at"] or now >= row["expires_at"]:
        raise CloudInventoryError("deadline_exceeded")
    return row


def safeCloudInventoryReason(reason):
    return reason if isinstance(reason, str) and reason in _REASONS else "worker_failed"


def _change(settings, connection, row, status, reason, context=None):
    reason = safeCloudInventoryReason(reason)
    connection.execute("UPDATE cloud_drive_inventory_jobs SET status=?,reason=?,revision=revision+1,updated_at=? WHERE id=?",
                       (status, reason, time.time(), row["id"]))
    current = _row(connection, row["id"])
    failed = status == "interrupted" or (status == "paused" and reason != "quantum_boundary")
    _audit(settings, connection, current, "inventory_" + status, context=context, result="failed" if failed else "success")


def createCloudInventoryJob(settings, *, requestId, sessionId, selectedPaths, excludedPaths,
                            mode="overview", maxEntries=10000, maxBytes=32 * 1024 * 1024,
                            maxDirectories=200, maxDepth=8, quantumDirectories=20, auditContext):
    inventoryId(requestId)
    inventoryId(sessionId)
    for value, low, high in ((maxEntries, 1, 1000000), (maxBytes, 1024, MAX_CACHE_BYTES),
                             (maxDirectories, 1, MAX_DIRECTORY_ROWS), (maxDepth, 0, 32), (quantumDirectories, 1, 200)):
        _limit(value, low, high)
    if auditContext.actor is None:
        raise ValueError("盘点须关联已授权用户")
    with inventoryDb(settings, write=True) as connection:
        requireNoDirectoryExecution(connection)
        session = connection.execute("SELECT * FROM cloud_drive_read_sessions WHERE id=?", (sessionId,)).fetchone()
        if session is None or session["actor_id"] != auditContext.actor.id:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, session["actor_id"])
        plan = buildCloudScanAdvice(CloudDriveSettings(session["root_path"]), selectedPaths, excludedPaths, mode)
        exclusions = json.dumps(plan["excludedPaths"], ensure_ascii=False, separators=(",", ":"))
        if len(exclusions.encode()) > 65536 or len(plan["scopes"]) > maxDirectories:
            raise ValueError("范围或排除清单超过盘点预算")
        maxDepth = 0 if mode == "overview" else maxDepth
        digest = sha256(json.dumps([sessionId, plan["scopes"], exclusions, mode, maxEntries,
            maxBytes, maxDirectories, maxDepth, quantumDirectories], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        existing = connection.execute("SELECT * FROM cloud_drive_inventory_jobs WHERE request_id=?", (requestId,)).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise CloudInventoryError("request_conflict")
            return existing["id"]
        requireCloudSessionForWork(settings, connection, sessionId)
        if connection.execute("SELECT 1 FROM cloud_drive_inventory_jobs WHERE session_id=?", (sessionId,)).fetchone():
            raise CloudInventoryError("session_already_bound")
        if connection.execute("SELECT COUNT(*) FROM cloud_drive_inventory_jobs").fetchone()[0] >= 1000:
            raise CloudInventoryError("queue_capacity")
        if connection.execute("SELECT COUNT(*) FROM cloud_drive_inventory_directories").fetchone()[0] + len(plan["scopes"]) > MAX_DIRECTORY_ROWS:
            raise CloudInventoryError("queue_capacity")
        jobId = str(uuid4())
        now = time.time()
        connection.execute("INSERT INTO cloud_drive_inventory_jobs "
            "(id,request_id,request_hash,session_id,actor_id,root_path,mode,exclusions_json,max_entries,max_bytes,"
            "max_directories,max_depth,quantum_directories,status,expires_at,created_at,updated_at,selected_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)", (jobId, requestId, digest, sessionId,
                session["actor_id"], session["root_path"], mode, exclusions, maxEntries, maxBytes, maxDirectories,
                maxDepth, quantumDirectories, session["expires_at"], now, now,
                json.dumps([scope["path"] for scope in plan["scopes"]], ensure_ascii=False)))
        connection.executemany("INSERT INTO cloud_drive_inventory_directories(job_id,path,depth,status) VALUES(?,?,0,'pending')",
                               [(jobId, scope["path"]) for scope in plan["scopes"]])
        connection.execute("UPDATE cloud_drive_inventory_jobs SET path_epoch=? WHERE id=?", (cloudPathEpoch(connection)["revision"], jobId))
        _audit(settings, connection, _row(connection, jobId), "inventory_approve", context=auditContext)
        return jobId


def claimCloudInventoryJob(settings, jobId, *, actorId=None, expectedRevision=None):
    inventoryId(jobId)
    if expectedRevision is not None:
        _limit(expectedRevision, 0, 2**63 - 1)
    with inventoryDb(settings, write=True) as connection:
        requireNoDirectoryExecution(connection)
        row = _row(connection, jobId)
        if actorId is not None and row["actor_id"] != actorId:
            raise CloudInventoryError("job_not_found")
        if row["path_epoch"] != cloudPathEpoch(connection)["revision"]:
            raise CloudInventoryError("path_revision_changed")
        if expectedRevision is not None and row["revision"] != expectedRevision:
            raise CloudInventoryError("job_not_queued")
        if row["status"] != "queued" or row["cancel_requested"]:
            raise CloudInventoryError("job_not_queued")
        requireCloudSessionForWork(settings, connection, row["session_id"])
        if connection.execute("SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL").fetchone():
            raise CloudInventoryError("worker_slot_busy")
        token = str(uuid4())
        connection.execute("UPDATE cloud_drive_inventory_jobs SET status='running',reason=NULL,worker_token=?,"
            "owner_pid=?,owner_instance=?,owner_host=?,revision=revision+1,updated_at=? WHERE id=?",
            (token, os.getpid(), str(uuid4()), cloudDriveHostKey(), time.time(), jobId))
        _audit(settings, connection, _row(connection, jobId), "inventory_claim")
        return token


def persistCloudInventoryPid(settings, jobId, workerToken, pid):
    _limit(pid, 1, 2**31 - 1)
    with inventoryDb(settings, write=True) as connection:
        changed = connection.execute("UPDATE cloud_drive_inventory_jobs SET worker_pid=? WHERE id=? "
            "AND status='running' AND worker_token=? AND owner_pid=? AND worker_pid IS NULL",
            (pid, jobId, workerToken, os.getpid())).rowcount
        if changed != 1:
            raise CloudInventoryError("worker_ownership_lost")


def _beginDirectory(settings, jobId, token):
    with inventoryDb(settings, write=True) as connection:
        row = _active(connection, jobId, token)
        directory = connection.execute("SELECT * FROM cloud_drive_inventory_directories WHERE job_id=? AND status='pending' "
                                       "ORDER BY id LIMIT 1", (jobId,)).fetchone()
        if directory is None:
            if connection.execute("SELECT 1 FROM cloud_drive_inventory_directories WHERE job_id=? AND status<>'complete' LIMIT 1", (jobId,)).fetchone():
                raise CloudInventoryError("directory_incomplete")
            _change(settings, connection, row, "completed", "scope_complete")
            return None
        session = requireCloudSessionForWork(settings, connection, row["session_id"])
        # 单目录必需先验令牌再列举；已知不足时不浪费最后一次RPC。
        if session["max_rpc"] - session["used_rpc"] < 2:
            raise CloudInventoryError("session_exhausted")
        cachedEntries, cachedBytes = connection.execute("SELECT COALESCE(SUM(stored_entries),0),COALESCE(SUM(stored_bytes),0) "
                                                        "FROM cloud_drive_inventory_jobs").fetchone()
        if cachedEntries >= MAX_CACHE_ENTRIES or cachedBytes >= MAX_CACHE_BYTES or row["stored_bytes"] >= row["max_bytes"]:
            raise CloudInventoryError("cache_capacity")
        if row["received_entries"] >= row["max_entries"] or row["received_bytes"] >= row["max_bytes"]:
            raise CloudInventoryError("entry_budget" if row["received_entries"] >= row["max_entries"] else "byte_budget")
        connection.execute("UPDATE cloud_drive_inventory_directories SET status='reading' WHERE id=?", (directory["id"],))
        return dict(directory), dict(row)


class InventoryDirectorySink:
    def __init__(self, settings, jobId, token, directory):
        self.settings, self.jobId, self.token, self.directory = settings, jobId, token, directory

    def messageReceived(self, entries, byteCount):
        with inventoryDb(self.settings, write=True) as connection:
            job = _active(connection, self.jobId, self.token)
            count, size = job["received_entries"] + entries, job["received_bytes"] + byteCount
            connection.execute("UPDATE cloud_drive_inventory_jobs SET received_entries=?,received_bytes=?,updated_at=? WHERE id=?",
                               (count, size, time.time(), self.jobId))
            exceeded = "entry_budget" if count > job["max_entries"] else ("byte_budget" if size > job["max_bytes"] else None)
        # 已接收但未保存的消息仍消费预算，超限也必须先提交计数。
        if exceeded:
            raise CloudInventoryError(exceeded)

    def writeEntries(self, entries):
        if len(entries) > 128:
            raise CloudInventoryError("cache_capacity")
        with inventoryDb(self.settings, write=True) as connection:
            job = _active(connection, self.jobId, self.token)
            exclusions = json.loads(job["exclusions_json"])
            totalCount, totalBytes = connection.execute("SELECT COALESCE(SUM(stored_entries),0),COALESCE(SUM(stored_bytes),0) "
                                                       "FROM cloud_drive_inventory_jobs").fetchone()
            addedCount = addedBytes = 0
            for entry in entries:
                path = canonicalCloudPath(entry.path)
                if path.rpartition("/")[0] != self.directory["path"] or not isWithinCloudRoot(path, job["root_path"]):
                    raise CloudInventoryError("out_of_scope_or_unsupported_entry")
                if any(isWithinCloudRoot(path, excluded) for excluded in exclusions):
                    continue
                if connection.execute("SELECT 1 FROM cloud_drive_inventory_entries WHERE job_id=? AND path=?",
                                      (self.jobId, path)).fetchone():
                    raise CloudInventoryError("duplicate_entry")
                size = len(path.encode()) + len(entry.name.encode()) + len((entry.id or "").encode()) + 64
                if (totalCount + addedCount + 1 > MAX_CACHE_ENTRIES or totalBytes + addedBytes + size > MAX_CACHE_BYTES
                        or job["stored_bytes"] + addedBytes + size > job["max_bytes"]):
                    raise CloudInventoryError("cache_capacity")
                connection.execute("INSERT INTO cloud_drive_inventory_entries "
                    "(job_id,directory_id,generation,path,name,remote_id,is_directory,size,logical_bytes) VALUES(?,?,?,?,?,?,?,?,?)",
                    (self.jobId, self.directory["id"], self.directory["generation"], path, entry.name, entry.id,
                     int(entry.isDirectory), entry.size, size))
                addedCount += 1
                addedBytes += size
            connection.execute("UPDATE cloud_drive_inventory_jobs SET stored_entries=stored_entries+?,stored_bytes=stored_bytes+?,"
                "updated_at=? WHERE id=?", (addedCount, addedBytes, time.time(), self.jobId))


def _completeDirectory(settings, jobId, token, directory):
    with inventoryDb(settings, write=True) as connection:
        row = _active(connection, jobId, token)
        changed = connection.execute("UPDATE cloud_drive_inventory_directories SET status='complete' WHERE id=? AND job_id=? "
            "AND status='reading' AND generation=?", (directory["id"], jobId, directory["generation"])).rowcount
        if changed != 1:
            raise CloudInventoryError("directory_incomplete")
        connection.execute("UPDATE cloud_drive_inventory_jobs SET completed_directories=completed_directories+1 WHERE id=?", (jobId,))
        if row["mode"] == "overview":
            return
        count = connection.execute("SELECT COUNT(*) FROM cloud_drive_inventory_directories WHERE job_id=?", (jobId,)).fetchone()[0]
        globalCount = connection.execute("SELECT COUNT(*) FROM cloud_drive_inventory_directories").fetchone()[0]
        cursor = connection.execute("SELECT path FROM cloud_drive_inventory_entries WHERE directory_id=? AND generation=? "
                                    "AND is_directory=1 ORDER BY id", (directory["id"], directory["generation"]))
        for child in cursor:
            if directory["depth"] >= row["max_depth"]:
                _change(settings, connection, row, "paused", "depth_budget")
                return
            if count >= row["max_directories"] or globalCount >= MAX_DIRECTORY_ROWS:
                reason = "directory_budget" if count >= row["max_directories"] else "queue_capacity"
                _change(settings, connection, row, "paused", reason)
                return
            changed = connection.execute("INSERT OR IGNORE INTO cloud_drive_inventory_directories(job_id,path,depth,status) "
                "VALUES(?,?,?,'pending')", (jobId, child["path"], directory["depth"] + 1)).rowcount
            count += changed
            globalCount += changed


def pauseCloudInventoryExecution(settings, jobId, workerToken, reason):
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, jobId)
        if row["worker_token"] != workerToken or row["worker_pid"] != os.getpid():
            raise CloudInventoryError("worker_ownership_lost")
        connection.execute("UPDATE cloud_drive_inventory_directories SET status='partial' WHERE job_id=? AND status='reading'", (jobId,))
        if row["status"] == "running":
            _change(settings, connection, row, "cancelled" if row["cancel_requested"] else "paused", reason)


def executeCloudInventoryQuantum(settings, jobId, workerToken, client):
    """仅worker调用；所有结果分批持久化，异常时不自动重读目录。"""
    try:
        with inventoryDb(settings) as connection:
            quantum = _active(connection, jobId, workerToken)["quantum_directories"]
        for _ in range(quantum):
            prepared = _beginDirectory(settings, jobId, workerToken)
            if prepared is None:
                return
            directory, job = prepared
            remainingTime = job["expires_at"] - time.time()
            result = client.consumeDirectory(directory["path"], InventoryDirectorySink(settings, jobId, workerToken, directory),
                entryLimit=job["max_entries"] - job["received_entries"],
                byteLimit=max(1024, job["max_bytes"] - job["received_bytes"]),
                timeoutSeconds=min(60, max(0.05, remainingTime)))
            if not result.complete:
                raise CloudInventoryError(result.stopReason)
            _completeDirectory(settings, jobId, workerToken, directory)
            with inventoryDb(settings) as connection:
                if _row(connection, jobId)["status"] != "running":
                    return
        with inventoryDb(settings, write=True) as connection:
            row = _active(connection, jobId, workerToken)
            pending = connection.execute("SELECT 1 FROM cloud_drive_inventory_directories WHERE job_id=? AND status='pending' LIMIT 1", (jobId,)).fetchone()
            _change(settings, connection, row, "paused" if pending else "completed", "quantum_boundary" if pending else "scope_complete")
    except Exception as exc:
        code = getattr(exc, "code", "worker_failed")
        pauseCloudInventoryExecution(settings, jobId, workerToken, code if isinstance(code, str) else "worker_failed")
        raise


def continueCloudInventoryQuantum(settings, jobId, *, auditContext, expectedRevision, confirmContinue):
    inventoryId(jobId)
    _limit(expectedRevision, 0, 2**63 - 1)
    if confirmContinue is not True or auditContext.actor is None:
        raise ValueError("继续量子须显式确认并关联用户")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, jobId)
        if row["actor_id"] != auditContext.actor.id:
            raise CloudInventoryError("job_not_found")
        requireCloudSessionForWork(settings, connection, row["session_id"])
        if (row["status"] != "paused" or row["reason"] != "quantum_boundary" or row["worker_token"] is not None
                or row["cancel_requested"] or row["revision"] != expectedRevision):
            raise CloudInventoryError("job_not_resumable")
        connection.execute("UPDATE cloud_drive_inventory_jobs SET status='queued',reason=NULL,revision=revision+1,updated_at=? WHERE id=?", (time.time(), jobId))
        _audit(settings, connection, _row(connection, jobId), "inventory_continue", context=auditContext)


def cloudInventorySummary(settings, jobId, actorId):
    inventoryId(jobId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        row = _row(connection, jobId)
        if row["actor_id"] != actorId:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, actorId)
        counts = dict(connection.execute("SELECT status,COUNT(*) FROM cloud_drive_inventory_directories WHERE job_id=? GROUP BY status", (jobId,)))
        selected = json.loads(row["selected_json"])
        rpcHeld = bool(connection.execute("SELECT 1 FROM cloud_drive_rpc_lease WHERE session_id=?", (row["session_id"],)).fetchone())
        directoryHeld = directoryExecutionHeld(connection)
        pathCurrent = row["path_epoch"] == cloudPathEpoch(connection)["revision"]
        budget = connection.execute("SELECT max_rpc,used_rpc,status,expires_at FROM cloud_drive_read_sessions WHERE id=?", (row["session_id"],)).fetchone()
    return {"id": jobId, "status": row["status"], "reason": row["reason"], "revision": row["revision"],
        "mode": row["mode"], "approvedRoot": row["root_path"], "cancelRequested": bool(row["cancel_requested"]),
        "selectedPaths": selected, "excludedPaths": json.loads(row["exclusions_json"]), "freshness": "unknown",
        "receivedEntries": row["received_entries"], "receivedBytes": row["received_bytes"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"], "storedBytes": row["stored_bytes"],
        "remainingEntries": max(0, row["max_entries"] - row["received_entries"]),
        "remainingReceiveBytes": max(0, row["max_bytes"] - row["received_bytes"]),
        "budget": {"maxRpc": budget["max_rpc"], "usedRpc": budget["used_rpc"],
            "remainingRpc": max(0, budget["max_rpc"] - budget["used_rpc"]), "status": budget["status"],
            "expiresAt": budget["expires_at"], "expired": time.time() >= budget["expires_at"]},
        "storedEntries": row["stored_entries"], "completedDirectories": row["completed_directories"],
        "directoryStates": counts, "estimatedTotalEntries": None,
        "limits": {"entries": row["max_entries"], "bytes": row["max_bytes"], "directories": row["max_directories"], "depth": row["max_depth"]},
        "scopeComplete": row["status"] == "completed", "workerSlotHeld": row["worker_token"] is not None,
        "cacheState": row["cache_state"], "clearedAt": row["cleared_at"],
        "pathCurrent": pathCurrent,
        "cacheClearAvailable": row["status"] in {"completed", "cancelled", "interrupted"}
            and row["worker_token"] is None and not rpcHeld and not directoryHeld and row["cache_state"] != "cleared",
        "writeAvailable": False, "executionAvailable": False}


def cloudInventoryJobs(settings, actorId, *, page=1, pageSize=20):
    _limit(page, 1, 1000)
    _limit(pageSize, 1, 50)
    with inventoryDb(settings) as connection:
        requireCloudActor(connection, actorId)
        total = connection.execute("SELECT COUNT(*) FROM cloud_drive_inventory_jobs WHERE actor_id=?", (actorId,)).fetchone()[0]
        rows = connection.execute("SELECT id,root_path,mode,status,reason,stored_entries,created_at FROM cloud_drive_inventory_jobs "
            "WHERE actor_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (actorId, pageSize, (page - 1) * pageSize)).fetchall()
    return {"items": [{"id": row["id"], "approvedRoot": row["root_path"], "mode": row["mode"], "status": row["status"],
        "reason": row["reason"], "storedEntries": row["stored_entries"], "createdAt": row["created_at"]} for row in rows],
        "total": total, "page": page, "pageSize": pageSize, "networkAccess": False}


def cloudInventoryEntries(settings, jobId, actorId, *, afterId=0, limit=100):
    _limit(afterId, 0, 2**63 - 1)
    _limit(limit, 1, 200)
    inventoryId(jobId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        job = _row(connection, jobId)
        if job["actor_id"] != actorId:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, actorId)
        if job["cache_state"] != "available":
            raise CloudInventoryError("observations_" + job["cache_state"])
        rows = connection.execute("SELECT e.*,d.status AS directory_status,d.generation AS directory_generation "
            "FROM cloud_drive_inventory_entries e JOIN cloud_drive_inventory_directories d ON d.id=e.directory_id "
            "WHERE e.job_id=? AND e.id>? ORDER BY e.id LIMIT ?", (jobId, afterId, limit + 1)).fetchall()
    return {"items": [{"id": row["id"], "name": row["name"], "relativePath": row["path"][len(job["root_path"]) + 1:],
                       "isDirectory": bool(row["is_directory"]), "size": row["size"], "directoryComplete": row["directory_status"] == "complete"}
                      for row in rows[:limit]], "hasMore": len(rows) > limit,
            "nextId": rows[min(limit, len(rows)) - 1]["id"] if rows else afterId, "networkAccess": False}


def cloudInventoryDirectories(settings, jobId, actorId, *, afterId=0, limit=50):
    _limit(afterId, 0, 2**63 - 1)
    _limit(limit, 1, 200)
    inventoryId(jobId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        job = _row(connection, jobId)
        if job["actor_id"] != actorId:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, actorId)
        if job["cache_state"] != "available":
            raise CloudInventoryError("observations_" + job["cache_state"])
        rows = connection.execute("SELECT id,path,depth,status FROM cloud_drive_inventory_directories "
            "WHERE job_id=? AND id>? ORDER BY id LIMIT ?", (jobId, afterId, limit + 1)).fetchall()
    return {"items": [{"id": row["id"], "relativePath": "" if row["path"] == job["root_path"] else row["path"][len(job["root_path"]) + 1:],
            "depth": row["depth"], "status": row["status"]} for row in rows[:limit]],
        "hasMore": len(rows) > limit, "nextId": rows[min(limit, len(rows)) - 1]["id"] if rows else afterId,
        "jobRevision": job["revision"], "networkAccess": False}


def cancelCloudInventoryJob(settings, jobId, *, auditContext):
    inventoryId(jobId)
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, jobId)
        if auditContext.actor is None or row["actor_id"] != auditContext.actor.id:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, row["actor_id"])
        if row["status"] not in {"completed", "cancelled"}:
            connection.execute("UPDATE cloud_drive_inventory_jobs SET cancel_requested=1,revision=revision+1 WHERE id=?", (jobId,))
            if row["worker_token"] is None:
                _change(settings, connection, row, "cancelled", "cancel_requested", auditContext)
            else:
                _audit(settings, connection, _row(connection, jobId), "inventory_cancel_requested", context=auditContext)
    return cloudInventorySummary(settings, jobId, auditContext.actor.id)
