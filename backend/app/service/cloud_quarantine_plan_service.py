"""仅从当前人工复核固定隔离计划；不接收凭据，不连接或修改云端。"""

from hashlib import sha256
import json
import math
import time
import unicodedata
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service import cloud_ad_review_service as reviews
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.service.cloud_directory_plan_service import CloudDirectoryError, _name, _permission
from app.service.cloud_inventory_advice_service import RULE_VERSION, _protected, _PROMOTION, _URL
from app.service.cloud_inventory_service import CloudInventoryError, inventoryDb, inventoryId, _limit
from app.service.cloud_inventory_web_service import _trustedCa
from app.utils.cloud_drive_paths import canonicalCd2Target, canonicalCloudPath, isWithinCloudRoot

MAX_ITEMS = 5
MAX_PLANS = 1000
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
PLAN_SECONDS = 1800


class QuarantineError(CloudDirectoryError):
    def __init__(self, code):
        RuntimeError.__init__(self, "隔离计划停止：" + code + "；未发送云请求")
        self.code = code


def _permissionForPlan(connection, actorId):
    try:
        if type(actorId) is not int or actorId <= 0:
            raise QuarantineError("permission_denied")
        _permission(connection, actorId, "cloud-quarantine:plan")
        _permission(connection, actorId, "scan:run")
    except CloudDirectoryError:
        raise QuarantineError("permission_denied") from None


def quarantinePolicyHash(settings):
    config = settings.cloudDrive
    if not config.endpoint or not config.approvedRoot or not config.directoryMappingRevision:
        raise QuarantineError("scope_not_configured")
    ca = _trustedCa(settings)
    return reviews._digest(["controlled-quarantine-v1", canonicalCd2Target(config.endpoint), config.approvedRoot,
        sha256(ca).hexdigest() if ca else "system-ca", config.directoryMappingRevision,
        config.minIntervalSeconds, config.failureCooldownSeconds, config.quarantineEnabled,
        config.quarantineFolderName, RULE_VERSION, MAX_ITEMS, PLAN_SECONDS])


def _entry(connection, jobId, entryId):
    row = connection.execute("SELECT e.*,d.path AS observed_parent,d.status AS directory_status,"
        "d.generation AS current_generation FROM cloud_drive_inventory_entries e "
        "JOIN cloud_drive_inventory_directories d ON d.id=e.directory_id AND d.job_id=e.job_id "
        "WHERE e.id=? AND e.job_id=?", (entryId, jobId)).fetchone()
    if row is None:
        raise QuarantineError("observation_changed")
    if (row["directory_status"] != "complete" or row["generation"] != row["current_generation"]
            or not isinstance(row["remote_id"], str) or not row["remote_id"]
            or len(row["remote_id"].encode("utf-8")) > 1024
            or any(unicodedata.category(char).startswith("C") for char in row["remote_id"])):
        raise QuarantineError("observation_changed")
    try:
        path = canonicalCloudPath(row["path"])
        _name(row["name"])
    except (ValueError, CloudDirectoryError):
        raise QuarantineError("observation_changed") from None
    if (path != row["path"] or path.rpartition("/")[0] != row["observed_parent"]
            or path.rpartition("/")[2] != row["name"]):
        raise QuarantineError("observation_changed")
    return row


def _snapshot(connection, settings, reviewId, actorId, entryIds, targetEntryId):
    try:
        review = reviews._owned(connection, reviewId, actorId)
    except CloudInventoryError:
        raise QuarantineError("not_found") from None
    reason = reviews._currentReason(connection, settings, review)
    if reason:
        raise QuarantineError(reason)
    job = connection.execute("SELECT j.*,s.endpoint_hash FROM cloud_drive_inventory_jobs j "
        "JOIN cloud_drive_read_sessions s ON s.id=j.session_id WHERE j.id=? AND j.actor_id=?",
        (review["job_id"], actorId)).fetchone()
    if (job is None or job["status"] != "completed" or job["worker_token"] is not None or job["cancel_requested"]):
        raise QuarantineError("observation_changed")
    if (job["endpoint_hash"] != sha256(canonicalCd2Target(settings.cloudDrive.endpoint).encode()).hexdigest()
            or job["path_epoch"] != cloudPathEpoch(connection)["revision"]
            or not isWithinCloudRoot(job["root_path"], settings.cloudDrive.approvedRoot)):
        raise QuarantineError("scope_changed")
    reviewed = {item["entryId"]: item for item in json.loads(review["snapshot_json"])["items"]}
    target = _entry(connection, job["id"], targetEntryId)
    if not target["is_directory"] or target["name"] != settings.cloudDrive.quarantineFolderName:
        raise QuarantineError("invalid_quarantine_target")
    if _protected({"name": target["name"], "relativePath": target["path"], "isDirectory": True}):
        raise QuarantineError("protected_entry")
    items = []
    remoteIds = {target["remote_id"]}
    for entryId in entryIds:
        evidence = reviewed.get(entryId)
        if not evidence or evidence["decision"] != "suggest_quarantine" or not evidence["reason"] or not evidence["evidence"]:
            raise QuarantineError("review_evidence_required")
        row = _entry(connection, job["id"], entryId)
        if (row["is_directory"] or type(row["size"]) is not int or row["size"] < 0
                or any(row[column] != evidence[key] for column, key in (
                    ("name", "name"), ("path", "path"), ("size", "size"),
                    ("remote_id", "remoteId"), ("generation", "generation")))):
            raise QuarantineError("observation_changed")
        if (row["directory_id"] != target["directory_id"] or row["observed_parent"] != target["observed_parent"]
                or not isWithinCloudRoot(row["path"], job["root_path"]) or row["remote_id"] in remoteIds):
            raise QuarantineError("same_parent_distinct_identity_required")
        protection = _protected({"name": row["name"], "relativePath": row["path"], "isDirectory": False})
        kept = connection.execute("SELECT 1 FROM cloud_drive_inventory_keeps WHERE job_id=? AND entry_id IN (?,?)",
            (job["id"], entryId, targetEntryId)).fetchone()
        if protection or kept:
            raise QuarantineError("protected_entry")
        currentEvidence = [code for code, matched in (
            ("promotional_phrase", any(term in row["name"] for term in _PROMOTION)),
            ("domain_or_url_in_name", bool(_URL.search(row["name"])))) if matched]
        if not currentEvidence or evidence["protection"] or evidence["evidence"] != currentEvidence:
            raise QuarantineError("review_evidence_required")
        targetPath = target["path"] + "/" + row["name"]
        try:
            canonicalCloudPath(targetPath)
        except ValueError:
            raise QuarantineError("observation_changed") from None
        remoteIds.add(row["remote_id"])
        items.append({"entryId": entryId, "generation": row["generation"], "remoteId": row["remote_id"],
            "sourcePath": row["path"], "sourceName": row["name"], "targetPath": targetPath,
            "size": row["size"], "reason": evidence["reason"], "evidence": currentEvidence})
    return {"reviewId": reviewId, "reviewSnapshotHash": sha256(review["snapshot_json"].encode()).hexdigest(),
        "inventoryJobId": job["id"], "inventoryRevision": job["revision"], "pathEpoch": job["path_epoch"],
        "rootPath": job["root_path"], "parentPath": target["observed_parent"], "directoryId": target["directory_id"],
        "targetEntryId": targetEntryId, "targetName": target["name"], "targetPath": target["path"],
        "targetRemoteId": target["remote_id"], "targetGeneration": target["generation"], "items": items}


def _owned(connection, planId, actorId, *, byRequest=False):
    inventoryId(planId)
    _permissionForPlan(connection, actorId)
    column = "request_id" if byRequest else "id"
    row = connection.execute(f"SELECT * FROM cloud_quarantine_plans WHERE {column}=? AND actor_id=?", (planId, actorId)).fetchone()
    if row is None:
        raise QuarantineError("not_found")
    return row


def _valid(connection, settings, row, snapshot, *, ignoreExecution=False):
    if row["status"] != "prepared":
        return row["status"]
    if not ignoreExecution and connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE plan_id=? LIMIT 1", (row["id"],)).fetchone():
        return "execution_accepted"
    now = time.time()
    if not math.isfinite(now) or now < row["created_at"] or now >= row["expires_at"]:
        return "expired"
    try:
        if row["policy_hash"] != quarantinePolicyHash(settings) or row["rule_version"] != RULE_VERSION:
            return "policy_changed"
        if row["snapshot_hash"] != sha256(row["snapshot_json"].encode()).hexdigest():
            return "observation_changed"
        if row["kind"] == "reverse":
            from app.service.cloud_quarantine_recovery_service import reversePlanInvalidReason
            return reversePlanInvalidReason(connection, row, snapshot)
        current = _snapshot(connection, settings, row["review_id"], row["actor_id"],
            [item["entryId"] for item in snapshot["items"]], snapshot["targetEntryId"])
        if snapshot != current:
            return "observation_changed"
    except (QuarantineError, CloudInventoryError) as exc:
        return exc.code
    return None


def _view(connection, settings, row):
    snapshot = json.loads(row["snapshot_json"])
    reason = _valid(connection, settings, row, snapshot)
    execution = connection.execute("SELECT request_id FROM cloud_quarantine_runs WHERE plan_id=? AND kind='move'", (row["id"],)).fetchone()
    return {**snapshot, "id": row["id"], "requestId": row["request_id"], "status": row["status"],
        "executionRequestId":execution[0] if execution else None,
        "kind": row["kind"], "revision": row["revision"], "ruleVersion": row["rule_version"], "policyHash": row["policy_hash"],
        "snapshotHash": row["snapshot_hash"], "createdAt": row["created_at"], "expiresAt": row["expires_at"],
        "valid": reason is None, "invalidReason": reason, "networkAccess": False, "writeAvailable": False}


def createQuarantinePlan(settings, *, requestId, reviewId, entryIds, targetEntryId, auditContext):
    inventoryId(requestId)
    inventoryId(reviewId)
    _limit(targetEntryId, 1, 2**63 - 1)
    if (not isinstance(entryIds, list) or not 1 <= len(entryIds) <= MAX_ITEMS
            or any(type(item) is not int or not 1 <= item <= 2**63 - 1 for item in entryIds)
            or len(set(entryIds)) != len(entryIds)):
        raise ValueError("须明确选择一至五个不同文件")
    actorId = auditContext.actor.id if auditContext.actor else None
    normalized = sorted(entryIds)
    requestHash = reviews._digest([reviewId, normalized, targetEntryId])
    with inventoryDb(settings, write=True) as connection:
        _permissionForPlan(connection, actorId)
        old = connection.execute("SELECT * FROM cloud_quarantine_plans WHERE request_id=?", (requestId,)).fetchone()
        if old is not None:
            if old["actor_id"] != actorId or old["request_hash"] != requestHash:
                raise QuarantineError("request_conflict")
            return _view(connection, settings, old)
        requireNoDirectoryExecution(connection)
        policy = quarantinePolicyHash(settings)
        snapshot = _snapshot(connection, settings, reviewId, actorId, normalized, targetEntryId)
        serialized = reviews._json(snapshot)
        logicalBytes = len(serialized.encode())
        count, total = connection.execute("SELECT COUNT(*),COALESCE(SUM(logical_bytes),0) FROM cloud_quarantine_plans").fetchone()
        if logicalBytes > MAX_SNAPSHOT_BYTES or count >= MAX_PLANS or total + logicalBytes > MAX_TOTAL_BYTES:
            raise QuarantineError("plan_capacity")
        now = time.time()
        if not math.isfinite(now):
            raise QuarantineError("clock_unavailable")
        planId = str(uuid4())
        connection.execute("INSERT INTO cloud_quarantine_plans "
            "(id,request_id,request_hash,actor_id,review_id,job_id,inventory_revision,path_epoch,policy_hash,rule_version,"
            "item_count,snapshot_json,snapshot_hash,logical_bytes,created_at,expires_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (planId, requestId, requestHash, actorId, reviewId,
                snapshot["inventoryJobId"], snapshot["inventoryRevision"], snapshot["pathEpoch"], policy, RULE_VERSION,
                len(normalized), serialized, sha256(serialized.encode()).hexdigest(), logicalBytes, now, now + PLAN_SECONDS, now))
        record_audit_event(settings, event_type="cloud_quarantine.plan", action="prepare", result="success",
            summary="固定本地隔离计划；未发送云请求", target_type="cloud_quarantine_plan", target_id=planId,
            actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent,
            detail={"itemCount": len(normalized), "logicalBytes": logicalBytes, "networkAccess": False}, connection=connection)
        return _view(connection, settings, _owned(connection, planId, actorId))


def getQuarantinePlan(settings, planId, actorId, *, byRequest=False):
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        return _view(connection, settings, _owned(connection, planId, actorId, byRequest=byRequest))


def listQuarantinePlans(settings, actorId, *, page=1, pageSize=20):
    _limit(page, 1, 1000)
    _limit(pageSize, 1, 50)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permissionForPlan(connection, actorId)
        total = connection.execute("SELECT COUNT(*) FROM cloud_quarantine_plans WHERE actor_id=?", (actorId,)).fetchone()[0]
        # 历史仅取分页摘要，避免为了列表重复解析全部固定证据。
        rows = connection.execute("SELECT id,request_id,review_id,job_id,status,kind,revision,item_count,created_at,expires_at "
            "FROM cloud_quarantine_plans WHERE actor_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (actorId, pageSize, (page - 1) * pageSize)).fetchall()
        return {"items": [{"id": row["id"], "requestId": row["request_id"], "reviewId": row["review_id"],
            "inventoryJobId": row["job_id"], "kind":row["kind"], "status": row["status"], "revision": row["revision"],
            "itemCount": row["item_count"], "createdAt": row["created_at"], "expiresAt": row["expires_at"]} for row in rows],
            "page": page, "pageSize": pageSize, "total": total, "networkAccess": False, "writeAvailable": False}


def cancelQuarantinePlan(settings, planId, *, expectedRevision, auditContext):
    _limit(expectedRevision, 0, 2**63 - 1)
    with inventoryDb(settings, write=True) as connection:
        actorId = auditContext.actor.id if auditContext.actor else None
        row = _owned(connection, planId, actorId)
        if connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE plan_id=? LIMIT 1", (planId,)).fetchone():
            raise QuarantineError("execution_accepted")
        if row["status"] != "prepared" or row["revision"] != expectedRevision:
            raise QuarantineError("revision_conflict")
        connection.execute("UPDATE cloud_quarantine_plans SET status='cancelled',revision=revision+1,updated_at=? WHERE id=?",
            (time.time(), planId))
        record_audit_event(settings, event_type="cloud_quarantine.plan", action="cancel", result="success",
            summary="取消本地隔离计划；未发送云请求", target_type="cloud_quarantine_plan", target_id=planId,
            actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent, connection=connection)
        return _view(connection, settings, _owned(connection, planId, actorId))


def quarantinePlanPolicy(settings, actorId):
    with inventoryDb(settings) as connection:
        _permissionForPlan(connection, actorId)
        try:
            digest, reason = quarantinePolicyHash(settings), None
        except (QuarantineError, CloudInventoryError) as exc:
            digest, reason = "", exc.code
        from app.service.cloud_directory_guard import directoryExecutionHeld
        try:
            _permission(connection,actorId,"cloud-quarantine:execute")
            canExecute = True
        except CloudDirectoryError:
            canExecute = False
        held = directoryExecutionHeld(connection)
        executionReason = reason or ("permission_denied" if not canExecute else "execution_disabled" if not settings.cloudDrive.quarantineEnabled else "directory_execution_held" if held else None)
        return {"planAvailable": reason is None, "reason": reason, "policyHash": digest,
            "executionEnabled": settings.cloudDrive.quarantineEnabled, "executionImplemented": True,
            "canExecute":canExecute,"executionAvailable":executionReason is None,"executionReason":executionReason,
            "controlledMode":True,"atomicIdentityGuaranteed":False,"automaticRescan":False,
            "targetName": settings.cloudDrive.quarantineFolderName, "limits": {"items": MAX_ITEMS, "seconds": PLAN_SECONDS,
                "plans": MAX_PLANS, "snapshotBytes": MAX_SNAPSHOT_BYTES, "totalLogicalBytes": MAX_TOTAL_BYTES,
                "executionSeconds":180,"baseRpc":3,"rpcPerItem":9,"bytes":1048576,"observeSeconds":90},
            "networkAccess": False, "writeAvailable": False}


def quarantineTargets(settings, reviewId, entryId, actorId):
    inventoryId(reviewId)
    _limit(entryId,1,2**63-1)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permissionForPlan(connection,actorId)
        try:
            review = reviews._owned(connection,reviewId,actorId)
        except CloudInventoryError:
            raise QuarantineError("not_found") from None
        reason = reviews._currentReason(connection,settings,review)
        if reason:
            raise QuarantineError(reason)
        source = _entry(connection,review["job_id"],entryId)
        targetRows = connection.execute("SELECT id,name FROM cloud_drive_inventory_entries INDEXED BY idx_cloud_quarantine_target "
            "WHERE job_id=? AND directory_id=? AND generation=? AND name=? AND is_directory=1 LIMIT 2",
            (review["job_id"],source["directory_id"],source["generation"],settings.cloudDrive.quarantineFolderName)).fetchall()
        if len(targetRows)>1:
            raise QuarantineError("invalid_quarantine_target")
        values = []
        for target in targetRows:
            _snapshot(connection,settings,reviewId,actorId,[entryId],target["id"])
            values.append({"id":target["id"],"name":target["name"]})
        return {"items":values,"reason":None if values else "quarantine_target_missing","networkAccess":False}
