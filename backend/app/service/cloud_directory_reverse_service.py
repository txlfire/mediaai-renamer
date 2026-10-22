"""由已提交位置修订创建反向新计划，不自动补偿、不发送云请求。"""

import json
import time
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service.cloud_directory_plan_service import (CloudDirectoryError, _owned, _permission, _revision, _name, _digest, _view,
    directoryPolicyHash, directoryNameKey, PLAN_LIMIT, PLAN_SECONDS, RULE_VERSION, _protected, _SEASON)
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.service.cloud_directory_recovery_service import recoveryPolicyHash
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_inventory_service import inventoryDb, inventoryId, CloudInventoryError
from app.utils.cloud_drive_paths import canonicalCloudPath, isWithinCloudRoot


def _reverseBasis(connection, settings, originPlanId, actorId):
    origin = _owned(connection, originPlanId, actorId)
    execution = connection.execute("SELECT * FROM cloud_directory_executions WHERE plan_id=? AND actor_id=?", (originPlanId, actorId)).fetchone()
    if (execution is None or execution["slot_held"] or execution["worker_pid"] is not None
            or not (execution["status"] == "completed" or execution["status"] == "review" and execution["reason"] == "positions_reconciled")):
        raise CloudDirectoryError("reverse_requires_committed_positions")
    if execution["policy_hash"] != recoveryPolicyHash(settings):
        raise CloudDirectoryError("execution_policy_changed")
    epoch = cloudPathEpoch(connection)["revision"]
    rows = connection.execute("SELECT r.*,p.entry_id,p.observation_generation,p.source_name,p.target_name,p.remote_id AS expected_id "
        "FROM cloud_directory_path_revisions r JOIN cloud_directory_plan_items p ON p.plan_id=r.plan_id AND p.ordinal=r.ordinal "
        "WHERE r.plan_id=? AND r.epoch=? ORDER BY r.ordinal", (originPlanId, epoch)).fetchall()
    if not 1 <= len(rows) <= 5:
        raise CloudDirectoryError("reverse_basis_outdated_or_no_changes")
    items = []
    for row in rows:
        sourceName, targetName = _name(row["target_name"]), _name(row["source_name"])
        parent = origin["parent_path"]
        if (row["remote_id"] != row["expected_id"] or row["source_path"] != parent + "/" + targetName
                or row["target_path"] != parent + "/" + sourceName or not isWithinCloudRoot(parent, settings.cloudDrive.approvedRoot)):
            raise CloudDirectoryError("reverse_basis_changed")
        for name in (sourceName, targetName):
            path = canonicalCloudPath(parent + "/" + name)
            item = {"name": name, "relativePath": path.lstrip("/"), "isDirectory": True}
            if any(_SEASON.fullmatch(part) for part in path.split("/")) or _protected(item):
                raise CloudDirectoryError("protected_entry")
        items.append({"entryId": row["entry_id"], "generation": row["observation_generation"], "remoteId": row["remote_id"],
            "sourcePath": parent + "/" + sourceName, "sourceName": sourceName, "targetName": targetName, "targetPath": parent + "/" + targetName})
    sourceKeys = {directoryNameKey(item["sourceName"]) for item in items}
    targetKeys = {directoryNameKey(item["targetName"]) for item in items}
    if (len(sourceKeys) != len(items) or len(targetKeys) != len(items) or sourceKeys & targetKeys
            or len({item["remoteId"] for item in items}) != len(items)):
        raise CloudDirectoryError("observed_name_conflict")
    return origin, execution["revision"], epoch, items


def reversePlanInvalidReason(connection, settings, row, items):
    try:
        origin, revision, epoch, current = _reverseBasis(connection, settings, row["origin_plan_id"], row["actor_id"])
        if (revision != row["origin_revision"] or epoch != row["basis_epoch"] or current != items
                or _digest(items) != row["snapshot_hash"] or row["parent_path"] != origin["parent_path"] or row["root_path"] != origin["root_path"]):
            return "reverse_basis_changed"
        if row["rule_version"] != RULE_VERSION or row["rules_hash"] != _digest(["reverse-v1", row["origin_plan_id"], revision, epoch]):
            return "rules_changed"
    except (CloudDirectoryError, CloudInventoryError) as exc:
        return exc.code
    except (ValueError, TypeError):
        return "reverse_basis_changed"
    return None


def createReverseDirectoryPlan(settings, originPlanId, *, requestId, expectedRevision, auditContext):
    inventoryId(originPlanId)
    inventoryId(requestId)
    _revision(expectedRevision)
    if auditContext.actor is None:
        raise ValueError("须关联当前用户")
    actorId = auditContext.actor.id
    requestHash = _digest(["reverse-v1", actorId, originPlanId, expectedRevision])
    with inventoryDb(settings, write=True) as connection:
        _permission(connection, actorId)
        requireNoDirectoryExecution(connection)
        old = connection.execute("SELECT * FROM cloud_directory_plans WHERE request_id=?", (requestId,)).fetchone()
        if old is not None:
            if old["actor_id"] != actorId or old["request_hash"] != requestHash:
                raise CloudDirectoryError("request_conflict")
            return _view(connection, settings, old)
        origin, revision, epoch, items = _reverseBasis(connection, settings, originPlanId, actorId)
        if revision != expectedRevision:
            raise CloudDirectoryError("revision_conflict")
        if connection.execute("SELECT COUNT(*) FROM cloud_directory_plans").fetchone()[0] >= PLAN_LIMIT:
            raise CloudDirectoryError("plan_capacity")
        planId, now = str(uuid4()), time.time()
        connection.execute("INSERT INTO cloud_directory_plans "
            "(id,request_id,request_hash,actor_id,inventory_job_id,inventory_revision,policy_hash,rules_hash,rule_version,terms_json,root_path,parent_path,snapshot_hash,"
            "created_at,expires_at,updated_at,kind,origin_plan_id,origin_revision,basis_epoch) VALUES(?,?,?,?,?,?,?,?,?,'[]',?,?,?,?,?,?,'reverse',?,?,?)",
            (planId, requestId, requestHash, actorId, origin["inventory_job_id"], origin["inventory_revision"], directoryPolicyHash(settings),
                _digest(["reverse-v1", originPlanId, revision, epoch]), RULE_VERSION, origin["root_path"], origin["parent_path"], _digest(items),
                now, now + PLAN_SECONDS, now, originPlanId, revision, epoch))
        connection.executemany("INSERT INTO cloud_directory_plan_items(plan_id,ordinal,entry_id,observation_generation,remote_id,source_path,source_name,target_name,target_path) VALUES(?,?,?,?,?,?,?,?,?)",
            [(planId, ordinal, item["entryId"], item["generation"], item["remoteId"], item["sourcePath"], item["sourceName"], item["targetName"], item["targetPath"])
             for ordinal, item in enumerate(items, 1)])
        record_audit_event(settings, event_type="cloud_directory.plan", action="prepare_reverse", result="success",
            summary="固定反向新计划；未改名、不自动补偿", target_type="cloud_directory_plan", target_id=planId, actor=auditContext.actor,
            detail={"originPlanId": originPlanId, "basisEpoch": epoch, "itemCount": len(items), "networkAccess": False}, connection=connection)
        return _view(connection, settings, _owned(connection, planId, actorId))
