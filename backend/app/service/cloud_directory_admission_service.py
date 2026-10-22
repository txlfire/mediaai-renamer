"""目录动作的独立预算和持久化RPC意图；不创建连接、不自动调度或重放。"""

import os
import time
import unicodedata
from uuid import uuid4

from app.service.audit_service import record_audit_event
from app.service.cloud_directory_plan_service import (
    CloudDirectoryError, _owned, _items, _valid, _permission, _revision, directoryPolicyHash,
)
from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_drive_budget_service import _pool, _wallNow, cloudDriveHostKey
from app.service.cloud_inventory_service import inventoryDb, inventoryId
from app.service.file_task_queue_service import assertFileTaskSlotFree

METHODS = {"GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath", "RenameFile"}
RESPONSE_CODES = {"ok", "not_found", "rejected", "unauthenticated", "permission_denied"}
DURATION_SECONDS = 180
MAX_BYTES = 1024 * 1024


class DirectorySpacingWait(CloudDirectoryError):
    def __init__(self, retryAfterSeconds):
        super().__init__("spacing_wait_required")
        self.retryAfterSeconds = retryAfterSeconds


def _audit(settings, connection, row, action, *, context=None, detail=None, result="pending"):
    record_audit_event(settings, event_type="cloud_directory.execution", action=action, result=result,
        summary="目录动作批准或执行归属变更；不自动重放", target_type="cloud_directory_plan", target_id=row["plan_id"],
        actor=context.actor if context else None, actor_id=row["actor_id"],
        actor_name=None if context else "cloud_directory_worker", detail=detail,
        ip_address=context.ipAddress if context else None, user_agent=context.userAgent if context else None, connection=connection)
    from app.service.cloud_directory_log_service import recordDirectoryLog
    recordDirectoryLog(settings, row["plan_id"], action, connection=connection)


def _row(connection, planId):
    row = connection.execute("SELECT * FROM cloud_directory_executions WHERE plan_id=?", (planId,)).fetchone()
    if row is None:
        raise CloudDirectoryError("execution_not_found")
    return row


def _worker(connection, planId, token):
    row = _row(connection, planId)
    if (row["worker_token"] != token or row["worker_pid"] != os.getpid()
            or row["owner_host"] != cloudDriveHostKey() or row["slot_held"] != 1):
        raise CloudDirectoryError("worker_ownership_lost")
    return row


def _active(settings, connection, row, now):
    _permission(connection, row["actor_id"])
    _permission(connection, row["actor_id"], "cloud-directory:execute")
    if not settings.cloudDrive.directoryRenameEnabled or row["policy_hash"] != directoryPolicyHash(settings):
        raise CloudDirectoryError("execution_policy_changed")
    if row["status"] != "running" or now < row["created_at"] or now >= row["expires_at"]:
        raise CloudDirectoryError("execution_not_active")
    if row["cancel_requested"]:
        raise CloudDirectoryError("cancel_requested")
    if connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE plan_id=? LIMIT 1", (row["plan_id"],)).fetchone():
        raise CloudDirectoryError("execution_handed_to_recovery")
    plan = connection.execute("SELECT * FROM cloud_directory_plans WHERE id=?", (row["plan_id"],)).fetchone()
    if plan["kind"] == "reverse":
        from app.service.cloud_directory_plan_service import _items
        from app.service.cloud_directory_reverse_service import reversePlanInvalidReason
        reason = reversePlanInvalidReason(connection, settings, plan, _items(connection, row["plan_id"]))
        if reason is not None:
            raise CloudDirectoryError(reason)
        return
    job = connection.execute("SELECT revision,cache_state FROM cloud_drive_inventory_jobs WHERE id=?",
                             (plan["inventory_job_id"],)).fetchone()
    if job is None or job["revision"] != plan["inventory_revision"] or job["cache_state"] != "available":
        raise CloudDirectoryError("observation_changed")


def approveDirectoryExecution(settings, planId, *, requestId, expectedRevision, confirmMaintenance, auditContext, confirmGlobalFence=False):
    """固定预算并占槽；显式入口单次调用，不自动领取历史意图。"""
    inventoryId(requestId)
    _revision(expectedRevision)
    if confirmMaintenance is not True or confirmGlobalFence is not True or auditContext.actor is None:
        raise ValueError("须确认维护窗口及其他客户端不修改的受控边界")
    if not settings.cloudDrive.directoryRenameEnabled:
        raise CloudDirectoryError("directory_execution_disabled")
    with inventoryDb(settings, write=True) as connection:
        plan = _owned(connection, planId, auditContext.actor.id)
        _permission(connection, auditContext.actor.id, "cloud-directory:execute")
        old = connection.execute("SELECT * FROM cloud_directory_executions WHERE request_id=? OR plan_id=?", (requestId, planId)).fetchone()
        if old is not None:
            # 批准响应不明时只能查询；不能通过重复POST取得新的工作进程令牌。
            raise CloudDirectoryError("execution_already_accepted")
        if plan["revision"] != expectedRevision:
            raise CloudDirectoryError("revision_conflict")
        items = _items(connection, planId)
        reason = _valid(connection, settings, plan, items)
        if reason is not None:
            raise CloudDirectoryError(reason)
        requireNoDirectoryExecution(connection)
        assertFileTaskSlotFree(connection, "rename_operation", -1)
        if connection.execute("SELECT 1 FROM scan_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1").fetchone():
            raise CloudDirectoryError("scan_slot_busy")
        if connection.execute("SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL LIMIT 1").fetchone():
            raise CloudDirectoryError("inventory_slot_busy")
        if connection.execute("SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1").fetchone():
            raise CloudDirectoryError("read_rpc_unresolved")
        if connection.execute("SELECT 1 FROM cloud_directory_probe_leases LIMIT 1").fetchone():
            raise CloudDirectoryError("file_probe_unresolved")
        if (connection.execute("SELECT 1 FROM local_file_actions WHERE status IN ('prepared','applied','recovery_required') LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM remote_operation_items WHERE status NOT IN ('completed','failed') LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM remote_operation_locks WHERE status='active' LIMIT 1").fetchone()):
            raise CloudDirectoryError("file_action_unresolved")
        if connection.execute("SELECT 1 FROM background_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1").fetchone():
            raise CloudDirectoryError("background_work_busy")
        now = _wallNow()
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready" or now < pool["cooldown_until"]:
            raise CloudDirectoryError("pool_not_ready")
        expiresAt = min(now + DURATION_SECONDS, plan["expires_at"])
        initialWait = max(0, pool["last_started_at"] + pool["min_interval"] - now) if pool["last_started_at"] is not None else 0
        requiredSeconds = initialWait + (3 + 7 * len(items) - 1) * pool["min_interval"] + 15
        if requiredSeconds > expiresAt - now:
            raise CloudDirectoryError("batch_exceeds_time_budget")
        token = str(uuid4())
        logId = connection.execute("SELECT COALESCE(MAX(log_id),0)+1 FROM cloud_directory_executions").fetchone()[0]
        connection.execute("INSERT INTO cloud_directory_executions "
            "(plan_id,request_id,actor_id,policy_hash,status,worker_token,owner_pid,owner_host,max_rpc,max_bytes,created_at,expires_at,updated_at,log_id) "
            "VALUES(?,?,?,?,'accepted',?,?,?,?,?,?,?,?,?)", (planId, requestId, auditContext.actor.id, plan["policy_hash"], token,
                os.getpid(), cloudDriveHostKey(), 3 + 7 * len(items), MAX_BYTES, now, expiresAt, now, logId))
        _audit(settings, connection, _row(connection, planId), "approve", context=auditContext,
               detail={"maintenanceConfirmed": True, "globalOldPlanFenceConfirmed": True, "automaticRescan": False,
                       "atomicIdentityGuaranteed": False, "itemCount": len(items)})
        return token


def bindDirectoryWorker(settings, planId, token, pid):
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        raise ValueError("工作进程PID无效")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, planId)
        if (row["worker_token"] != token or row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey()
                or row["status"] != "accepted" or row["worker_pid"] is not None or row["slot_held"] != 1):
            raise CloudDirectoryError("worker_ownership_lost")
        candidate = dict(row)
        candidate["status"] = "running"
        _active(settings, connection, candidate, _wallNow())
        connection.execute("UPDATE cloud_directory_executions SET worker_pid=?,status='running',revision=revision+1,updated_at=? WHERE plan_id=?",
                           (pid, time.time(), planId))


def reserveDirectoryRpc(settings, planId, token, method, *, itemOrdinal=None, requestPath=None):
    """发送前持久化并计费；本函数返回不代表允许绕过工作进程的身份/冲突校验。"""
    if method not in METHODS or (method == "RenameFile" and itemOrdinal is None):
        raise CloudDirectoryError("rpc_not_allowed")
    if itemOrdinal is not None and (type(itemOrdinal) is not int or not 1 <= itemOrdinal <= 5):
        raise ValueError("动作序号无效")
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, planId, token)
        now = _wallNow()
        _active(settings, connection, row, now)
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready" or now < pool["cooldown_until"]:
            raise CloudDirectoryError("pool_not_ready")
        if (connection.execute("SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL LIMIT 1").fetchone()):
            raise CloudDirectoryError("rpc_unresolved")
        if row["used_rpc"] >= row["max_rpc"] or row["received_bytes"] >= row["max_bytes"]:
            raise CloudDirectoryError("execution_budget_exhausted")
        if itemOrdinal is not None and not connection.execute("SELECT 1 FROM cloud_directory_plan_items WHERE plan_id=? AND ordinal=?", (planId, itemOrdinal)).fetchone():
            raise CloudDirectoryError("item_not_found")
        if method in {"FindFileByPath", "RenameFile"}:
            from app.service.cloud_directory_item_service import requireDirectoryRenameProof
            items = connection.execute("SELECT ordinal,source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=?", (planId,)).fetchall()
            paths = {"/"}
            for item in items:
                if itemOrdinal is None or item["ordinal"] == itemOrdinal:
                    paths.update(("/" + item["source_name"], "/" + item["target_name"]))
            if requestPath not in paths:
                raise CloudDirectoryError("path_not_in_plan")
            if method == "RenameFile":
                proof = requireDirectoryRenameProof(connection, planId, itemOrdinal)
                if requestPath != "/" + proof["source_name"]:
                    raise CloudDirectoryError("path_not_in_plan")
        elif requestPath is not None:
            raise CloudDirectoryError("rpc_not_allowed")
        if pool["last_started_at"] is not None and now < pool["last_started_at"] + pool["min_interval"]:
            raise DirectorySpacingWait(pool["last_started_at"] + pool["min_interval"] - now)
        rpcId = str(uuid4())
        connection.execute("INSERT INTO cloud_directory_rpc_intents(id,plan_id,ordinal,method,item_ordinal,phase,sent_at,request_path) "
            "VALUES(?,?,?,?,?,'sending',?,?)", (rpcId, planId, row["used_rpc"] + 1, method, itemOrdinal, now, requestPath))
        if method == "RenameFile":
            connection.execute("UPDATE cloud_directory_item_outcomes SET state='sending',rename_rpc_id=?,sent_at=? WHERE plan_id=? AND ordinal=? AND state='prepared'",
                               (rpcId, now, planId, itemOrdinal))
        connection.execute("UPDATE cloud_directory_executions SET used_rpc=used_rpc+1,revision=revision+1,updated_at=? WHERE plan_id=?", (now, planId))
        connection.execute("UPDATE cloud_drive_budget_pool SET last_started_at=? WHERE id=1", (now,))
        return rpcId


def recordDirectoryRpcResponse(settings, planId, token, rpcId, *, responseCode, receivedBytes, identity=None):
    if responseCode not in RESPONSE_CODES or type(receivedBytes) is not int or not 0 <= receivedBytes <= 2**31 - 1:
        raise ValueError("响应记录须使用固定状态码及有效字节数")
    with inventoryDb(settings, write=True) as connection:
        # 撤权、取消或过期后仍保存已发生的响应事实，但不会据此继续发送或释放槽。
        row = _worker(connection, planId, token)
        rpc = connection.execute("SELECT * FROM cloud_directory_rpc_intents WHERE id=? AND plan_id=? AND phase='sending'", (rpcId, planId)).fetchone()
        if rpc is None:
            raise CloudDirectoryError("rpc_state_changed")
        remoteId = None
        if rpc["method"] == "FindFileByPath" and responseCode == "ok":
            if (not isinstance(identity, dict) or set(identity) != {"objectId", "virtualPath", "name"}
                    or not isinstance(identity["objectId"], str) or not identity["objectId"]
                    or any(unicodedata.category(char).startswith("C") for char in identity["objectId"])
                    or len(identity["objectId"].encode()) > 1024 or identity["virtualPath"] != rpc["request_path"]):
                raise ValueError("精确身份查询须保存已校验的对象证据")
            remoteId = identity["objectId"]
        elif identity is not None:
            raise ValueError("该响应不能携带对象身份")
        now = _wallNow()
        exceeded = row["received_bytes"] + receivedBytes > row["max_bytes"]
        connection.execute("UPDATE cloud_directory_rpc_intents SET phase='responded',response_code=?,received_bytes=?,responded_at=?,remote_id=? WHERE id=?",
                           (responseCode, receivedBytes, now, remoteId, rpcId))
        if rpc["method"] == "RenameFile":
            changed = connection.execute("UPDATE cloud_directory_item_outcomes SET state='responded',remote_reply=? WHERE plan_id=? AND ordinal=? AND state='sending' AND rename_rpc_id=?",
                (1 if responseCode == "ok" else 0, planId, rpc["item_ordinal"], rpcId)).rowcount
            if changed != 1:
                raise CloudDirectoryError("item_state_changed")
        connection.execute("UPDATE cloud_directory_executions SET received_bytes=received_bytes+?,"
            "status=CASE WHEN ? THEN 'review' ELSE status END,revision=revision+1,updated_at=? WHERE plan_id=?", (receivedBytes, exceeded, now, planId))


def markDirectoryRpcUnknown(settings, planId, token, rpcId):
    with inventoryDb(settings, write=True) as connection:
        row = _worker(connection, planId, token)
        now = _wallNow()
        changed = connection.execute("UPDATE cloud_directory_rpc_intents SET phase='unknown' WHERE id=? AND plan_id=? AND phase='sending'", (rpcId, planId)).rowcount
        if changed != 1:
            raise CloudDirectoryError("rpc_state_changed")
        connection.execute("UPDATE cloud_directory_executions SET status='review',revision=revision+1,updated_at=? WHERE plan_id=?", (now, planId))
        connection.execute("UPDATE cloud_directory_item_outcomes SET state='review',reason='rpc_unknown' WHERE plan_id=? AND ordinal IN "
            "(SELECT item_ordinal FROM cloud_directory_rpc_intents WHERE id=?)", (planId, rpcId))
        pool = _pool(settings, connection, now)
        connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
            "pause_reason=CASE WHEN pause_reason='manual_pause' THEN pause_reason ELSE 'request_incomplete' END,"
            "cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1", (now + pool["failure_cooldown"], now))
        _audit(settings, connection, row, "rpc_unknown", detail={"rpcId": rpcId, "replayed": False})


def cancelUnstartedDirectoryExecution(settings, planId, token, *, auditContext):
    """仅取消没有工作进程、没有任何发送意图的受理；不是未知动作释放入口。"""
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, planId)
        _owned(connection, planId, auditContext.actor.id)
        if (row["worker_token"] != token or row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey()
                or row["worker_pid"] is not None or row["used_rpc"] != 0 or row["status"] != "accepted"):
            raise CloudDirectoryError("execution_requires_review")
        if connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE plan_id=? LIMIT 1", (planId,)).fetchone():
            raise CloudDirectoryError("execution_handed_to_recovery")
        connection.execute("UPDATE cloud_directory_executions SET status='cancelled',slot_held=0,revision=revision+1,updated_at=? WHERE plan_id=?", (time.time(), planId))
        _audit(settings, connection, row, "cancel_before_worker", context=auditContext, detail={"remoteRequests": 0})
