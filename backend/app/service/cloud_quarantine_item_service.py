"""隔离逐项前后证据；不发RPC、不据成功返回猜测实际移动。"""

import json
import time

from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_inventory_service import inventoryDb

QuarantineError = actions.QuarantineError


def _checks(connection, runId, ordinal, *, beforeOrdinal=None):
    rows = list(reversed(connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE run_id=? AND (? IS NULL OR ordinal<?) "
        "ORDER BY ordinal DESC LIMIT 4", (runId,beforeOrdinal,beforeOrdinal)).fetchall()))
    if (len(rows) != 4 or any(row["method"] != "FindFileByPath" or row["phase"] != "responded"
            or row["item_ordinal"] != ordinal for row in rows)):
        raise QuarantineError("identity_precheck_required")
    return rows


def _object(row, path, objectId=None, *, directory=None, size=None):
    if row["request_path"] != path or row["response_code"] != "ok" or row["identity_json"] is None:
        return False
    identity = json.loads(row["identity_json"])
    return ((objectId is None or identity["objectId"] == objectId)
        and (directory is None or identity["isDirectory"] is directory)
        and (size is None or identity["size"] == size))


def _missing(row, path):
    return row["request_path"] == path and row["response_code"] == "not_found" and row["identity_json"] is None


def _prechecks(connection, row, snapshot, ordinal, *, beforeOrdinal=None):
    item, sourcePath, targetPath, folder = actions.itemPaths(snapshot, ordinal)
    checks = _checks(connection, row["id"], ordinal, beforeOrdinal=beforeOrdinal)
    first = connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE run_id=? ORDER BY ordinal LIMIT 3", (row["id"],)).fetchall()
    if (len(first) != 3 or [r["method"] for r in first] != ["GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath"]
            or any(r["phase"] != "responded" or r["response_code"] != "ok" for r in first)
            or not _object(first[2], "/", directory=True)):
        raise QuarantineError("identity_precheck_required")
    parent, destination, source, absent = checks
    baseline = json.loads(first[2]["identity_json"])["objectId"]
    sourceParent, targetParent = actions.itemParents(snapshot, ordinal)
    sourceParentId = baseline if sourceParent == "/" else snapshot["targetRemoteId"]
    targetParentId = baseline if targetParent == "/" else snapshot["targetRemoteId"]
    if ((snapshot.get("parentRemoteId") is not None and baseline != snapshot["parentRemoteId"])
            or not _object(parent, sourceParent, sourceParentId, directory=True)
            or not _object(destination, targetParent, targetParentId, directory=True)
            or not _object(source, sourcePath, item["remoteId"], directory=False, size=item["size"])
            or not _missing(absent, targetPath)
            or len({baseline, snapshot["targetRemoteId"], item["remoteId"]}) != 3):
        raise QuarantineError("identity_or_conflict_changed")
    now = time.time()
    if any(now < r["responded_at"] or now - r["responded_at"] > 30 for r in checks):
        raise QuarantineError("identity_precheck_expired")
    return checks, sourceParentId


def prepareQuarantineItem(settings, runId, token, ordinal):
    with inventoryDb(settings, write=True) as connection:
        row = actions._worker(connection, runId, token)
        actions._active(settings, connection, row, time.time())
        _, snapshot = actions._snapshot(connection, row)
        if connection.execute("SELECT 1 FROM cloud_quarantine_outcomes WHERE run_id=? AND ordinal=?", (runId, ordinal)).fetchone():
            raise QuarantineError("item_already_prepared")
        if connection.execute("SELECT COUNT(*) FROM cloud_quarantine_outcomes WHERE run_id=? AND ordinal<? AND state='verified'", (runId, ordinal)).fetchone()[0] != ordinal-1:
            raise QuarantineError("item_sequence_required")
        checks, parentId = _prechecks(connection, row, snapshot, ordinal)
        connection.execute("INSERT INTO cloud_quarantine_outcomes "
            "(run_id,ordinal,state,precheck_json,source_parent_id,target_parent_id,created_at) VALUES(?,?,'prepared',?,?,?,?)",
            (runId, ordinal, json.dumps([r["id"] for r in checks]), parentId,
             json.loads(checks[1]["identity_json"])["objectId"], time.time()))
        from app.service.cloud_quarantine_log_service import recordQuarantineLog
        recordQuarantineLog(settings,runId,"item_prechecked",connection=connection,ordinal=ordinal)


def requireMoveProof(connection, row, snapshot, ordinal):
    outcome = connection.execute("SELECT * FROM cloud_quarantine_outcomes WHERE run_id=? AND ordinal=? AND state='prepared'", (row["id"], ordinal)).fetchone()
    if outcome is None:
        raise QuarantineError("identity_precheck_required")
    checks = _checks(connection, row["id"], ordinal)
    if [r["id"] for r in checks] != json.loads(outcome["precheck_json"]):
        raise QuarantineError("identity_precheck_changed")
    _, parentId = _prechecks(connection, row, snapshot, ordinal)
    if outcome["source_parent_id"] != parentId or outcome["target_parent_id"] != json.loads(checks[1]["identity_json"])["objectId"]:
        raise QuarantineError("identity_precheck_changed")


def requireSendingMoveProof(connection, row, snapshot, rpcId):
    rpc = connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE id=? AND run_id=? AND method='MoveFile' AND phase='sending'", (rpcId,row["id"])).fetchone()
    if rpc is None:
        raise QuarantineError("rpc_state_changed")
    outcome = connection.execute("SELECT * FROM cloud_quarantine_outcomes WHERE run_id=? AND ordinal=? AND state='sending' AND move_rpc_id=?",
        (row["id"],rpc["item_ordinal"],rpcId)).fetchone()
    if outcome is None:
        raise QuarantineError("identity_precheck_required")
    checks,parentId = _prechecks(connection,row,snapshot,rpc["item_ordinal"],beforeOrdinal=rpc["ordinal"])
    _,source,_,_ = actions.itemPaths(snapshot,rpc["item_ordinal"])
    if ([r["id"] for r in checks] != json.loads(outcome["precheck_json"]) or rpc["request_path"] != source
            or outcome["source_parent_id"] != parentId
            or outcome["target_parent_id"] != json.loads(checks[1]["identity_json"])["objectId"]
            or connection.execute("SELECT MAX(ordinal) FROM cloud_quarantine_rpcs WHERE run_id=?", (row["id"],)).fetchone()[0] != rpc["ordinal"]):
        raise QuarantineError("identity_precheck_changed")
def verifyQuarantineItem(settings, runId, token, ordinal):
    with inventoryDb(settings, write=True) as connection:
        row = actions._worker(connection, runId, token)
        _, snapshot = actions._snapshot(connection, row)
        item, sourcePath, targetPath, folder = actions.itemPaths(snapshot, ordinal)
        outcome = connection.execute("SELECT * FROM cloud_quarantine_outcomes WHERE run_id=? AND ordinal=?", (runId, ordinal)).fetchone()
        if outcome is None or outcome["state"] != "responded":
            raise QuarantineError("move_response_required")
        checks = _checks(connection, runId, ordinal)
        move = connection.execute("SELECT ordinal,phase FROM cloud_quarantine_rpcs WHERE id=? AND run_id=?", (outcome["move_rpc_id"], runId)).fetchone()
        target, source, parent, destination = checks
        sourceParent, targetParent = actions.itemParents(snapshot, ordinal)
        common = (move is not None and move["phase"] == "responded" and checks[0]["ordinal"] > move["ordinal"]
            and _object(parent, sourceParent, outcome["source_parent_id"], directory=True)
            and _object(destination, targetParent, outcome["target_parent_id"], directory=True))
        moved = common and outcome["remote_reply"] == 1 and _object(target, targetPath, item["remoteId"], directory=False, size=item["size"]) and _missing(source, sourcePath)
        original = common and _object(source, sourcePath, item["remoteId"], directory=False, size=item["size"])
        targetOtherOrAbsent = (_missing(target, targetPath) or (_object(target, targetPath, directory=False)
            and json.loads(target["identity_json"])["objectId"] != item["remoteId"]))
        notApplied = original and targetOtherOrAbsent
        state = "verified" if moved else "not_applied" if notApplied else "review"
        reason = None if moved else "target_conflict_or_not_applied" if notApplied else "post_identity_changed"
        connection.execute("UPDATE cloud_quarantine_outcomes SET state=?,reason=?,postcheck_json=?,verified_at=? WHERE run_id=? AND ordinal=?",
            (state, reason, json.dumps([r["id"] for r in checks]), time.time(), runId, ordinal))
        if not moved:
            connection.execute("UPDATE cloud_quarantine_runs SET status='review',reason=?,revision=revision+1,updated_at=? WHERE id=?", (reason, time.time(), runId))
        from app.service.cloud_quarantine_log_service import recordQuarantineLog
        recordQuarantineLog(settings,runId,"item_verified" if moved else "item_review",connection=connection,ordinal=ordinal)
        return state
