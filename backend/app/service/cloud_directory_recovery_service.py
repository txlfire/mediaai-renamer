"""独立只读恢复预算与当前位置证据，不重试原动作、不回填未知RPC为成功。"""

from contextlib import contextmanager
from dataclasses import replace
import os
import time
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service.cloud_directory_plan_service import CloudDirectoryError, _owned, _permission, _revision, directoryPolicyHash
from app.service.cloud_directory_admission_service import DirectorySpacingWait
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.service.cloud_directory_log_service import recordDirectoryLog
from app.service.cloud_drive_budget_service import _pool, _wallNow, cloudDriveHostKey
from app.service.cloud_inventory_service import inventoryDb, inventoryId
from app.service.scan_recovery_service import isProcessAlive

READ_METHODS = frozenset({"GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath"})
MAX_RECOVERY_BYTES = 1048576
RECOVERY_SECONDS = 90


def recoveryPolicyHash(settings):
    # 关闭写开关仍可只读核对，其他连接/信任/映射/规则摘要不能变。
    return directoryPolicyHash(replace(settings, cloudDrive=replace(settings.cloudDrive, directoryRenameEnabled=True)))


def _row(connection, recoveryId):
    row = connection.execute("SELECT * FROM cloud_directory_recoveries WHERE id=?", (recoveryId,)).fetchone()
    if row is None:
        raise CloudDirectoryError("not_found")
    return row


def _original(connection, planId, actorId):
    _owned(connection, planId, actorId)
    _permission(connection, actorId, "cloud-directory:execute")
    row = connection.execute("SELECT * FROM cloud_directory_executions WHERE plan_id=? AND actor_id=?", (planId, actorId)).fetchone()
    if row is None or row["slot_held"] != 1 or row["owner_host"] != cloudDriveHostKey():
        raise CloudDirectoryError("recovery_not_required_or_not_local")
    if row["worker_pid"] is not None:
        if isProcessAlive(row["worker_pid"]) or isProcessAlive(row["owner_pid"]):
            raise CloudDirectoryError("worker_exit_unconfirmed")
    elif row["status"] in {"accepted", "running"} and isProcessAlive(row["owner_pid"]):
        # 尚未绑定PID也可能正准备启动；其他API进程不能把它当成已退出。
        raise CloudDirectoryError("worker_exit_unconfirmed")
    return row


def _audit(settings, connection, row, action, *, detail=None):
    record_audit_event(settings, event_type="cloud_directory.recovery", action=action, result="pending",
        summary="目录当前位置只读核对；原请求事实不改写、不重发", actor_id=row["actor_id"],
        actor_name="cloud_directory_recovery", target_type="cloud_directory_recovery", target_id=row["id"],
        detail={"planId": row["plan_id"], "replayed": False, **(detail or {})}, connection=connection)
    recordDirectoryLog(settings, row["plan_id"], action, connection=connection)


def approveDirectoryRecovery(settings, planId, *, requestId, expectedRevision, confirmMaintenance, confirmRemoteIdle, auditContext):
    inventoryId(planId)
    inventoryId(requestId)
    _revision(expectedRevision)
    if confirmMaintenance is not True or confirmRemoteIdle is not True or auditContext.actor is None:
        raise ValueError("须确认维护窗口、旧进程退出及原请求和先前恢复涉及的远端已静止")
    with inventoryDb(settings, write=True) as connection:
        original = _original(connection, planId, auditContext.actor.id)
        if original["revision"] != expectedRevision:
            raise CloudDirectoryError("revision_conflict")
        if connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE request_id=?", (requestId,)).fetchone():
            raise CloudDirectoryError("recovery_already_accepted")
        if original["policy_hash"] != recoveryPolicyHash(settings):
            raise CloudDirectoryError("execution_policy_changed")
        if (connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1").fetchone()):
            raise CloudDirectoryError("recovery_slot_busy")
        attempt = connection.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM cloud_directory_recoveries WHERE plan_id=?", (planId,)).fetchone()[0]
        if attempt > 5:
            raise CloudDirectoryError("recovery_attempt_limit")
        count = connection.execute("SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?", (planId,)).fetchone()[0]
        if not 1 <= count <= 5:
            raise CloudDirectoryError("invalid_plan_items")
        now = _wallNow()
        pool = _pool(settings, connection, now)
        if now < pool["cooldown_until"] or (pool["state"] != "ready" and pool["pause_reason"] != "request_incomplete"):
            raise CloudDirectoryError("pool_not_ready")
        if (3 + 2 * count) * pool["min_interval"] + 15 > RECOVERY_SECONDS:
            raise CloudDirectoryError("batch_exceeds_time_budget")
        recoveryId, token = str(uuid4()), str(uuid4())
        connection.execute("INSERT INTO cloud_directory_recoveries "
            "(id,request_id,plan_id,attempt,actor_id,policy_hash,basis_epoch,basis_revision,status,worker_token,owner_pid,owner_host,"
            "max_rpc,max_bytes,created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'accepted',?,?,?,?,?,?,?,?)",
            (recoveryId, requestId, planId, attempt, original["actor_id"], original["policy_hash"], cloudPathEpoch(connection)["revision"],
             original["revision"], token, os.getpid(), cloudDriveHostKey(), 4 + 2 * count, MAX_RECOVERY_BYTES, now, now + RECOVERY_SECONDS, now))
        # 仅本计划全局冻结下、冷却已过且用户确认远端静止，允许新只读预算；原未知记录保留。
        if pool["state"] != "ready":
            connection.execute("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL,revision=revision+1,updated_at=? WHERE id=1", (now,))
        row = _row(connection, recoveryId)
        _audit(settings, connection, row, "recovery_approved", detail={"newReadBudget": True, "attempt": attempt,
            "maintenanceConfirmed": True, "remoteIdleConfirmed": True, "originalRpcChanged": False})
        return recoveryId, token


def _worker(connection, recoveryId, token):
    row = _row(connection, recoveryId)
    if (row["worker_token"] != token or row["worker_pid"] != os.getpid() or row["slot_held"] != 1
            or row["owner_host"] != cloudDriveHostKey()):
        raise CloudDirectoryError("worker_ownership_lost")
    return row


def _active(settings, connection, row, now):
    original = _original(connection, row["plan_id"], row["actor_id"])
    if (row["status"] != "running" or row["cancel_requested"] or now < row["created_at"] or now >= row["expires_at"]
            or row["basis_revision"] != original["revision"] or row["basis_epoch"] != cloudPathEpoch(connection)["revision"]):
        raise CloudDirectoryError("recovery_not_active")
    if row["policy_hash"] != recoveryPolicyHash(settings):
        raise CloudDirectoryError("execution_policy_changed")


def bindRecoveryWorker(settings, recoveryId, token, pid):
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        raise ValueError("工作进程PID无效")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, recoveryId)
        if (row["worker_token"] != token or row["worker_pid"] is not None or row["owner_pid"] != os.getpid()
                or row["owner_host"] != cloudDriveHostKey() or row["status"] != "accepted" or row["slot_held"] != 1):
            raise CloudDirectoryError("worker_ownership_lost")
        candidate = dict(row)
        candidate["status"] = "running"
        _active(settings, connection, candidate, _wallNow())
        connection.execute("UPDATE cloud_directory_recoveries SET status='running',worker_pid=?,revision=revision+1,updated_at=? WHERE id=?", (pid, time.time(), recoveryId))


def reserveRecoveryRpc(settings, recoveryId, token, method, *, itemOrdinal=None, requestPath=None):
    if method not in READ_METHODS:
        raise CloudDirectoryError("rpc_not_allowed")
    if itemOrdinal is not None and (type(itemOrdinal) is not int or not 1 <= itemOrdinal <= 5):
        raise ValueError("恢复条目序号无效")
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, recoveryId, token)
        now = _wallNow()
        _active(settings, connection, row, now)
        items = connection.execute("SELECT ordinal,source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=? ORDER BY ordinal", (row["plan_id"],)).fetchall()
        if method == "FindFileByPath":
            valid = itemOrdinal is None and requestPath == "/" or any(item["ordinal"] == itemOrdinal
                and requestPath in {"/" + item["source_name"], "/" + item["target_name"]} for item in items)
            if type(itemOrdinal) is bool or not valid:
                raise CloudDirectoryError("path_not_in_plan")
        elif itemOrdinal is not None or requestPath is not None:
            raise CloudDirectoryError("rpc_not_allowed")
        if row["used_rpc"] >= row["max_rpc"] or row["received_bytes"] >= row["max_bytes"]:
            raise CloudDirectoryError("execution_budget_exhausted")
        if connection.execute("SELECT 1 FROM cloud_directory_recovery_rpcs WHERE recovery_id=? AND phase IN ('sending','unknown') LIMIT 1", (recoveryId,)).fetchone():
            raise CloudDirectoryError("rpc_unresolved")
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready" or now < pool["cooldown_until"]:
            raise CloudDirectoryError("pool_not_ready")
        if pool["last_started_at"] is not None and now < pool["last_started_at"] + pool["min_interval"]:
            raise DirectorySpacingWait(pool["last_started_at"] + pool["min_interval"] - now)
        rpcId = str(uuid4())
        connection.execute("INSERT INTO cloud_directory_recovery_rpcs(id,recovery_id,ordinal,method,item_ordinal,request_path,phase,sent_at) VALUES(?,?,?,?,?,?,'sending',?)",
            (rpcId, recoveryId, row["used_rpc"] + 1, method, itemOrdinal, requestPath, now))
        connection.execute("UPDATE cloud_directory_recoveries SET used_rpc=used_rpc+1,revision=revision+1,updated_at=? WHERE id=?", (now, recoveryId))
        connection.execute("UPDATE cloud_drive_budget_pool SET last_started_at=? WHERE id=1", (now,))
        return rpcId


def recordRecoveryResponse(settings, recoveryId, token, rpcId, *, responseCode, receivedBytes, identity=None):
    if responseCode not in {"ok", "not_found"} or type(receivedBytes) is not int or not 0 <= receivedBytes <= 2**31 - 1:
        raise ValueError("恢复响应须为固定状态及有效计数")
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, recoveryId, token)
        rpc = connection.execute("SELECT * FROM cloud_directory_recovery_rpcs WHERE id=? AND recovery_id=? AND phase='sending'", (rpcId, recoveryId)).fetchone()
        if rpc is None:
            raise CloudDirectoryError("rpc_state_changed")
        remoteId = None
        if rpc["method"] == "FindFileByPath" and responseCode == "ok":
            import unicodedata
            if (not isinstance(identity, dict) or set(identity) != {"objectId", "virtualPath", "name"}
                    or identity["virtualPath"] != rpc["request_path"] or not isinstance(identity["objectId"], str)
                    or not identity["objectId"] or len(identity["objectId"].encode()) > 1024
                    or any(unicodedata.category(char).startswith("C") for char in identity["objectId"])):
                raise ValueError("恢复响应缺少已校验的精确身份")
            remoteId = identity["objectId"]
        elif identity is not None or (responseCode == "not_found" and rpc["method"] != "FindFileByPath"):
            raise ValueError("恢复响应类型不一致")
        now = _wallNow()
        connection.execute("UPDATE cloud_directory_recovery_rpcs SET phase='responded',response_code=?,remote_id=?,received_bytes=?,responded_at=? WHERE id=?",
            (responseCode, remoteId, receivedBytes, now, rpcId))
        connection.execute("UPDATE cloud_directory_recoveries SET received_bytes=received_bytes+?,revision=revision+1,updated_at=? WHERE id=?", (receivedBytes, now, recoveryId))


def markRecoveryUnknown(settings, recoveryId, token, rpcId):
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, recoveryId, token)
        changed = connection.execute("UPDATE cloud_directory_recovery_rpcs SET phase='unknown' WHERE id=? AND recovery_id=? AND phase='sending'", (rpcId, recoveryId)).rowcount
        if changed != 1:
            raise CloudDirectoryError("rpc_state_changed")
        now = _wallNow()
        pool = _pool(settings, connection, now)
        connection.execute("UPDATE cloud_directory_recoveries SET status='review',reason='rpc_unknown',revision=revision+1,updated_at=? WHERE id=?", (now, recoveryId))
        connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
            "pause_reason=CASE WHEN pause_reason='manual_pause' THEN pause_reason ELSE 'request_incomplete' END,"
            "cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1", (now + pool["failure_cooldown"], now))
        _audit(settings, connection, _row(connection, recoveryId), "recovery_unknown")


class RecoveryRpcAdmission:
    def __init__(self, settings, recoveryId, token):
        self.settings, self.recoveryId, self.token, self.pid = settings, recoveryId, token, os.getpid()
        with inventoryDb(settings) as connection:
            row = _worker(connection, recoveryId, token)
            _active(settings, connection, row, time.time())
            self.planId = row["plan_id"]
            self.parentPath = connection.execute("SELECT parent_path FROM cloud_directory_plans WHERE id=?", (self.planId,)).fetchone()[0]

    @contextmanager
    def call(self, method, deadline, itemOrdinal=None, requestPath=None):
        if self.pid != os.getpid() or method not in READ_METHODS:
            raise CloudDirectoryError("rpc_not_allowed")
        rpcId = None
        while time.monotonic() < deadline:
            try:
                rpcId = reserveRecoveryRpc(self.settings, self.recoveryId, self.token, method, itemOrdinal=itemOrdinal, requestPath=requestPath)
                break
            except DirectorySpacingWait as exc:
                time.sleep(min(exc.retryAfterSeconds, max(0, deadline - time.monotonic())))
        if rpcId is None:
            raise CloudDirectoryError("deadline_exceeded")
        recorded = False

        def responded(code, size, identity=None):
            nonlocal recorded
            recordRecoveryResponse(self.settings, self.recoveryId, self.token, rpcId, responseCode=code, receivedBytes=size, identity=identity)
            recorded = True

        try:
            yield responded
        finally:
            if not recorded:
                markRecoveryUnknown(self.settings, self.recoveryId, self.token, rpcId)


def verifyRecoveryPositions(settings, recoveryId, token):
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, recoveryId, token)
        _active(settings, connection, row, _wallNow())
        items = connection.execute("SELECT * FROM cloud_directory_plan_items WHERE plan_id=? ORDER BY ordinal", (row["plan_id"],)).fetchall()
        rpcs = connection.execute("SELECT * FROM cloud_directory_recovery_rpcs WHERE recovery_id=? ORDER BY ordinal", (recoveryId,)).fetchall()
        if (row["received_bytes"] > row["max_bytes"] or len(rpcs) != 4 + 2 * len(items)
                or any(rpc["phase"] != "responded" for rpc in rpcs)
                or [rpc["method"] for rpc in rpcs[:3]] != ["GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath"]):
            raise CloudDirectoryError("recovery_evidence_incomplete")
        first, last = rpcs[2], rpcs[-1]
        parent = first["remote_id"]
        if (not parent or first["request_path"] != "/" or last["request_path"] != "/"
                or last["method"] != "FindFileByPath" or first["item_ordinal"] is not None or last["item_ordinal"] is not None
                or last["response_code"] != "ok" or last["remote_id"] != parent):
            raise CloudDirectoryError("parent_identity_changed")
        writeIssued = connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE plan_id=? AND method='RenameFile' LIMIT 1", (row["plan_id"],)).fetchone()
        baseline = connection.execute("SELECT remote_id FROM cloud_directory_rpc_intents WHERE plan_id=? AND method='FindFileByPath' AND request_path='/' AND item_ordinal IS NULL AND phase='responded' AND response_code='ok' ORDER BY ordinal LIMIT 1", (row["plan_id"],)).fetchone()
        if writeIssued and (baseline is None or baseline["remote_id"] != parent):
            raise CloudDirectoryError("parent_identity_changed")
        positions = []
        for index, item in enumerate(items):
            source, target = rpcs[3 + 2 * index:5 + 2 * index]
            if any(rpc["method"] != "FindFileByPath" or rpc["item_ordinal"] != item["ordinal"] for rpc in (source, target)) or source["request_path"] != "/" + item["source_name"] or target["request_path"] != "/" + item["target_name"]:
                raise CloudDirectoryError("recovery_evidence_incomplete")
            atSource = source["response_code"] == "ok" and source["remote_id"] == item["remote_id"] and target["response_code"] == "not_found" and target["remote_id"] is None
            atTarget = target["response_code"] == "ok" and target["remote_id"] == item["remote_id"] and source["response_code"] == "not_found" and source["remote_id"] is None
            positions.append((recoveryId, item["ordinal"], "at_source" if atSource else "at_target" if atTarget else "ambiguous", source["remote_id"], target["remote_id"], parent, _wallNow()))
        connection.executemany("INSERT INTO cloud_directory_recovery_items(recovery_id,ordinal,position,source_id,target_id,parent_id,observed_at) VALUES(?,?,?,?,?,?,?)", positions)
        ready = all(position[2] != "ambiguous" for position in positions)
        connection.execute("UPDATE cloud_directory_recoveries SET status=?,reason=?,revision=revision+1,updated_at=? WHERE id=?",
            ("ready" if ready else "review", None if ready else "position_ambiguous", _wallNow(), recoveryId))
        _audit(settings, connection, row, "recovery_observed", detail={"positionsCertain": ready, "causalityClaimed": False})
        return ready


def commitRecoveryPositions(settings, recoveryId, *, expectedRevision, confirmCurrentPositions, actorId):
    inventoryId(recoveryId)
    _revision(expectedRevision)
    if confirmCurrentPositions is not True:
        raise ValueError("须确认仅依据本轮已核对位置提交本地修订，原请求仍可能未知，不发送云请求")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, recoveryId)
        if row["actor_id"] != actorId:
            raise CloudDirectoryError("not_found")
        _permission(connection, actorId)
        _permission(connection, actorId, "cloud-directory:execute")
        if row["status"] == "committed":
            return cloudPathEpoch(connection)["revision"]
        original = _original(connection, row["plan_id"], actorId)
        if (row["revision"] != expectedRevision or row["basis_revision"] != original["revision"]
                or row["basis_epoch"] != cloudPathEpoch(connection)["revision"] or row["policy_hash"] != recoveryPolicyHash(settings)):
            raise CloudDirectoryError("revision_or_policy_changed")
        now = _wallNow()
        if (row["status"] != "ready" or row["cancel_requested"] or row["worker_pid"] is not None or row["slot_held"] != 0
                or now < row["updated_at"] or now > row["updated_at"] + 300
                or connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE plan_id=? AND attempt>? LIMIT 1", (row["plan_id"], row["attempt"])).fetchone()):
            raise CloudDirectoryError("recovery_not_ready_or_stale")
        oldestResponse = connection.execute("SELECT MIN(responded_at) FROM cloud_directory_recovery_rpcs WHERE recovery_id=? AND method='FindFileByPath'", (recoveryId,)).fetchone()[0]
        if oldestResponse is None or not 0 <= now - oldestResponse <= 300:
            raise CloudDirectoryError("recovery_not_ready_or_stale")
        positions = connection.execute("SELECT p.*,r.position FROM cloud_directory_plan_items p JOIN cloud_directory_recovery_items r "
            "ON r.ordinal=p.ordinal AND r.recovery_id=? WHERE p.plan_id=? ORDER BY p.ordinal", (recoveryId, row["plan_id"])).fetchall()
        count = connection.execute("SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?", (row["plan_id"],)).fetchone()[0]
        if (not 1 <= count <= 5 or len(positions) != count or any(item["position"] not in {"at_source", "at_target"} for item in positions)
                or connection.execute("SELECT 1 FROM cloud_directory_recovery_rpcs WHERE recovery_id=? AND phase!='responded' LIMIT 1", (recoveryId,)).fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_path_revisions WHERE plan_id=? LIMIT 1", (row["plan_id"],)).fetchone()):
            raise CloudDirectoryError("recovery_evidence_incomplete")
        from app.service.cloud_directory_path_service import advanceCloudPathEpoch
        epoch = advanceCloudPathEpoch(connection, now)
        connection.executemany("INSERT INTO cloud_directory_path_revisions(plan_id,ordinal,epoch,source_path,target_path,remote_id,committed_at) VALUES(?,?,?,?,?,?,?)",
            [(row["plan_id"], item["ordinal"], epoch, item["source_path"], item["target_path"], item["remote_id"], now)
             for item in positions if item["position"] == "at_target"])
        # 未知依然未知；单独引用本轮位置核对，解除的是本地发送阻断，不伪造历史响应。
        connection.execute("UPDATE cloud_directory_rpc_intents SET phase=CASE WHEN phase='sending' THEN 'unknown' ELSE phase END,reviewed_by_recovery=? "
            "WHERE plan_id=? AND phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL", (recoveryId, row["plan_id"]))
        connection.execute("UPDATE cloud_directory_executions SET status='review',reason='positions_reconciled',slot_held=0,worker_pid=NULL,revision=revision+1,updated_at=? WHERE plan_id=?", (now, row["plan_id"]))
        connection.execute("UPDATE cloud_directory_recoveries SET status='committed',revision=revision+1,updated_at=? WHERE id=?", (now, recoveryId))
        _audit(settings, connection, row, "recovery_positions_committed", detail={"epoch": epoch, "causalityClaimed": False,
            "atTarget": sum(item["position"] == "at_target" for item in positions), "automaticRescan": False})
        return epoch


def recoveryView(connection, row):
    items = connection.execute("SELECT p.ordinal,p.source_name,p.target_name,r.position FROM cloud_directory_plan_items p "
        "LEFT JOIN cloud_directory_recovery_items r ON r.ordinal=p.ordinal AND r.recovery_id=? WHERE p.plan_id=? ORDER BY p.ordinal", (row["id"], row["plan_id"])).fetchall()
    events = connection.execute("SELECT ordinal,method,item_ordinal,phase,response_code,received_bytes FROM cloud_directory_recovery_rpcs WHERE recovery_id=? ORDER BY ordinal LIMIT 14", (row["id"],)).fetchall()
    return {"id": row["id"], "requestId": row["request_id"], "planId": row["plan_id"], "attempt": row["attempt"],
        "status": row["status"], "reason": row["reason"], "revision": row["revision"], "workerSlotHeld": row["worker_pid"] is not None,
        "recoverySlotHeld": bool(row["slot_held"]), "cancelRequested": bool(row["cancel_requested"]),
        "usedRpc": row["used_rpc"], "maxRpc": row["max_rpc"], "receivedBytes": row["received_bytes"], "maxBytes": row["max_bytes"],
        "createdAt": row["created_at"], "expiresAt": row["expires_at"], "updatedAt": row["updated_at"],
        "pollingRecommended": (row["status"] in {"accepted", "running"} or row["worker_pid"] is not None) and time.time() < row["expires_at"],
        "items": [{"ordinal": item["ordinal"], "sourceName": item["source_name"], "targetName": item["target_name"], "position": item["position"] or "not_observed"} for item in items],
        "events": [{"ordinal": item["ordinal"], "method": item["method"], "itemOrdinal": item["item_ordinal"], "phase": item["phase"],
            "responseCode": item["response_code"], "receivedBytes": item["received_bytes"]} for item in events],
        "originalOutcomeRewritten": False, "networkAccess": False, "replayed": False}


def getDirectoryRecovery(settings, recoveryId, actorId):
    inventoryId(recoveryId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        row = _row(connection, recoveryId)
        if row["actor_id"] != actorId:
            raise CloudDirectoryError("not_found")
        _owned(connection, row["plan_id"], actorId)
        return recoveryView(connection, row)


def findDirectoryRecoveryRequest(settings, requestId, actorId):
    inventoryId(requestId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permission(connection, actorId)
        row = connection.execute("SELECT * FROM cloud_directory_recoveries WHERE request_id=? AND actor_id=?", (requestId, actorId)).fetchone()
        if row is None:
            raise CloudDirectoryError("not_found")
        _owned(connection, row["plan_id"], actorId)
        return recoveryView(connection, row)


def cancelDirectoryRecovery(settings, recoveryId, *, expectedRevision, actorId):
    inventoryId(recoveryId)
    _revision(expectedRevision)
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, recoveryId)
        if row["actor_id"] != actorId:
            raise CloudDirectoryError("not_found")
        _owned(connection, row["plan_id"], actorId)
        if row["cancel_requested"] or row["status"] == "committed":
            return recoveryView(connection, row)
        if row["revision"] != expectedRevision:
            raise CloudDirectoryError("revision_conflict")
        connection.execute("UPDATE cloud_directory_recoveries SET cancel_requested=1,status=CASE WHEN status='ready' THEN 'review' ELSE status END,"
            "reason='cancel_requested',revision=revision+1,updated_at=? WHERE id=?", (time.time(), recoveryId))
        _audit(settings, connection, row, "recovery_cancel_requested")
        return recoveryView(connection, _row(connection, recoveryId))
