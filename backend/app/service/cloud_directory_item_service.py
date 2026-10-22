"""逐项目录身份前后证据；只检查已持久化的精确查询，不自行发请求或猜测补偿。"""

import json
import time

from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_inventory_service import inventoryDb
from app.service.cloud_directory_log_service import recordDirectoryLog


def _planItem(connection, planId, ordinal):
    if type(ordinal) is not int or not 1 <= ordinal <= 5:
        raise ValueError("目录动作序号无效")
    row = connection.execute("SELECT * FROM cloud_directory_plan_items WHERE plan_id=? AND ordinal=?", (planId, ordinal)).fetchone()
    if row is None:
        raise CloudDirectoryError("item_not_found")
    return row


def _lastChecks(connection, planId, ordinal):
    rows = connection.execute("SELECT * FROM cloud_directory_rpc_intents WHERE plan_id=? ORDER BY ordinal DESC LIMIT 3", (planId,)).fetchall()
    rows = list(reversed(rows))
    if len(rows) != 3 or any(row["phase"] != "responded" or row["method"] != "FindFileByPath"
                           or row["item_ordinal"] != ordinal for row in rows):
        raise CloudDirectoryError("identity_precheck_required")
    return rows


def prepareDirectoryItem(settings, planId, token, ordinal):
    from app.service.cloud_directory_admission_service import _worker, _active
    with inventoryDb(settings, write=True) as connection:
        execution = _worker(connection, planId, token)
        _active(settings, connection, execution, time.time())
        item = _planItem(connection, planId, ordinal)
        if connection.execute("SELECT 1 FROM cloud_directory_item_outcomes WHERE plan_id=? AND ordinal=?", (planId, ordinal)).fetchone():
            raise CloudDirectoryError("item_already_prepared")
        if connection.execute("SELECT COUNT(*) FROM cloud_directory_item_outcomes WHERE plan_id=? AND ordinal<? AND state='verified'", (planId, ordinal)).fetchone()[0] != ordinal - 1:
            raise CloudDirectoryError("item_sequence_required")
        parent, source, target = _lastChecks(connection, planId, ordinal)
        baseline = connection.execute("SELECT remote_id FROM cloud_directory_rpc_intents WHERE plan_id=? AND method='FindFileByPath' "
            "AND item_ordinal IS NULL AND request_path='/' AND phase='responded' AND response_code='ok' ORDER BY ordinal LIMIT 1", (planId,)).fetchone()
        if (parent["request_path"] != "/" or parent["response_code"] != "ok" or not parent["remote_id"]
                or baseline is None or baseline["remote_id"] != parent["remote_id"]
                or source["request_path"] != "/" + item["source_name"] or source["response_code"] != "ok"
                or source["remote_id"] != item["remote_id"] or parent["remote_id"] == source["remote_id"]
                or target["request_path"] != "/" + item["target_name"] or target["response_code"] != "not_found"
                or target["remote_id"] is not None):
            raise CloudDirectoryError("identity_or_conflict_changed")
        # 已有其他条目的父身份也必须一致；映射变化不能借每项重新建立基线绕过。
        old = connection.execute("SELECT parent_remote_id FROM cloud_directory_item_outcomes WHERE plan_id=? LIMIT 1", (planId,)).fetchone()
        if old is not None and old["parent_remote_id"] != parent["remote_id"]:
            raise CloudDirectoryError("parent_identity_changed")
        connection.execute("INSERT INTO cloud_directory_item_outcomes "
            "(plan_id,ordinal,state,source_remote_id,parent_remote_id,precheck_json,created_at) VALUES(?,?,'prepared',?,?,?,?)",
            (planId, ordinal, item["remote_id"], parent["remote_id"], json.dumps([row["id"] for row in (parent, source, target)]), time.time()))
        recordDirectoryLog(settings, planId, "item_prechecked", connection=connection, ordinal=ordinal)


def requireDirectoryRenameProof(connection, planId, ordinal):
    item = _planItem(connection, planId, ordinal)
    outcome = connection.execute("SELECT * FROM cloud_directory_item_outcomes WHERE plan_id=? AND ordinal=? AND state='prepared'", (planId, ordinal)).fetchone()
    if outcome is None:
        raise CloudDirectoryError("identity_precheck_required")
    checks = _lastChecks(connection, planId, ordinal)
    if ([row["id"] for row in checks] != json.loads(outcome["precheck_json"])
            or outcome["source_remote_id"] != item["remote_id"]):
        raise CloudDirectoryError("identity_precheck_changed")
    return item


def verifyDirectoryItem(settings, planId, token, ordinal):
    from app.service.cloud_directory_admission_service import _worker
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, planId, token)
        item = _planItem(connection, planId, ordinal)
        outcome = connection.execute("SELECT * FROM cloud_directory_item_outcomes WHERE plan_id=? AND ordinal=?", (planId, ordinal)).fetchone()
        if outcome is None or outcome["state"] != "responded" or outcome["remote_reply"] not in {0, 1}:
            raise CloudDirectoryError("rename_response_required")
        target, source, parent = _lastChecks(connection, planId, ordinal)
        rename = connection.execute("SELECT * FROM cloud_directory_rpc_intents WHERE id=?", (outcome["rename_rpc_id"],)).fetchone()
        common = (rename is not None and target["ordinal"] > rename["ordinal"]
            and target["request_path"] == "/" + item["target_name"] and source["request_path"] == "/" + item["source_name"]
            and parent["request_path"] == "/" and parent["response_code"] == "ok" and parent["remote_id"] == outcome["parent_remote_id"])
        applied = common and outcome["remote_reply"] == 1 and target["response_code"] == "ok" and target["remote_id"] == item["remote_id"] and source["response_code"] == "not_found" and source["remote_id"] is None
        notApplied = common and outcome["remote_reply"] == 0 and target["response_code"] == "not_found" and target["remote_id"] is None and source["response_code"] == "ok" and source["remote_id"] == item["remote_id"]
        state = "verified" if applied else "not_applied" if notApplied else "review"
        connection.execute("UPDATE cloud_directory_item_outcomes SET state=?,postcheck_json=?,verified_at=?,reason=? WHERE plan_id=? AND ordinal=?",
            (state, json.dumps([row["id"] for row in (target, source, parent)]), time.time(), None if applied else "remote_rejected" if notApplied else "post_identity_changed", planId, ordinal))
        if state != "verified":
            connection.execute("UPDATE cloud_directory_executions SET status='review',reason=?,revision=revision+1,updated_at=? WHERE plan_id=?",
                               ("remote_rejected" if notApplied else "post_identity_changed", time.time(), planId))
        recordDirectoryLog(settings, planId, "item_verified" if applied else "item_review", connection=connection, ordinal=ordinal)
        return state
