"""单次目录动作的独立进程监督；无启动领取循环，进程退出不证明远端未执行。"""

import os
import time

from app.service.cloud_drive_configuration_service import cloudConfigurationReference

from app.core.logger import get_logger
from app.service.cloud_directory_admission_service import _row, _worker, _active, _audit, bindDirectoryWorker
from app.service.cloud_directory_plan_service import CloudDirectoryError, _permission
from app.service.cloud_drive_budget_service import cloudDriveHostKey, _pool
from app.service.cloud_inventory_service import inventoryDb
from app.service.cd2_directory_client import Cd2DirectoryClient
from app.service.cloud_directory_item_service import prepareDirectoryItem, verifyDirectoryItem
from app.service.isolated_task_service import startTaskProcess, TaskProcessStartError
from app.service.scan_recovery_service import isProcessAlive

_REASONS = {"worker_failed", "worker_start_failed", "worker_interrupted", "cancel_requested", "deadline_exceeded",
    "configuration_changed", "configuration_unavailable", "configuration_unbound",
    "recovery_not_active", "recovery_evidence_incomplete", "position_ambiguous", "positions_reconciled",
    "execution_handed_to_recovery", "reverse_basis_changed", "reverse_basis_outdated_or_no_changes",
    "revision_or_policy_changed", "recovery_not_ready_or_stale",
    "rpc_unknown", "awaiting_path_sync", "remote_rejected", "post_identity_changed", "permission_denied",
    "execution_not_active", "execution_policy_changed", "observation_changed", "identity_or_conflict_changed",
    "parent_identity_changed", "directory_identity_unsupported", "token_scope_or_permissions_invalid",
    "unknown_token_permissions", "runtime_not_verified", "unavailable", "unauthenticated", "resource_exhausted",
    "invalid_response", "execution_budget_exhausted", "pool_not_ready", "storage_unavailable", "trust_unavailable"}


def safeDirectoryReason(value):
    return value if isinstance(value, str) and value in _REASONS else "worker_failed"


def stopDirectoryWorker(settings, planId, token, reason):
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, planId, token)
        connection.execute("UPDATE cloud_directory_executions SET status='review',reason=?,revision=revision+1,updated_at=? WHERE plan_id=?",
                           (safeDirectoryReason(reason), time.time(), planId))


def executeDirectoryBatch(settings, planId, token, apiToken):
    with inventoryDb(settings) as connection:
        row = _worker(connection, planId, token)
        _active(settings, connection, row, time.time())
        remaining = row["expires_at"] - time.time()
        count = connection.execute("SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?", (planId,)).fetchone()[0]
    deadline = time.monotonic() + min(180, remaining)
    with Cd2DirectoryClient(settings, planId, token, apiToken) as client:
        client.verifyConnection(deadline)
        client.findDirectory("/", deadline)
        for ordinal in range(1, count + 1):
            with inventoryDb(settings) as connection:
                row = _worker(connection, planId, token)
                _active(settings, connection, row, time.time())
                item = connection.execute("SELECT source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=? AND ordinal=?", (planId, ordinal)).fetchone()
            get_logger(__name__).info("C2目录项前检 plan_id=%s ordinal=%s total=%s", planId, ordinal, count)
            client.findDirectory("/", deadline, itemOrdinal=ordinal)
            client.findDirectory("/" + item["source_name"], deadline, itemOrdinal=ordinal)
            client.findDirectory("/" + item["target_name"], deadline, itemOrdinal=ordinal, missingAllowed=True)
            prepareDirectoryItem(settings, planId, token, ordinal)
            client.renameItem(ordinal, deadline)
            client.findDirectory("/" + item["target_name"], deadline, itemOrdinal=ordinal, missingAllowed=True)
            client.findDirectory("/" + item["source_name"], deadline, itemOrdinal=ordinal, missingAllowed=True)
            client.findDirectory("/", deadline, itemOrdinal=ordinal)
            state = verifyDirectoryItem(settings, planId, token, ordinal)
            get_logger(__name__).info("C2目录项后检 plan_id=%s ordinal=%s state=%s", planId, ordinal, state)
            if state != "verified":
                return
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, planId, token)
        verified = connection.execute("SELECT COUNT(*) FROM cloud_directory_item_outcomes WHERE plan_id=? AND state='verified'", (planId,)).fetchone()[0]
        if verified != count:
            raise CloudDirectoryError("post_identity_changed")
        # 路径一致性尚未提交时仍保留全实例槽，不能把远端已改名直接标为业务完成。
        connection.execute("UPDATE cloud_directory_executions SET status='review',reason='awaiting_path_sync',revision=revision+1,updated_at=? WHERE plan_id=?", (time.time(), planId))


def finishDirectoryWorker(settings, planId, token, expectedPid, *, stopReason="worker_interrupted"):
    if expectedPid is not None and isProcessAlive(expectedPid):
        raise CloudDirectoryError("worker_exit_unconfirmed")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, planId)
        if (row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey()
                or row["worker_token"] != token or row["worker_pid"] not in {None, expectedPid} or row["slot_held"] != 1
                or connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE plan_id=? LIMIT 1", (planId,)).fetchone()):
            raise CloudDirectoryError("worker_ownership_lost")
        unresolved = connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE plan_id=? AND phase IN ('sending','unknown') LIMIT 1", (planId,)).fetchone()
        changed = connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE plan_id=? AND method='RenameFile' LIMIT 1", (planId,)).fetchone()
        if row["status"] == "review" and row["reason"] == "awaiting_path_sync":
            status, reason, held = "review", "awaiting_path_sync", 1
        elif not unresolved and not changed:
            status, reason, held = "cancelled", row["reason"] or ("cancel_requested" if row["cancel_requested"] else safeDirectoryReason(stopReason)), 0
        else:
            status, reason, held = "review", row["reason"] or "worker_interrupted", 1
        connection.execute("UPDATE cloud_directory_rpc_intents SET phase='unknown' WHERE plan_id=? AND phase='sending'", (planId,))
        if unresolved:
            now = time.time()
            pool = _pool(settings, connection, now)
            connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
                "pause_reason=CASE WHEN pause_reason='manual_pause' THEN pause_reason ELSE 'request_incomplete' END,"
                "cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1", (now + pool["failure_cooldown"], now))
        connection.execute("UPDATE cloud_directory_executions SET status=?,reason=?,slot_held=?,worker_pid=NULL,revision=revision+1,updated_at=? WHERE plan_id=?",
                           (status, reason, held, time.time(), planId))
        _audit(settings, connection, row, "worker_reaped", detail={"workerExited": True, "status": status,
            "workerPid": expectedPid, "ownerPid": os.getpid(), "reason": reason, "slotHeld": bool(held), "replayed": False},
            result="pending" if reason == "awaiting_path_sync" else "failed" if status == "review" else "success")


def superviseDirectoryWorker(settings, planId, token, apiToken, *, stop=None):
    process = None
    payload = {}
    stopReason = "worker_interrupted"
    try:
        with inventoryDb(settings) as connection:
            row = _row(connection, planId)
            remaining = row["expires_at"] - time.time()
        if remaining <= 0 or stop is not None and stop.is_set():
            return
        deadline = time.monotonic() + min(180, remaining)
        payload = {"plan_id": planId, "worker_token": token, "api_token": apiToken,
                   "configuration_revision": cloudConfigurationReference(settings)}
        try:
            process = startTaskProcess(settings, "app.cloud_directory_worker", payload,
                lambda pid: bindDirectoryWorker(settings, planId, token, pid))
        except TaskProcessStartError as exc:
            process = exc.process
            raise CloudDirectoryError("worker_start_failed") from None
        finally:
            payload.pop("api_token", None)
            apiToken = ""
        while process.poll() is None:
            with inventoryDb(settings) as connection:
                row = _row(connection, planId)
                try:
                    _permission(connection, row["actor_id"])
                    _permission(connection, row["actor_id"], "cloud-directory:execute")
                    permitted = True
                except CloudDirectoryError:
                    permitted = False
            if (not permitted or row["cancel_requested"] or stop is not None and stop.is_set()
                    or time.monotonic() >= deadline or time.time() >= row["expires_at"]):
                stopReason = "permission_denied" if not permitted else "cancel_requested" if row["cancel_requested"] else "deadline_exceeded" if time.monotonic() >= deadline else "worker_interrupted"
                break
            time.sleep(0.1)
    except Exception as exc:
        stopReason = safeDirectoryReason(getattr(exc, "code", None))
        raise
    finally:
        payload.pop("api_token", None)
        apiToken = ""
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        finishDirectoryWorker(settings, planId, token, process.pid if process else None, stopReason=stopReason)
