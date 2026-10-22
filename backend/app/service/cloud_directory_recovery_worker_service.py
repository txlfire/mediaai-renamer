"""单次只读位置核对进程，不自动开始新预算，不修改原意图。"""

import os
import time

from app.service.cloud_drive_configuration_service import cloudConfigurationReference

from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_directory_recovery_service import _row, _worker, _active, _audit, bindRecoveryWorker, verifyRecoveryPositions, recoveryView
from app.service.cloud_directory_plan_service import _owned, _permission, _revision
from app.service.cloud_inventory_service import inventoryId
from app.service.cloud_directory_worker_service import safeDirectoryReason
from app.service.cloud_inventory_service import inventoryDb
from app.service.cloud_drive_budget_service import _pool, cloudDriveHostKey
from app.service.cd2_directory_recovery_client import Cd2DirectoryRecoveryClient
from app.service.isolated_task_service import startTaskProcess, TaskProcessStartError
from app.service.scan_recovery_service import isProcessAlive


def stopDirectoryRecovery(settings, recoveryId, token, reason):
    with inventoryDb(settings, write=True) as connection:
        _worker(connection, recoveryId, token)
        connection.execute("UPDATE cloud_directory_recoveries SET status='review',reason=?,revision=revision+1,updated_at=? WHERE id=?",
            (safeDirectoryReason(reason), time.time(), recoveryId))


def executeDirectoryRecovery(settings, recoveryId, token, apiToken):
    with inventoryDb(settings) as connection:
        row = _worker(connection, recoveryId, token)
        _active(settings, connection, row, time.time())
        deadline = time.monotonic() + min(90, row["expires_at"] - time.time())
        items = connection.execute("SELECT ordinal,source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=? ORDER BY ordinal", (row["plan_id"],)).fetchall()
    with Cd2DirectoryRecoveryClient(settings, recoveryId, token, apiToken) as client:
        client.verifyConnection(deadline)
        client.findDirectory("/", deadline)
        for item in items:
            client.findDirectory("/" + item["source_name"], deadline, itemOrdinal=item["ordinal"], missingAllowed=True)
            client.findDirectory("/" + item["target_name"], deadline, itemOrdinal=item["ordinal"], missingAllowed=True)
        client.findDirectory("/", deadline)
    verifyRecoveryPositions(settings, recoveryId, token)


def finishRecoveryWorker(settings, recoveryId, token, expectedPid, *, reason="worker_interrupted"):
    if expectedPid is not None and isProcessAlive(expectedPid):
        raise CloudDirectoryError("worker_exit_unconfirmed")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, recoveryId)
        if (row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey() or row["worker_token"] != token
                or row["worker_pid"] not in {None, expectedPid} or row["slot_held"] != 1):
            raise CloudDirectoryError("worker_ownership_lost")
        _finishRecoveryState(settings, connection, row, reason)


def _finishRecoveryState(settings, connection, row, reason, *, reviewed=False):
    recoveryId = row["id"]
    connection.execute("UPDATE cloud_directory_recovery_rpcs SET phase='unknown' WHERE recovery_id=? AND phase='sending'", (recoveryId,))
    unresolved = connection.execute("SELECT 1 FROM cloud_directory_recovery_rpcs WHERE recovery_id=? AND phase='unknown' LIMIT 1", (recoveryId,)).fetchone()
    now = time.time()
    if unresolved:
        pool = _pool(settings, connection, now)
        connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
            "pause_reason=CASE WHEN pause_reason='manual_pause' THEN pause_reason ELSE 'request_incomplete' END,"
            "cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1", (now + pool["failure_cooldown"], now))
    ready = row["status"] == "ready" and not unresolved
    stoppedReason = row["reason"] or ("rpc_unknown" if unresolved else safeDirectoryReason(reason))
    connection.execute("UPDATE cloud_directory_recoveries SET worker_pid=NULL,slot_held=0,status=?,reason=?,revision=revision+1,updated_at=? WHERE id=?",
        ("ready" if ready else "review", None if ready else stoppedReason, now, recoveryId))
    # 只归还本轮只读worker槽；原目录动作及所有未知RPC事实不在此释放。
    _audit(settings, connection, row, "recovery_worker_reviewed" if reviewed else "recovery_reaped",
        detail={"workerExited": row["worker_pid"] is not None, "unboundWorker": row["worker_pid"] is None,
                "ownerExitReviewed": reviewed, "originalSlotReleased": False, "networkAccess": False,
                "ready": ready, "reason": None if ready else stoppedReason})


def reviewDirectoryRecoveryWorker(settings, recoveryId, *, expectedRevision, confirmClientsStopped, confirmRemoteIdle, auditContext):
    """显式回收孤儿只读核对槽，不接管旧进程、不重发或释放原目录保护。"""
    inventoryId(recoveryId)
    _revision(expectedRevision)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True or auditContext.actor is None:
        raise ValueError("须确认旧客户端退出及相关远端静止；不自动发送新的核对请求")
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, recoveryId)
        if row["actor_id"] != auditContext.actor.id:
            raise CloudDirectoryError("not_found")
        _owned(connection, row["plan_id"], auditContext.actor.id)
        _permission(connection, auditContext.actor.id, "cloud-directory:execute")
        _permission(connection, auditContext.actor.id, "settings:write")
        if row["revision"] != expectedRevision or not row["slot_held"]:
            raise CloudDirectoryError("revision_conflict")
        try:
            if (row["owner_host"] != cloudDriveHostKey() or type(row["owner_pid"]) is not int
                    or not 1 <= row["owner_pid"] <= 2**31 - 1 or isProcessAlive(row["owner_pid"])):
                raise CloudDirectoryError("worker_exit_unconfirmed")
            if row["worker_pid"] is not None and (type(row["worker_pid"]) is not int
                    or not 1 <= row["worker_pid"] <= 2**31 - 1 or isProcessAlive(row["worker_pid"])):
                raise CloudDirectoryError("worker_exit_unconfirmed")
        except (ValueError, TypeError, OSError):
            raise CloudDirectoryError("worker_exit_unconfirmed") from None
        _finishRecoveryState(settings, connection, row, "worker_interrupted", reviewed=True)
        return recoveryView(connection, _row(connection, recoveryId))


def superviseDirectoryRecovery(settings, recoveryId, token, apiToken, *, stop=None):
    process, payload = None, {}
    reason = "worker_interrupted"
    try:
        with inventoryDb(settings) as connection:
            row = _row(connection, recoveryId)
            remaining = row["expires_at"] - time.time()
        if remaining <= 0 or stop is not None and stop.is_set():
            return
        deadline = time.monotonic() + min(90, remaining)
        payload = {"recovery_id": recoveryId, "worker_token": token, "api_token": apiToken,
            "configuration_revision": cloudConfigurationReference(settings)}
        try:
            process = startTaskProcess(settings, "app.cloud_directory_recovery_worker", payload,
                lambda pid: bindRecoveryWorker(settings, recoveryId, token, pid))
        except TaskProcessStartError as exc:
            process = exc.process
            raise CloudDirectoryError("worker_start_failed") from None
        finally:
            payload.pop("api_token", None)
            apiToken = ""
        while process.poll() is None:
            with inventoryDb(settings) as connection:
                row = _row(connection, recoveryId)
                try:
                    _active(settings, connection, row, time.time())
                    permitted = True
                except CloudDirectoryError:
                    permitted = False
            if not permitted or stop is not None and stop.is_set() or time.monotonic() >= deadline:
                reason = "cancel_requested" if row["cancel_requested"] else "deadline_exceeded" if time.monotonic() >= deadline else "worker_interrupted"
                break
            time.sleep(0.1)
    except Exception as exc:
        reason = safeDirectoryReason(getattr(exc, "code", None))
        raise
    finally:
        payload.pop("api_token", None)
        apiToken = ""
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        finishRecoveryWorker(settings, recoveryId, token, process.pid if process else None, reason=reason)
