"""隔离动作预算、归属及RPC事实；不连接云端、不领取旧任务或重放请求。"""

import json
import os
import time
import unicodedata
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service import cloud_quarantine_plan_service as plans
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_directory_plan_service import _permission
from app.service.cloud_drive_budget_service import _pool, _wallNow, cloudDriveHostKey
from app.service.cloud_inventory_service import inventoryDb, inventoryId, _limit
from app.service.file_task_queue_service import TASKS

QuarantineError = plans.QuarantineError
METHODS = {"GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath", "MoveFile"}
MAX_BYTES = 1024 * 1024
DURATION_SECONDS = 180


class QuarantineSpacingWait(QuarantineError):
    def __init__(self, seconds):
        super().__init__("spacing_wait_required")
        self.retryAfterSeconds = seconds


def _row(connection, runId):
    inventoryId(runId)
    row = connection.execute("SELECT * FROM cloud_quarantine_runs WHERE id=?", (runId,)).fetchone()
    if row is None:
        raise QuarantineError("not_found")
    return row


def _owned(connection, runId, actorId):
    plans._permissionForPlan(connection, actorId)
    row = _row(connection, runId)
    if row["actor_id"] != actorId:
        raise QuarantineError("not_found")
    return row


def _executePermission(connection, actorId):
    plans._permissionForPlan(connection, actorId)
    try:
        _permission(connection, actorId, "cloud-quarantine:execute")
    except plans.CloudDirectoryError:
        raise QuarantineError("permission_denied") from None


def _worker(connection, runId, token):
    row = _row(connection, runId)
    if (row["worker_token"] != token or row["worker_pid"] != os.getpid()
            or row["owner_host"] != cloudDriveHostKey() or not row["slot_held"]):
        raise QuarantineError("worker_ownership_lost")
    return row


def _snapshot(connection, row):
    plan = connection.execute("SELECT * FROM cloud_quarantine_plans WHERE id=?", (row["plan_id"],)).fetchone()
    if plan is None:
        raise QuarantineError("not_found")
    return plan, json.loads(plan["snapshot_json"])


def _active(settings, connection, row, now):
    _executePermission(connection, row["actor_id"])
    if row["kind"] == "observe":
        from app.service.cloud_quarantine_recovery_service import requireObservationActive
        return requireObservationActive(settings, connection, row, now)
    if not settings.cloudDrive.quarantineEnabled or row["policy_hash"] != plans.quarantinePolicyHash(settings):
        raise QuarantineError("execution_policy_changed")
    if row["cancel_requested"]:
        raise QuarantineError("cancel_requested")
    if row["status"] != "running" or now < row["created_at"] or now >= row["expires_at"]:
        raise QuarantineError("execution_not_active")
    if row["kind"] != "move":
        raise QuarantineError("rpc_not_allowed")
    if connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE original_run_id=? LIMIT 1", (row["id"],)).fetchone():
        raise QuarantineError("execution_handed_to_recovery")
    plan, snapshot = _snapshot(connection, row)
    reason = plans._valid(connection, settings, plan, snapshot, ignoreExecution=True)
    if reason:
        raise QuarantineError(reason)


def _audit(settings, connection, row, action, context=None, detail=None):
    record_audit_event(settings, event_type="cloud_quarantine.execution", action=action, result="pending",
        summary="受控隔离状态变更；未知不重放", target_type="cloud_quarantine_run", target_id=row["id"],
        actor=context.actor if context else None, actor_id=row["actor_id"],
        actor_name=None if context else "cloud_quarantine_worker", detail=detail,
        ip_address=context.ipAddress if context else None, user_agent=context.userAgent if context else None, connection=connection)
    from app.service.cloud_quarantine_log_service import recordQuarantineLog
    recordQuarantineLog(settings,row["id"],action,connection=connection)


def _requireOtherWorkIdle(connection):
    for table, _, _, _ in TASKS.values():
        if connection.execute(f"SELECT 1 FROM {table} WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1").fetchone():
            raise QuarantineError("file_task_busy")
    checks = (
        ("scan_slot_busy", "SELECT 1 FROM scan_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1"),
        ("inventory_slot_busy", "SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL LIMIT 1"),
        ("read_rpc_unresolved", "SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1"),
        ("file_probe_unresolved", "SELECT 1 FROM cloud_directory_probe_leases LIMIT 1"),
        ("file_action_unresolved", "SELECT 1 FROM local_file_actions WHERE status IN ('prepared','applied','recovery_required') LIMIT 1"),
        ("file_action_unresolved", "SELECT 1 FROM remote_operation_items WHERE status NOT IN ('completed','failed') LIMIT 1"),
        ("file_action_unresolved", "SELECT 1 FROM remote_operation_locks WHERE status='active' LIMIT 1"),
        ("background_work_busy", "SELECT 1 FROM background_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1"))
    for code, query in checks:
        if connection.execute(query).fetchone():
            raise QuarantineError(code)


def approveQuarantineRun(settings, planId, *, requestId, expectedRevision, policyHash,
                         confirmMaintenance, confirmGlobalFence, auditContext):
    inventoryId(requestId)
    _limit(expectedRevision, 0, 2**63-1)
    if confirmMaintenance is not True or confirmGlobalFence is not True or auditContext.actor is None:
        raise ValueError("须确认维护窗口、单写者及全实例旧计划失效")
    if not settings.cloudDrive.quarantineEnabled:
        raise QuarantineError("execution_disabled")
    actorId = auditContext.actor.id
    with inventoryDb(settings, write=True) as connection:
        plan = plans._owned(connection, planId, actorId)
        _executePermission(connection, actorId)
        if connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE request_id=? OR (plan_id=? AND kind='move')",
                              (requestId, planId)).fetchone():
            raise QuarantineError("execution_already_accepted")
        if plan["revision"] != expectedRevision or policyHash != plan["policy_hash"]:
            raise QuarantineError("revision_or_policy_changed")
        snapshot = json.loads(plan["snapshot_json"])
        reason = plans._valid(connection, settings, plan, snapshot)
        if reason:
            raise QuarantineError(reason)
        requireNoDirectoryExecution(connection)
        _requireOtherWorkIdle(connection)
        now = _wallNow()
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready" or now < pool["cooldown_until"]:
            raise QuarantineError("pool_not_ready")
        maxRpc = 3 + 9 * len(snapshot["items"])
        expires = min(now + DURATION_SECONDS, plan["expires_at"])
        if maxRpc * pool["min_interval"] + 15 > expires - now:
            raise QuarantineError("batch_exceeds_time_budget")
        runId, token = str(uuid4()), str(uuid4())
        logId = connection.execute("SELECT COALESCE(MAX(log_id),0)+1 FROM cloud_quarantine_runs").fetchone()[0]
        connection.execute("INSERT INTO cloud_quarantine_runs "
            "(id,request_id,plan_id,actor_id,kind,policy_hash,status,worker_token,owner_pid,owner_host,max_rpc,max_bytes,log_id,created_at,expires_at,updated_at) "
            "VALUES(?,?,?,?,'move',?,'accepted',?,?,?,?,?,?,?,?,?)",
            (runId, requestId, planId, actorId, policyHash, token, os.getpid(), cloudDriveHostKey(), maxRpc, MAX_BYTES, logId, now, expires, now))
        _audit(settings, connection, _row(connection, runId), "approve", auditContext,
            {"itemCount": len(snapshot["items"]), "maxRpc": maxRpc, "atomicIdentityGuaranteed": False})
        return runId, token


def bindQuarantineWorker(settings, runId, token, pid):
    _limit(pid, 1, 2**31-1)
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, runId)
        if (row["worker_token"] != token or row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey()
                or row["status"] != "accepted" or row["worker_pid"] is not None or not row["slot_held"]):
            raise QuarantineError("worker_ownership_lost")
        _active(settings, connection, {**dict(row), "status": "running"}, _wallNow())
        connection.execute("UPDATE cloud_quarantine_runs SET worker_pid=?,status='running',revision=revision+1,updated_at=? WHERE id=?",
            (pid, time.time(), runId))


def itemPaths(snapshot, ordinal):
    _limit(ordinal, 1, len(snapshot["items"]))
    item = snapshot["items"][ordinal-1]
    prefix = snapshot["parentPath"] + "/"
    if not item["sourcePath"].startswith(prefix) or not item["targetPath"].startswith(prefix):
        raise QuarantineError("path_not_in_plan")
    source = "/" + item["sourcePath"][len(prefix):]
    target = "/" + item["targetPath"][len(prefix):]
    return item, source, target, target.rpartition("/")[0] or "/"


def itemParents(snapshot, ordinal):
    _, source, target, _ = itemPaths(snapshot, ordinal)
    return source.rpartition("/")[0] or "/", target.rpartition("/")[0] or "/"


def reserveQuarantineRpc(settings, runId, token, method, *, itemOrdinal=None, requestPath=None):
    if not isinstance(method, str) or method not in METHODS:
        raise QuarantineError("rpc_not_allowed")
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, runId, token)
        now = _wallNow()
        _active(settings, connection, row, now)
        if row["kind"] == "observe" and method == "MoveFile":
            raise QuarantineError("rpc_not_allowed")
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready" or now < pool["cooldown_until"]:
            raise QuarantineError("pool_not_ready")
        unresolved = connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE phase IN ('sending','unknown') "
            "AND reviewed_by_run IS NULL AND (?='move' OR run_id=?) LIMIT 1", (row["kind"], runId)).fetchone()
        if connection.execute("SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1").fetchone() or unresolved:
            raise QuarantineError("rpc_unresolved")
        if row["used_rpc"] >= row["max_rpc"] or row["received_bytes"] >= row["max_bytes"]:
            raise QuarantineError("execution_budget_exhausted")
        _, snapshot = _snapshot(connection, row)
        paths = {"/"}
        if itemOrdinal is not None:
            _, source, target, folder = itemPaths(snapshot, itemOrdinal)
            paths.update((source, target, folder))
            paths.update(itemParents(snapshot, itemOrdinal))
        else:
            paths.add("/" + snapshot["targetName"])
        if method in {"FindFileByPath", "MoveFile"}:
            if requestPath not in paths:
                raise QuarantineError("path_not_in_plan")
            if method == "MoveFile":
                if itemOrdinal is None:
                    raise QuarantineError("identity_precheck_required")
                _requireMoveProof(connection, row, snapshot, itemOrdinal)
                if requestPath != source:
                    raise QuarantineError("path_not_in_plan")
        elif requestPath is not None or itemOrdinal is not None:
            raise QuarantineError("rpc_not_allowed")
        if pool["last_started_at"] is not None and now < pool["last_started_at"] + pool["min_interval"]:
            raise QuarantineSpacingWait(pool["last_started_at"] + pool["min_interval"] - now)
        rpcId = str(uuid4())
        connection.execute("INSERT INTO cloud_quarantine_rpcs (id,run_id,ordinal,method,request_path,item_ordinal,phase,sent_at) "
            "VALUES(?,?,?,?,?,?,'sending',?)", (rpcId, runId, row["used_rpc"]+1, method, requestPath, itemOrdinal, now))
        if method == "MoveFile":
            connection.execute("UPDATE cloud_quarantine_outcomes SET state='sending',move_rpc_id=? WHERE run_id=? AND ordinal=?",
                (rpcId, runId, itemOrdinal))
        connection.execute("UPDATE cloud_quarantine_runs SET used_rpc=used_rpc+1,revision=revision+1,updated_at=? WHERE id=?", (now, runId))
        connection.execute("UPDATE cloud_drive_budget_pool SET last_started_at=? WHERE id=1", (now,))
        return rpcId


def _identity(value, path):
    if (not isinstance(value, dict) or set(value) != {"objectId", "virtualPath", "name", "isDirectory", "size"}
            or not isinstance(value["objectId"], str) or not value["objectId"] or len(value["objectId"].encode()) > 1024
            or any(unicodedata.category(c).startswith("C") for c in value["objectId"])
            or value["virtualPath"] != path or type(value["isDirectory"]) is not bool
            or type(value["size"]) is not int or not 0 <= value["size"] <= 2**63-1
            or not isinstance(value["name"], str) or len(value["name"]) > 240
            or any(unicodedata.category(c).startswith("C") for c in value["name"])
            or (path != "/" and value["name"] != path.rpartition("/")[2])):
        raise ValueError("对象响应须保存精确已校验身份")
    return value


def recordQuarantineResponse(settings, runId, token, rpcId, *, responseCode, receivedBytes, identity=None):
    if responseCode not in {"ok", "not_found", "rejected"}:
        raise ValueError("响应须使用固定状态码")
    _limit(receivedBytes, 0, 2**31-1)
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, runId, token)
        rpc = connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE id=? AND run_id=? AND phase='sending'", (rpcId, runId)).fetchone()
        if rpc is None:
            raise QuarantineError("rpc_state_changed")
        if rpc["method"] == "FindFileByPath" and responseCode == "ok":
            _identity(identity, rpc["request_path"])
        elif identity is not None:
            raise ValueError("此响应不应附对象身份")
        now = _wallNow()
        exceeded = row["received_bytes"] + receivedBytes > row["max_bytes"]
        connection.execute("UPDATE cloud_quarantine_rpcs SET phase='responded',response_code=?,received_bytes=?,identity_json=?,responded_at=? WHERE id=?",
            (responseCode, receivedBytes, plans.reviews._json(identity) if identity else None, now, rpcId))
        if rpc["method"] == "MoveFile":
            changed = connection.execute("UPDATE cloud_quarantine_outcomes SET state='responded',remote_reply=? "
                "WHERE run_id=? AND ordinal=? AND state='sending' AND move_rpc_id=?",
                (1 if responseCode == "ok" else 0, runId, rpc["item_ordinal"], rpcId)).rowcount
            if changed != 1:
                raise QuarantineError("item_state_changed")
        connection.execute("UPDATE cloud_quarantine_runs SET received_bytes=received_bytes+?,"
            "status=CASE WHEN ? THEN 'review' ELSE status END,reason=CASE WHEN ? THEN 'byte_limit' ELSE reason END,"
            "revision=revision+1,updated_at=? WHERE id=?", (receivedBytes, exceeded, exceeded, now, runId))


def _pauseUnknown(settings, connection, now):
    pool = _pool(settings, connection, now)
    connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason=CASE WHEN pause_reason='manual_pause' "
        "THEN pause_reason ELSE 'request_incomplete' END,cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1",
        (now + pool["failure_cooldown"], now))


def markQuarantineUnknown(settings, runId, token, rpcId):
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, runId, token)
        if connection.execute("UPDATE cloud_quarantine_rpcs SET phase='unknown' WHERE id=? AND run_id=? AND phase='sending'", (rpcId, runId)).rowcount != 1:
            raise QuarantineError("rpc_state_changed")
        now = _wallNow()
        connection.execute("UPDATE cloud_quarantine_runs SET status='review',reason='rpc_unknown',revision=revision+1,updated_at=? WHERE id=?", (now, runId))
        connection.execute("UPDATE cloud_quarantine_outcomes SET state='review',reason='rpc_unknown' WHERE run_id=? AND move_rpc_id=?", (runId, rpcId))
        _pauseUnknown(settings, connection, now)
        _audit(settings, connection, row, "rpc_unknown", detail={"rpcId": rpcId, "replayed": False})


def _requireMoveProof(connection, row, snapshot, ordinal):
    from app.service.cloud_quarantine_item_service import requireMoveProof
    requireMoveProof(connection, row, snapshot, ordinal)


def _view(connection, row):
    events = connection.execute("SELECT ordinal,method,request_path,item_ordinal,phase,response_code,received_bytes "
        "FROM cloud_quarantine_rpcs WHERE run_id=? ORDER BY ordinal LIMIT 48", (row["id"],)).fetchall()
    outcomes = connection.execute("SELECT ordinal,state,reason FROM cloud_quarantine_outcomes WHERE run_id=? ORDER BY ordinal", (row["id"],)).fetchall()
    latest = connection.execute("SELECT request_id FROM cloud_quarantine_runs WHERE original_run_id=? ORDER BY attempt DESC LIMIT 1", (row["id"],)).fetchone()
    return {"id": row["id"], "planId": row["plan_id"], "requestId": row["request_id"], "kind": row["kind"],
        "originalRunId":row["original_run_id"],"attempt":row["attempt"],"committedEpoch":row["committed_epoch"],
        "latestObservationRequestId":latest[0] if latest else None,
        "positions":json.loads(row["positions_json"] or "[]"),
        "status": row["status"], "reason": row["reason"], "revision": row["revision"], "cancelRequested": bool(row["cancel_requested"]),
        "globalSlotHeld": bool(row["slot_held"]), "workerSlotHeld": row["worker_pid"] is not None,
        "usedRpc": row["used_rpc"], "maxRpc": row["max_rpc"], "receivedBytes": row["received_bytes"], "maxBytes": row["max_bytes"],
        "createdAt": row["created_at"], "expiresAt": row["expires_at"], "logId": row["log_id"],
        "logAvailable":connection.execute("SELECT 1 FROM operation_logs WHERE task_type='cloud_quarantine_run' AND task_id=? LIMIT 1", (row["log_id"],)).fetchone() is not None,
        "items":[{"ordinal":r["ordinal"],"state":r["state"],"reason":r["reason"]} for r in outcomes],
        "events": [{"ordinal": r["ordinal"], "method": r["method"], "itemOrdinal": r["item_ordinal"], "phase": r["phase"],
            "responseCode": r["response_code"], "receivedBytes": r["received_bytes"]} for r in events],
        "networkAccess": False, "replayed": False,
        "pollingRecommended": row["status"] in {"accepted", "running"} and time.time() < row["expires_at"]}


def getQuarantineRun(settings, runId, actorId, *, byRequest=False):
    inventoryId(runId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        plans._permissionForPlan(connection, actorId)
        if byRequest:
            found = connection.execute("SELECT id FROM cloud_quarantine_runs WHERE request_id=? AND actor_id=?", (runId, actorId)).fetchone()
            if found is None:
                raise QuarantineError("not_found")
            runId = found[0]
        return _view(connection, _owned(connection, runId, actorId))


def requestQuarantineCancel(settings, runId, *, expectedRevision, actorId, auditContext=None):
    _limit(expectedRevision, 0, 2**63-1)
    with inventoryDb(settings, write=True) as connection:
        row = _owned(connection, runId, actorId)
        if row["revision"] != expectedRevision or (row["status"] not in {"accepted", "running"}
                and not (row["kind"] == "observe" and row["reason"] == "positions_observed" and row["committed_epoch"] is None)):
            raise QuarantineError("revision_conflict")
        connection.execute("UPDATE cloud_quarantine_runs SET cancel_requested=1,revision=revision+1,updated_at=? WHERE id=?", (time.time(), runId))
        _audit(settings, connection, row, "cancel_requested",auditContext)
        return _view(connection, _row(connection, runId))
