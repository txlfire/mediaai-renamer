"""仅基于本地观察保存人工复核；无凭据、云端连接或动作执行入口。"""

from hashlib import sha256
import json
import time
import unicodedata
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.service.cloud_drive_budget_service import requireCloudActor
from app.service.cloud_inventory_advice_service import RULE_VERSION, _protected, _PROMOTION, _URL
from app.service.cloud_inventory_service import CloudInventoryError, inventoryDb, inventoryId, _limit
from app.utils.cloud_drive_paths import canonicalCd2Target, isWithinCloudRoot

MAX_REVIEW_ITEMS = 100
MAX_REVIEW_RECORDS = 200
MAX_REVIEW_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
DECISIONS = {"keep", "review", "suggest_quarantine", "suggest_delete"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _configHash(settings):
    config = settings.cloudDrive
    return _digest([config.endpoint, config.approvedRoot, config.directoryMappingRevision])


def _actor(connection, actorId):
    requireCloudActor(connection, actorId)


def _items(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_REVIEW_ITEMS:
        raise ValueError("每次复核须明确选择1至100个文件")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"entryId", "decision", "reason"}:
            raise ValueError("复核项仅允许文件ID、结论和人工理由")
        entryId, decision, reason = value["entryId"], value["decision"], value["reason"]
        _limit(entryId, 1, 2**63 - 1)
        if entryId in seen or not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("复核项重复或结论无效")
        if (not isinstance(reason, str) or len(reason) > 500 or reason != reason.strip()
                or any(unicodedata.category(char).startswith("C") for char in reason)):
            raise ValueError("人工理由须为最多500字符的单行文本")
        if decision.startswith("suggest_") and not reason:
            raise ValueError("隔离或删除建议须填写人工理由；此建议不授权执行")
        seen.add(entryId)
        result.append(dict(value))
    return sorted(result, key=lambda item: item["entryId"])


def _owned(connection, reviewId, actorId, *, byRequest=False):
    inventoryId(reviewId)
    _actor(connection, actorId)
    column = "request_id" if byRequest else "id"
    row = connection.execute(f"SELECT * FROM cloud_ad_reviews WHERE {column}=? AND actor_id=?", (reviewId, actorId)).fetchone()
    if row is None:
        raise CloudInventoryError("review_not_found")
    return row


def _currentReason(connection, settings, row):
    job = connection.execute("SELECT revision,cache_state,path_epoch FROM cloud_drive_inventory_jobs WHERE id=? AND actor_id=?",
        (row["job_id"], row["actor_id"])).fetchone()
    if job is None or job["cache_state"] != "available":
        return "observations_cleared"
    if row["path_epoch"] != cloudPathEpoch(connection)["revision"]:
        return "path_revision_changed"
    if row["config_hash"] != _configHash(settings):
        return "scope_changed"
    if row["inventory_revision"] != job["revision"] or row["rule_version"] != RULE_VERSION:
        return "observation_changed"
    return None


def _result(connection, settings, row):
    reason = _currentReason(connection, settings, row)
    return {**json.loads(row["snapshot_json"]), "id": row["id"], "requestId": row["request_id"],
        "createdAt": row["created_at"], "snapshotHash": sha256(row["snapshot_json"].encode()).hexdigest(),
        "sourceCurrent": reason is None, "staleReason": reason, "networkAccess": False,
        "contentReadBytes": 0, "writeAvailable": False, "executionAuthorized": False}


def createCloudAdReview(settings, *, requestId, jobId, expectedRevision, items, confirmLocalOnly, auditContext):
    inventoryId(requestId)
    inventoryId(jobId)
    _limit(expectedRevision, 0, 2**63 - 1)
    normalized = _items(items)
    if confirmLocalOnly is not True:
        raise ValueError("须确认仅保存本地人工复核，不执行云操作")
    actorId = auditContext.actor.id if auditContext.actor else None
    requestHash = _digest([jobId, expectedRevision, normalized])
    with inventoryDb(settings, write=True) as connection:
        _actor(connection, actorId)
        existing = connection.execute("SELECT * FROM cloud_ad_reviews WHERE request_id=?", (requestId,)).fetchone()
        if existing is not None:
            if existing["actor_id"] != actorId or existing["request_hash"] != requestHash:
                raise CloudInventoryError("request_conflict")
            return _result(connection, settings, existing)
        requireNoDirectoryExecution(connection)
        job = connection.execute("SELECT * FROM cloud_drive_inventory_jobs WHERE id=? AND actor_id=?", (jobId, actorId)).fetchone()
        if job is None:
            raise CloudInventoryError("job_not_found")
        if (job["cache_state"] != "available" or job["revision"] != expectedRevision or job["status"] != "completed"
                or job["worker_token"] is not None or job["cancel_requested"]):
            raise CloudInventoryError("observation_changed")
        epoch = cloudPathEpoch(connection)["revision"]
        if job["path_epoch"] != epoch:
            raise CloudInventoryError("path_revision_changed")
        session = connection.execute("SELECT endpoint_hash FROM cloud_drive_read_sessions WHERE id=?", (job["session_id"],)).fetchone()
        if (not settings.cloudDrive.approvedRoot or not isWithinCloudRoot(job["root_path"], settings.cloudDrive.approvedRoot)
                or session is None or (settings.cloudDrive.endpoint and session["endpoint_hash"] !=
                    sha256(canonicalCd2Target(settings.cloudDrive.endpoint).encode()).hexdigest())):
            raise CloudInventoryError("scope_changed")
        snapshotItems = []
        for item in normalized:
            row = connection.execute("SELECT e.*,d.status AS directory_status,d.generation AS current_generation "
                "FROM cloud_drive_inventory_entries e JOIN cloud_drive_inventory_directories d ON d.id=e.directory_id "
                "AND d.job_id=e.job_id WHERE e.id=? AND e.job_id=?", (item["entryId"], jobId)).fetchone()
            if row is None:
                raise CloudInventoryError("entry_not_found")
            if row["is_directory"] or row["directory_status"] != "complete" or row["generation"] != row["current_generation"]:
                raise CloudInventoryError("review_file_incomplete")
            protected = _protected({"name": row["name"], "relativePath": row["path"], "isDirectory": False})
            if connection.execute("SELECT 1 FROM cloud_drive_inventory_keeps WHERE entry_id=? AND job_id=?", (row["id"], jobId)).fetchone():
                protected = "user_keep"
            evidence = [] if protected else [code for code, matched in (
                ("promotional_phrase", any(term in row["name"] for term in _PROMOTION)),
                ("domain_or_url_in_name", bool(_URL.search(row["name"])))) if matched]
            if protected and item["decision"] != "keep":
                raise CloudInventoryError("protected_entry")
            if item["decision"].startswith("suggest_") and (not evidence or not row["remote_id"]):
                raise CloudInventoryError("review_evidence_required")
            snapshotItems.append({**item, "name": row["name"], "path": row["path"], "size": row["size"],
                "remoteId": row["remote_id"], "generation": row["generation"], "protection": protected,
                "evidence": evidence, "evidenceLevel": "medium" if len(evidence) > 1 else "low" if evidence else "none"})
        snapshot = {"jobId": jobId, "inventoryRevision": expectedRevision, "pathEpoch": epoch,
            "ruleVersion": RULE_VERSION, "items": snapshotItems}
        serialized = _json(snapshot)
        size = len(serialized.encode())
        count, totalBytes = connection.execute("SELECT COUNT(*),COALESCE(SUM(logical_bytes),0) FROM cloud_ad_reviews").fetchone()
        if size > MAX_REVIEW_BYTES or count >= MAX_REVIEW_RECORDS or totalBytes + size > MAX_TOTAL_BYTES:
            raise CloudInventoryError("review_capacity")
        reviewId, now = str(uuid4()), time.time()
        connection.execute("INSERT INTO cloud_ad_reviews(id,request_id,request_hash,actor_id,job_id,inventory_revision,"
            "path_epoch,config_hash,rule_version,item_count,snapshot_json,logical_bytes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (reviewId, requestId, requestHash, actorId, jobId, expectedRevision, epoch, _configHash(settings), RULE_VERSION, len(normalized), serialized, size, now))
        record_audit_event(settings, event_type="cloud_drive.ad_review", action="ad_review_saved", result="success",
            summary="保存本地广告人工复核；未授权云操作", target_type="cloud_ad_review", target_id=reviewId,
            actor=auditContext.actor, detail={"itemCount": len(normalized), "logicalBytes": size, "remoteActions": 0},
            ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent, connection=connection)
        return _result(connection, settings, _owned(connection, reviewId, actorId))


def getCloudAdReview(settings, reviewId, actorId, *, byRequest=False):
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        return _result(connection, settings, _owned(connection, reviewId, actorId, byRequest=byRequest))


def listCloudAdReviews(settings, actorId, *, page=1, pageSize=20):
    _limit(page, 1, 1000)
    _limit(pageSize, 1, 50)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _actor(connection, actorId)
        total = connection.execute("SELECT COUNT(*) FROM cloud_ad_reviews WHERE actor_id=?", (actorId,)).fetchone()[0]
        rows = connection.execute("SELECT id,request_id,job_id,actor_id,inventory_revision,path_epoch,config_hash,rule_version,item_count,created_at "
            "FROM cloud_ad_reviews WHERE actor_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (actorId, pageSize, (page - 1) * pageSize)).fetchall()
        return {"items": [{"id": row["id"], "requestId": row["request_id"], "jobId": row["job_id"],
            "createdAt": row["created_at"], "itemCount": row["item_count"],
            "sourceCurrent": _currentReason(connection, settings, row) is None} for row in rows],
            "page": page, "pageSize": pageSize, "total": total, "networkAccess": False, "writeAvailable": False}
