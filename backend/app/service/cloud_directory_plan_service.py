"""从有界本地观察固定目录计划；本模块不连接云端，不授予执行权限。"""

from hashlib import sha256
import json
import time
import unicodedata
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service.cloud_inventory_advice_service import RULE_VERSION, _terms, _protected, _folderSuggestion, _SEASON
from app.service.cloud_inventory_service import CloudInventoryError, inventoryDb, inventoryId
from app.service.cloud_inventory_web_service import _trustedCa
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.utils.cloud_drive_paths import canonicalCloudPath, canonicalCd2Target, isWithinCloudRoot

PLAN_LIMIT = 1000
PLAN_SECONDS = 1800
MAX_ITEMS = 5
MAX_SIBLINGS = 1000


class CloudDirectoryError(RuntimeError):
    def __init__(self, code):
        super().__init__("目录操作停止：" + code + "；不会自动重试，请核对原任务")
        self.code = code


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _permission(connection, actorId, permission="cloud-directory:plan"):
    row = connection.execute("SELECT enabled,permissions_json FROM users WHERE id=?", (actorId,)).fetchone()
    try:
        permissions = json.loads(row["permissions_json"]) if row else []
        valid = isinstance(permissions, list) and all(isinstance(item, str) for item in permissions)
    except (ValueError, TypeError):
        valid = False
    if not valid or row is None or row["enabled"] != 1 or permission not in permissions:
        raise CloudDirectoryError("permission_denied")


def directoryPolicyHash(settings):
    config = settings.cloudDrive
    if not config.approvedRoot or not config.endpoint:
        raise CloudDirectoryError("scope_not_configured")
    ca = _trustedCa(settings)
    return _digest(["controlled-maintenance-v1", canonicalCd2Target(config.endpoint), config.approvedRoot,
        sha256(ca).hexdigest() if ca else "system-ca", config.minIntervalSeconds,
        config.failureCooldownSeconds, config.directoryRenameEnabled, config.directoryMappingRevision,
        RULE_VERSION, MAX_ITEMS, MAX_SIBLINGS])


def directoryNameKey(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _name(value):
    if (not isinstance(value, str) or not value or value != value.strip(" .") or value in {".", ".."}
            or any(char in '/\\:*?"<>|' or unicodedata.category(char).startswith("C") for char in value)
            or len(value.encode("utf-8")) > 240 or unicodedata.normalize("NFC", value) != value):
        raise CloudDirectoryError("unsupported_name")
    compatible = unicodedata.normalize("NFKC", value)
    if any(char in '/\\:*?"<>|' for char in compatible) or compatible != compatible.strip(" ."):
        raise CloudDirectoryError("unsupported_name")
    stem = compatible.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise CloudDirectoryError("unsupported_name")
    return value


def _revision(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("修订号须为非负整数")


def _snapshot(connection, settings, jobId, actorId, expectedRevision, entryIds, terms):
    job = connection.execute("SELECT j.*,s.endpoint_hash FROM cloud_drive_inventory_jobs j "
        "JOIN cloud_drive_read_sessions s ON s.id=j.session_id WHERE j.id=? AND j.actor_id=?", (jobId, actorId)).fetchone()
    if job is None:
        raise CloudDirectoryError("not_found")
    if (job["revision"] != expectedRevision or job["cache_state"] != "available" or job["status"] != "completed"
            or job["worker_token"] is not None or job["cancel_requested"]):
        raise CloudDirectoryError("observation_changed")
    from app.service.cloud_directory_path_service import cloudPathEpoch
    if job["path_epoch"] != cloudPathEpoch(connection)["revision"]:
        raise CloudDirectoryError("path_revision_changed")
    if (job["endpoint_hash"] != sha256(canonicalCd2Target(settings.cloudDrive.endpoint).encode()).hexdigest()
            or not isWithinCloudRoot(job["root_path"], settings.cloudDrive.approvedRoot)):
        raise CloudDirectoryError("scope_changed")
    rows = []
    for entryId in sorted(entryIds):
        row = connection.execute("SELECT e.*,d.path AS observed_parent,d.status AS directory_status,d.generation AS current_generation "
            "FROM cloud_drive_inventory_entries e JOIN cloud_drive_inventory_directories d ON d.id=e.directory_id "
            "AND d.job_id=e.job_id WHERE e.id=? AND e.job_id=?", (entryId, jobId)).fetchone()
        if row is None:
            raise CloudDirectoryError("entry_not_found")
        if (not row["is_directory"] or row["directory_status"] != "complete"
                or row["generation"] != row["current_generation"] or not row["remote_id"]
                or len(row["remote_id"].encode()) > 1024
                or any(unicodedata.category(char).startswith("C") for char in row["remote_id"])):
            raise CloudDirectoryError("identity_or_observation_unavailable")
        if connection.execute("SELECT 1 FROM cloud_drive_inventory_keeps WHERE entry_id=? AND job_id=?", (entryId, jobId)).fetchone():
            raise CloudDirectoryError("protected_entry")
        path = canonicalCloudPath(row["path"])
        if (path == job["root_path"] or not isWithinCloudRoot(path, job["root_path"])
                or row["name"] != path.rpartition("/")[2] or path.rpartition("/")[0] != row["observed_parent"]):
            raise CloudDirectoryError("scope_changed")
        _name(row["name"])
        rows.append(row)
    parents = {row["path"].rpartition("/")[0] for row in rows}
    if len(parents) != 1 or len({row["remote_id"] for row in rows}) != len(rows):
        raise CloudDirectoryError("same_parent_distinct_identity_required")
    parent = parents.pop()
    # 只核对一个已批准父目录；超出本地上限就拒绝，不为2PB库做全量名称归一化扫描。
    directoryId = rows[0]["directory_id"]
    if any(row["directory_id"] != directoryId for row in rows):
        raise CloudDirectoryError("same_parent_distinct_identity_required")
    siblings = connection.execute("SELECT name FROM cloud_drive_inventory_entries "
        "INDEXED BY idx_cloud_inventory_entry_directory WHERE directory_id=? AND generation=? AND job_id=? "
        "ORDER BY id LIMIT ?", (directoryId, rows[0]["generation"], jobId, MAX_SIBLINGS + 1)).fetchall()
    if len(siblings) > MAX_SIBLINGS:
        raise CloudDirectoryError("smaller_scope_required")
    siblingKeys = {directoryNameKey(row["name"]) for row in siblings}
    targets = set()
    items = []
    for row in rows:
        item = {"id": row["id"], "name": row["name"], "relativePath": row["path"].lstrip("/"),
                "isDirectory": True, "directoryComplete": True}
        # 批准根可能已位于季/光盘/关联素材目录内，不能截掉根后绕过结构保护。
        protection = "protected_season" if any(_SEASON.fullmatch(part) for part in row["path"].split("/")) else _protected(item)
        proposal = _folderSuggestion(item, set(entryIds), terms, protection)
        if proposal["status"] != "suggested":
            raise CloudDirectoryError("no_safe_suggestion")
        name = _name(proposal["proposedName"])
        try:
            canonicalCloudPath(parent + "/" + name)
        except ValueError:
            raise CloudDirectoryError("unsupported_name") from None
        key = directoryNameKey(name)
        if key in siblingKeys or key in targets:
            raise CloudDirectoryError("observed_name_conflict")
        targets.add(key)
        items.append({"entryId": row["id"], "generation": row["generation"], "remoteId": row["remote_id"],
            "sourcePath": row["path"], "sourceName": row["name"], "targetName": name, "targetPath": parent + "/" + name})
    return job["root_path"], parent, items


def _owned(connection, planId, actorId):
    inventoryId(planId)
    _permission(connection, actorId)
    row = connection.execute("SELECT * FROM cloud_directory_plans WHERE id=? AND actor_id=?", (planId, actorId)).fetchone()
    if row is None:
        raise CloudDirectoryError("not_found")
    return row


def _items(connection, planId):
    return [{"entryId": row["entry_id"], "generation": row["observation_generation"], "remoteId": row["remote_id"],
        "sourcePath": row["source_path"], "sourceName": row["source_name"], "targetName": row["target_name"],
        "targetPath": row["target_path"]} for row in connection.execute(
            "SELECT * FROM cloud_directory_plan_items WHERE plan_id=? ORDER BY ordinal", (planId,))]


def _valid(connection, settings, row, items):
    if row["status"] != "prepared":
        return row["status"]
    if connection.execute("SELECT 1 FROM cloud_directory_executions WHERE plan_id=?", (row["id"],)).fetchone():
        return "execution_accepted"
    now = time.time()
    if now < row["created_at"] or now >= row["expires_at"]:
        return "expired"
    try:
        if row["policy_hash"] != directoryPolicyHash(settings):
            return "policy_changed"
        if row["kind"] == "reverse":
            from app.service.cloud_directory_reverse_service import reversePlanInvalidReason
            return reversePlanInvalidReason(connection, settings, row, items)
        ids, terms = [item["entryId"] for item in items], json.loads(row["terms_json"])
        if row["rule_version"] != RULE_VERSION or row["rules_hash"] != _digest([RULE_VERSION, terms, sorted(ids)]):
            return "rules_changed"
        root, parent, current = _snapshot(connection, settings, row["inventory_job_id"], row["actor_id"], row["inventory_revision"], ids, terms)
        if (_digest(items) != row["snapshot_hash"] or current != items
                or root != row["root_path"] or parent != row["parent_path"]):
            return "observation_changed"
    except (CloudDirectoryError, CloudInventoryError) as exc:
        return exc.code
    return None


def _view(connection, settings, row):
    items = _items(connection, row["id"])
    reason = _valid(connection, settings, row, items)
    return {"id": row["id"], "requestId": row["request_id"], "inventoryJobId": row["inventory_job_id"],
        "inventoryRevision": row["inventory_revision"], "revision": row["revision"], "status": row["status"],
        "valid": reason is None, "invalidReason": reason, "policyHash": row["policy_hash"], "rulesHash": row["rules_hash"],
        "kind": row["kind"], "originPlanId": row["origin_plan_id"], "originRevision": row["origin_revision"], "basisEpoch": row["basis_epoch"],
        "ruleVersion": row["rule_version"], "parentPath": row["parent_path"], "items": items,
        "createdAt": row["created_at"], "expiresAt": row["expires_at"], "networkAccess": False, "writeAvailable": False}


def createDirectoryPlan(settings, *, requestId, jobId, expectedRevision, entryIds, folderTerms, auditContext):
    inventoryId(requestId)
    inventoryId(jobId)
    _revision(expectedRevision)
    if (not isinstance(entryIds, list) or not 1 <= len(entryIds) <= MAX_ITEMS
            or any(type(item) is not int or not 1 <= item <= 2**63 - 1 for item in entryIds)
            or len(set(entryIds)) != len(entryIds) or auditContext.actor is None):
        raise ValueError("须明确选择一至五个不同目录并关联用户")
    terms = _terms(folderTerms)
    if any(unicodedata.category(char).startswith("C") for term in terms for char in term):
        raise ValueError("净化词不能包含控制、格式或代理字符")
    actorId = auditContext.actor.id
    requestHash = _digest([actorId, jobId, expectedRevision, sorted(entryIds), terms])
    with inventoryDb(settings, write=True) as connection:
        _permission(connection, actorId)
        requireNoDirectoryExecution(connection)
        old = connection.execute("SELECT * FROM cloud_directory_plans WHERE request_id=?", (requestId,)).fetchone()
        if old is not None:
            if old["actor_id"] != actorId or old["request_hash"] != requestHash:
                raise CloudDirectoryError("request_conflict")
            return _view(connection, settings, old)
        policy = directoryPolicyHash(settings)
        root, parent, items = _snapshot(connection, settings, jobId, actorId, expectedRevision, entryIds, terms)
        if connection.execute("SELECT COUNT(*) FROM cloud_directory_plans").fetchone()[0] >= PLAN_LIMIT:
            raise CloudDirectoryError("plan_capacity")
        planId, now = str(uuid4()), time.time()
        rulesHash = _digest([RULE_VERSION, terms, sorted(entryIds)])
        connection.execute("INSERT INTO cloud_directory_plans "
            "(id,request_id,request_hash,actor_id,inventory_job_id,inventory_revision,policy_hash,rules_hash,rule_version,"
            "terms_json,root_path,parent_path,snapshot_hash,created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (planId, requestId, requestHash, actorId, jobId, expectedRevision, policy, rulesHash, RULE_VERSION,
                json.dumps(terms, ensure_ascii=False), root, parent, _digest(items), now, now + PLAN_SECONDS, now))
        connection.executemany("INSERT INTO cloud_directory_plan_items "
            "(plan_id,ordinal,entry_id,observation_generation,remote_id,source_path,source_name,target_name,target_path) "
            "VALUES(?,?,?,?,?,?,?,?,?)", [(planId, index, item["entryId"], item["generation"], item["remoteId"],
                item["sourcePath"], item["sourceName"], item["targetName"], item["targetPath"]) for index, item in enumerate(items, 1)])
        record_audit_event(settings, event_type="cloud_directory.plan", action="prepare", result="success",
            summary="固定本地目录改名计划；未发送云请求", target_type="cloud_directory_plan", target_id=planId,
            actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent,
            detail={"itemCount": len(items), "ruleVersion": RULE_VERSION, "snapshotHash": _digest(items)}, connection=connection)
        return _view(connection, settings, _owned(connection, planId, actorId))


def getDirectoryPlan(settings, planId, actorId):
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        return _view(connection, settings, _owned(connection, planId, actorId))


def directoryPlanByRequest(settings, requestId, actorId):
    inventoryId(requestId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permission(connection, actorId)
        row = connection.execute("SELECT * FROM cloud_directory_plans WHERE request_id=? AND actor_id=?", (requestId, actorId)).fetchone()
        if row is None:
            raise CloudDirectoryError("not_found")
        return _view(connection, settings, row)


def listDirectoryPlans(settings, actorId, *, page=1, pageSize=20):
    if type(page) is not int or not 1 <= page <= 1000 or type(pageSize) is not int or not 1 <= pageSize <= 50:
        raise ValueError("目录计划分页参数无效")
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permission(connection, actorId)
        total = connection.execute("SELECT COUNT(*) FROM cloud_directory_plans WHERE actor_id=?", (actorId,)).fetchone()[0]
        page = min(page, max(1, (total + pageSize - 1) // pageSize))
        rows = connection.execute("SELECT p.id,p.request_id,p.kind,p.parent_path,p.status,p.created_at,p.expires_at,"
            "e.request_id AS execution_request,e.status AS execution_status,e.reason AS execution_reason,e.used_rpc,e.max_rpc,"
            "(SELECT request_id FROM cloud_directory_recoveries r WHERE r.plan_id=p.id ORDER BY attempt DESC LIMIT 1) AS recovery_request,"
            "(SELECT COUNT(*) FROM cloud_directory_plan_items i WHERE i.plan_id=p.id) AS item_count "
            "FROM cloud_directory_plans p LEFT JOIN cloud_directory_executions e ON e.plan_id=p.id "
            "WHERE p.actor_id=? ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?", (actorId, pageSize, (page - 1) * pageSize)).fetchall()
        return {"items": [{"id": row["id"], "requestId": row["request_id"], "kind": row["kind"], "parentPath": row["parent_path"],
            "status": row["status"], "createdAt": row["created_at"], "expiresAt": row["expires_at"], "itemCount": row["item_count"],
            "executionRequestId": row["execution_request"], "executionStatus": row["execution_status"], "executionReason": row["execution_reason"],
            "recoveryRequestId": row["recovery_request"], "usedRpc": row["used_rpc"], "maxRpc": row["max_rpc"]} for row in rows],
            "page": page, "pageSize": pageSize, "total": total, "networkAccess": False, "replayed": False}


def cancelDirectoryPlan(settings, planId, *, expectedRevision, auditContext):
    _revision(expectedRevision)
    with inventoryDb(settings, write=True) as connection:
        row = _owned(connection, planId, auditContext.actor.id)
        if connection.execute("SELECT 1 FROM cloud_directory_executions WHERE plan_id=?", (planId,)).fetchone():
            raise CloudDirectoryError("execution_accepted")
        if row["revision"] != expectedRevision or row["status"] != "prepared":
            raise CloudDirectoryError("revision_conflict")
        connection.execute("UPDATE cloud_directory_plans SET status='cancelled',revision=revision+1,updated_at=? WHERE id=?",
                           (time.time(), planId))
        record_audit_event(settings, event_type="cloud_directory.plan", action="cancel", result="success",
            summary="取消本地目录改名计划；未发送云请求", target_type="cloud_directory_plan", target_id=planId,
            actor=auditContext.actor, connection=connection)
        return _view(connection, settings, _owned(connection, planId, auditContext.actor.id))
