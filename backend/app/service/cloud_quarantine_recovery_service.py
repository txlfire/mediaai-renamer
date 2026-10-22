"""新只读预算、当前位置提交和反向新计划；不重放原动作、不伪造未知回执。"""

from dataclasses import replace
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

from app.service import cloud_quarantine_execution_service as actions
from app.service import cloud_quarantine_plan_service as plans
from app.service.cloud_directory_path_service import cloudPathEpoch, advanceCloudPathEpoch
from app.service.cloud_drive_budget_service import _pool, _wallNow, cloudDriveHostKey
from app.service.cloud_inventory_service import inventoryDb, inventoryId, _limit
from app.service.cloud_quarantine_item_service import _object, _missing
from app.service.scan_recovery_service import isProcessAlive

QuarantineError = plans.QuarantineError
READ_METHODS = frozenset({"GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath"})
OBSERVE_SECONDS = 90


def recoveryPolicyHash(settings):
    return plans.quarantinePolicyHash(replace(settings, cloudDrive=replace(settings.cloudDrive, quarantineEnabled=True)))


def _original(connection, runId, actorId):
    row = actions._owned(connection, runId, actorId)
    actions._executePermission(connection, actorId)
    if row["kind"] != "move" or not row["slot_held"] or row["owner_host"] != cloudDriveHostKey():
        raise QuarantineError("recovery_not_required_or_not_local")
    if row["worker_pid"] is not None or row["status"] in {"accepted", "running"}:
        raise QuarantineError("worker_exit_unconfirmed")
    return row


def approveQuarantineObservation(settings, originalId, *, requestId, expectedRevision, confirmMaintenance, confirmRemoteIdle, auditContext):
    inventoryId(requestId)
    _limit(expectedRevision, 0, 2**63-1)
    if confirmMaintenance is not True or confirmRemoteIdle is not True or auditContext.actor is None:
        raise ValueError("须确认维护窗口、旧进程退出及原请求相关远端已静止")
    with inventoryDb(settings, write=True) as connection:
        original = _original(connection, originalId, auditContext.actor.id)
        if original["revision"] != expectedRevision:
            raise QuarantineError("revision_conflict")
        if original["policy_hash"] != recoveryPolicyHash(settings):
            raise QuarantineError("execution_policy_changed")
        if connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE request_id=?", (requestId,)).fetchone():
            raise QuarantineError("execution_already_accepted")
        if (connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE id<>? AND (slot_held=1 OR worker_pid IS NOT NULL) LIMIT 1", (originalId,)).fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_executions WHERE slot_held=1 LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1").fetchone()):
            raise QuarantineError("recovery_slot_busy")
        actions._requireOtherWorkIdle(connection)
        attempt = connection.execute("SELECT COUNT(*)+1 FROM cloud_quarantine_runs WHERE original_run_id=?", (originalId,)).fetchone()[0]
        if attempt > 5:
            raise QuarantineError("recovery_attempt_limit")
        plan, snapshot = actions._snapshot(connection, original)
        if sha256(plan["snapshot_json"].encode()).hexdigest() != plan["snapshot_hash"]:
            raise QuarantineError("observation_changed")
        now = _wallNow()
        pool = _pool(settings, connection, now)
        if now < pool["cooldown_until"] or (pool["state"] != "ready" and pool["pause_reason"] != "request_incomplete"):
            raise QuarantineError("pool_not_ready")
        maxRpc = 6+2*len(snapshot["items"])
        if maxRpc*pool["min_interval"]+15 > OBSERVE_SECONDS:
            raise QuarantineError("batch_exceeds_time_budget")
        runId, token = str(uuid4()), str(uuid4())
        logId = connection.execute("SELECT COALESCE(MAX(log_id),0)+1 FROM cloud_quarantine_runs").fetchone()[0]
        connection.execute("INSERT INTO cloud_quarantine_runs "
            "(id,request_id,plan_id,actor_id,kind,original_run_id,attempt,policy_hash,status,worker_token,owner_pid,owner_host,"
            "max_rpc,max_bytes,log_id,basis_revision,basis_epoch,created_at,expires_at,updated_at) "
            "VALUES(?,?,?,?,'observe',?,?,?,'accepted',?,?,?,?,?,?,?,?,?,?,?)",
            (runId, requestId, original["plan_id"], original["actor_id"], originalId, attempt, original["policy_hash"], token,
             os.getpid(), cloudDriveHostKey(), maxRpc, actions.MAX_BYTES, logId, original["revision"], cloudPathEpoch(connection)["revision"], now, now+OBSERVE_SECONDS, now))
        if pool["state"] != "ready":
            connection.execute("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL,revision=revision+1,updated_at=? WHERE id=1", (now,))
        actions._audit(settings, connection, actions._row(connection, runId), "observe_approved", auditContext,
            {"newReadBudget": True, "attempt": attempt, "remoteIdleConfirmed": True, "originalOutcomeRewritten": False})
        return runId, token


def requireObservationActive(settings, connection, row, now):
    original = _original(connection, row["original_run_id"], row["actor_id"])
    if (row["status"] != "running" or row["cancel_requested"] or not row["created_at"] <= now < row["expires_at"]
            or row["basis_revision"] != original["revision"] or row["basis_epoch"] != cloudPathEpoch(connection)["revision"]
            or row["policy_hash"] != recoveryPolicyHash(settings)):
        raise QuarantineError("recovery_not_active")


def executeQuarantineObservation(settings, runId, token, apiToken):
    from app.service.cd2_quarantine_client import Cd2QuarantineClient

    class ReadClient(Cd2QuarantineClient):
        allowedMethods = READ_METHODS
        tokenPermissions = frozenset({"allow_list", "allow_view_runtime_info"})

    with inventoryDb(settings) as connection:
        row = actions._worker(connection, runId, token)
        actions._active(settings, connection, row, _wallNow())
        _, snapshot = actions._snapshot(connection, row)
        remaining = row["expires_at"]-time.time()
    deadline = time.monotonic()+min(OBSERVE_SECONDS, remaining)
    folder = "/"+snapshot["targetName"]
    with ReadClient(settings, runId, token, apiToken) as client:
        client.verifyConnection(deadline)
        client.findObject("/", deadline)
        client.findObject(folder, deadline)
        for ordinal in range(1, len(snapshot["items"])+1):
            _, source, target, _ = actions.itemPaths(snapshot, ordinal)
            client.findObject(source, deadline, itemOrdinal=ordinal, missingAllowed=True)
            client.findObject(target, deadline, itemOrdinal=ordinal, missingAllowed=True)
        client.findObject("/", deadline)
        client.findObject(folder, deadline)
        verifyQuarantinePositions(settings, runId, token)


def verifyQuarantinePositions(settings, runId, token):
    with inventoryDb(settings, write=True) as connection:
        row = actions._worker(connection, runId, token)
        requireObservationActive(settings, connection, row, _wallNow())
        original = actions._row(connection, row["original_run_id"])
        _, snapshot = actions._snapshot(connection, row)
        rpcs = connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE run_id=? ORDER BY ordinal", (runId,)).fetchall()
        if (len(rpcs) != 6+2*len(snapshot["items"]) or row["received_bytes"] > row["max_bytes"]
                or any(r["phase"] != "responded" for r in rpcs)
                or [r["method"] for r in rpcs[:2]] != ["GetApiTokenInfo", "GetRuntimeInfo"]
                or any(r["response_code"] != "ok" for r in rpcs[:2])
                or any(r["method"] != "FindFileByPath" for r in rpcs[2:])):
            raise QuarantineError("recovery_evidence_incomplete")
        folder = "/"+snapshot["targetName"]
        if not _object(rpcs[2], "/", directory=True):
            raise QuarantineError("parent_identity_changed")
        parentId = json.loads(rpcs[2]["identity_json"])["objectId"]
        if (not _object(rpcs[-2], "/", parentId, directory=True)
                or any(not _object(r, folder, snapshot["targetRemoteId"], directory=True) for r in (rpcs[3], rpcs[-1]))
                or any(r["item_ordinal"] is not None for r in (rpcs[2],rpcs[3],rpcs[-2],rpcs[-1]))):
            raise QuarantineError("parent_identity_changed")
        issued = connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND method='MoveFile'", (original["id"],)).fetchone()
        baseline = connection.execute("SELECT identity_json FROM cloud_quarantine_rpcs WHERE run_id=? AND method='FindFileByPath' "
            "AND request_path='/' AND item_ordinal IS NULL AND response_code='ok' AND phase='responded' ORDER BY ordinal LIMIT 1", (original["id"],)).fetchone()
        if ((issued and (baseline is None or json.loads(baseline[0])["objectId"] != parentId))
                or (snapshot.get("parentRemoteId") is not None and snapshot["parentRemoteId"] != parentId)):
            raise QuarantineError("parent_identity_changed")
        positions = []
        for ordinal in range(1, len(snapshot["items"])+1):
            item, sourcePath, targetPath, _ = actions.itemPaths(snapshot, ordinal)
            source, target = rpcs[4+2*(ordinal-1):6+2*(ordinal-1)]
            if source["item_ordinal"] != ordinal or target["item_ordinal"] != ordinal:
                raise QuarantineError("recovery_evidence_incomplete")
            sourceMatch = _object(source, sourcePath, item["remoteId"], directory=False, size=item["size"])
            targetMatch = _object(target, targetPath, item["remoteId"], directory=False, size=item["size"])
            targetOther = (_object(target, targetPath, directory=False) and json.loads(target["identity_json"])["objectId"] != item["remoteId"])
            position = ("at_source" if sourceMatch and (_missing(target, targetPath) or targetOther)
                else "at_target" if targetMatch and _missing(source, sourcePath) else "ambiguous")
            positions.append({"ordinal": ordinal, "position": position, "remoteId": item["remoteId"],
                "sourcePath": item["sourcePath"], "targetPath": item["targetPath"], "parentRemoteId": parentId})
        ready = all(p["position"] != "ambiguous" for p in positions)
        connection.execute("UPDATE cloud_quarantine_runs SET status='review',reason=?,positions_json=?,revision=revision+1,updated_at=? WHERE id=?",
            ("positions_observed" if ready else "position_ambiguous", plans.reviews._json(positions), _wallNow(), runId))
        actions._audit(settings, connection, row, "positions_observed", detail={"positionsCertain": ready, "causalityClaimed": False})
        return ready


def _commitOriginal(settings, connection, original, positions, now, observedBy=None):
    epoch = advanceCloudPathEpoch(connection, now)
    connection.execute("UPDATE cloud_quarantine_runs SET status=?,reason=?,slot_held=0,worker_pid=NULL,positions_json=?,"
        "committed_epoch=?,committed_at=?,revision=revision+1,updated_at=? WHERE id=?",
        ("review" if observedBy else "completed", "positions_reconciled" if observedBy else None,
         plans.reviews._json(positions), epoch, now, now, original["id"]))
    if observedBy:
        connection.execute("UPDATE cloud_quarantine_rpcs SET phase=CASE WHEN phase='sending' THEN 'unknown' ELSE phase END,reviewed_by_run=? "
            "WHERE run_id=? AND phase IN ('sending','unknown') AND reviewed_by_run IS NULL", (observedBy, original["id"]))
        connection.execute("UPDATE cloud_quarantine_rpcs SET reviewed_by_run=? WHERE run_id IN "
            "(SELECT id FROM cloud_quarantine_runs WHERE original_run_id=?) AND phase IN ('sending','unknown') AND reviewed_by_run IS NULL",
            (observedBy, original["id"]))
    actions._audit(settings, connection, original, "positions_committed", detail={"epoch": epoch, "observedBy": observedBy, "automaticRescan": False})
    return epoch


def commitQuarantineLocal(settings, runId, *, expectedRevision, confirmLocalOnly, actorId):
    _limit(expectedRevision, 0, 2**63-1)
    if confirmLocalOnly is not True:
        raise ValueError("须确认仅提交本地修订，不发送云请求")
    with inventoryDb(settings, write=True) as connection:
        original = actions._owned(connection, runId, actorId)
        actions._executePermission(connection, actorId)
        if connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE original_run_id=?", (runId,)).fetchone():
            raise QuarantineError("path_commit_requires_review")
        if original["committed_epoch"] is not None:
            return original["committed_epoch"]
        original = _original(connection, runId, actorId)
        if original["revision"] != expectedRevision or original["policy_hash"] != recoveryPolicyHash(settings):
            raise QuarantineError("revision_or_policy_changed")
        if connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND phase!='responded'", (runId,)).fetchone():
            raise QuarantineError("path_commit_requires_review")
        _, snapshot = actions._snapshot(connection, original)
        if snapshot["pathEpoch"] != cloudPathEpoch(connection)["revision"]:
            raise QuarantineError("revision_or_policy_changed")
        outcomes = connection.execute("SELECT * FROM cloud_quarantine_outcomes WHERE run_id=? ORDER BY ordinal", (runId,)).fetchall()
        now = _wallNow()
        if len(outcomes) != len(snapshot["items"]) or any(o["state"] not in {"verified","not_applied"} or not 0 <= now-o["verified_at"] <= 300 for o in outcomes):
            raise QuarantineError("recovery_not_ready_or_stale")
        positions = []
        for outcome, item in zip(outcomes, snapshot["items"]):
            parent = connection.execute("SELECT identity_json FROM cloud_quarantine_rpcs WHERE run_id=? AND request_path='/' "
                "AND response_code='ok' AND phase='responded' ORDER BY ordinal LIMIT 1", (runId,)).fetchone()
            positions.append({"ordinal": outcome["ordinal"], "position": "at_target" if outcome["state"] == "verified" else "at_source",
                "remoteId": item["remoteId"], "sourcePath": item["sourcePath"], "targetPath": item["targetPath"],
                "parentRemoteId": json.loads(parent[0])["objectId"]})
        oldest = connection.execute("SELECT MIN(responded_at) FROM cloud_quarantine_rpcs WHERE run_id=? AND method='FindFileByPath'", (runId,)).fetchone()[0]
        if oldest is None or not 0 <= now-oldest <= 300:
            raise QuarantineError("recovery_not_ready_or_stale")
        return _commitOriginal(settings, connection, original, positions, now)


def commitQuarantineObservation(settings, runId, *, expectedRevision, confirmLocalOnly, actorId):
    _limit(expectedRevision, 0, 2**63-1)
    if confirmLocalOnly is not True:
        raise ValueError("须确认仅依据当前位置提交本地修订，不改原未知回执")
    with inventoryDb(settings, write=True) as connection:
        row = actions._owned(connection, runId, actorId)
        actions._executePermission(connection, actorId)
        if row["kind"] != "observe":
            raise QuarantineError("not_found")
        if row["committed_epoch"] is not None:
            return row["committed_epoch"]
        original = _original(connection, row["original_run_id"], actorId)
        if (row["revision"] != expectedRevision or row["basis_revision"] != original["revision"]
                or row["basis_epoch"] != cloudPathEpoch(connection)["revision"] or row["policy_hash"] != recoveryPolicyHash(settings)):
            raise QuarantineError("revision_or_policy_changed")
        now = _wallNow()
        if (row["status"] != "review" or row["reason"] != "positions_observed" or row["cancel_requested"] or row["worker_pid"] is not None or row["slot_held"]
                or connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE original_run_id=? AND attempt>?", (original["id"],row["attempt"])).fetchone()
                or connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND phase!='responded'", (runId,)).fetchone()):
            raise QuarantineError("recovery_not_ready_or_stale")
        oldest = connection.execute("SELECT MIN(responded_at) FROM cloud_quarantine_rpcs WHERE run_id=? AND method='FindFileByPath'", (runId,)).fetchone()[0]
        positions = json.loads(row["positions_json"] or "[]")
        _, snapshot = actions._snapshot(connection, row)
        if (oldest is None or not 0 <= now-oldest <= 300 or len(positions) != len(snapshot["items"])
                or any(p["position"] not in {"at_source","at_target"} for p in positions)):
            raise QuarantineError("recovery_not_ready_or_stale")
        epoch = _commitOriginal(settings, connection, original, positions, now, runId)
        connection.execute("UPDATE cloud_quarantine_runs SET committed_epoch=?,committed_at=?,revision=revision+1,updated_at=? WHERE id=?", (epoch,now,now,runId))
        return epoch


def reversePlanInvalidReason(connection, row, snapshot):
    origin = connection.execute("SELECT * FROM cloud_quarantine_runs WHERE id=?", (row["origin_run_id"],)).fetchone()
    if (origin is None or origin["actor_id"] != row["actor_id"] or origin["slot_held"] or origin["worker_pid"] is not None
            or origin["committed_epoch"] != row["basis_epoch"] or row["basis_epoch"] != cloudPathEpoch(connection)["revision"]):
        return "reverse_basis_changed"
    return None


def createReverseQuarantinePlan(settings, planId, *, requestId, expectedRevision, auditContext):
    inventoryId(requestId)
    _limit(expectedRevision, 0, 2**63-1)
    if auditContext.actor is None:
        raise QuarantineError("permission_denied")
    with inventoryDb(settings, write=True) as connection:
        plan = plans._owned(connection, planId, auditContext.actor.id)
        requestHash = plans.reviews._digest(["reverse",planId,expectedRevision])
        old = connection.execute("SELECT * FROM cloud_quarantine_plans WHERE request_id=?", (requestId,)).fetchone()
        if old:
            if old["actor_id"] != plan["actor_id"] or old["request_hash"] != requestHash:
                raise QuarantineError("request_conflict")
            return plans._view(connection, settings, old)
        if plan["revision"] != expectedRevision or plan["policy_hash"] != recoveryPolicyHash(settings):
            raise QuarantineError("revision_or_policy_changed")
        from app.service.cloud_directory_guard import requireNoDirectoryExecution
        requireNoDirectoryExecution(connection)
        original = connection.execute("SELECT * FROM cloud_quarantine_runs WHERE plan_id=? AND kind='move'", (planId,)).fetchone()
        epoch = cloudPathEpoch(connection)["revision"]
        if original is None or original["committed_epoch"] != epoch or original["slot_held"] or original["worker_pid"] is not None:
            raise QuarantineError("reverse_basis_changed")
        snapshot = json.loads(plan["snapshot_json"])
        positions = json.loads(original["positions_json"] or "[]")
        chosen = {p["ordinal"] for p in positions if p["position"] == "at_target"}
        if not chosen:
            raise QuarantineError("reverse_basis_outdated_or_no_changes")
        snapshot["items"] = [{**item, "sourcePath": item["targetPath"], "targetPath": item["sourcePath"]}
            for ordinal,item in enumerate(snapshot["items"],1) if ordinal in chosen]
        snapshot["parentRemoteId"] = positions[0]["parentRemoteId"]
        snapshot["pathEpoch"] = epoch
        snapshot["originRunId"] = original["id"]
        serialized = plans.reviews._json(snapshot)
        size = len(serialized.encode())
        count,total = connection.execute("SELECT COUNT(*),COALESCE(SUM(logical_bytes),0) FROM cloud_quarantine_plans").fetchone()
        if count >= plans.MAX_PLANS or size > plans.MAX_SNAPSHOT_BYTES or total+size > plans.MAX_TOTAL_BYTES:
            raise QuarantineError("plan_capacity")
        newId,now = str(uuid4()),_wallNow()
        connection.execute("INSERT INTO cloud_quarantine_plans "
            "(id,request_id,request_hash,actor_id,review_id,job_id,inventory_revision,path_epoch,policy_hash,rule_version,item_count,"
            "snapshot_json,snapshot_hash,logical_bytes,created_at,expires_at,updated_at,kind,origin_run_id,basis_epoch) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'reverse',?,?)",
            (newId,requestId,requestHash,plan["actor_id"],plan["review_id"],plan["job_id"],plan["inventory_revision"],epoch,
             plans.quarantinePolicyHash(settings),plan["rule_version"],len(snapshot["items"]),serialized,sha256(serialized.encode()).hexdigest(),size,
             now,now+plans.PLAN_SECONDS,now,original["id"],epoch))
        from app.service.audit_service import record_audit_event
        record_audit_event(settings,event_type="cloud_quarantine.plan",action="reverse_prepare",result="success",
            summary="固定反向隔离新计划；未发送云请求",target_type="cloud_quarantine_plan",target_id=newId,
            actor=auditContext.actor,connection=connection,detail={"itemCount":len(snapshot["items"]),"originRunId":original["id"]})
        return plans._view(connection, settings, plans._owned(connection, newId, plan["actor_id"]))


def reviewQuarantineWorker(settings, runId, *, expectedRevision, confirmClientsStopped, confirmRemoteIdle, auditContext):
    """仅本机管理员核对旧进程并归还PID；未知和原动作保护不因进程退出释放。"""
    _limit(expectedRevision, 0, 2**63-1)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True or auditContext.actor is None:
        raise ValueError("须确认旧客户端退出及相关远端静止；不会释放原未知保护或发送请求")
    with inventoryDb(settings, write=True) as connection:
        try:
            plans._permission(connection, auditContext.actor.id, "settings:write")
        except plans.CloudDirectoryError:
            raise QuarantineError("permission_denied") from None
        row = actions._owned(connection, runId, auditContext.actor.id)
        actions._executePermission(connection, auditContext.actor.id)
        if row["revision"] != expectedRevision or row["owner_host"] != cloudDriveHostKey() or not row["slot_held"]:
            raise QuarantineError("revision_or_policy_changed")
        if (row["worker_pid"] is not None and (isProcessAlive(row["worker_pid"]) or isProcessAlive(row["owner_pid"]))) or (
                row["status"] in {"accepted","running"} and isProcessAlive(row["owner_pid"])):
            raise QuarantineError("worker_exit_unconfirmed")
        unknown = connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND phase IN ('sending','unknown') LIMIT 1", (runId,)).fetchone()
        connection.execute("UPDATE cloud_quarantine_rpcs SET phase='unknown' WHERE run_id=? AND phase='sending'", (runId,))
        now = _wallNow()
        if unknown:
            actions._pauseUnknown(settings, connection, now)
        connection.execute("UPDATE cloud_quarantine_runs SET worker_pid=NULL,status='review',"
            "reason=COALESCE(reason,'worker_interrupted'),slot_held=?,revision=revision+1,updated_at=? WHERE id=?", (1 if row["kind"] == "move" else 0,now,runId))
        actions._audit(settings, connection, row, "worker_reviewed", auditContext,
            {"clientsStoppedConfirmed":True,"remoteIdleConfirmed":True,"originalOutcomeRewritten":False,"networkAccess":False})
        return actions._view(connection, actions._row(connection, runId))
