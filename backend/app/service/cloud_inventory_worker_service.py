"""显式运行单个盘点量子；不注册启动循环，不自动重放中断任务。"""

import math
import os
import time

from app.service.cloud_drive_configuration_service import cloudConfigurationReference

from app.service.cloud_drive_budget_service import cloudDriveHostKey, requireCloudActor, CloudBudgetError
from app.service.cloud_inventory_service import (
    CloudInventoryError, inventoryDb, inventoryId, claimCloudInventoryJob, persistCloudInventoryPid,
    _row, _change, _audit,
)
from app.service.isolated_task_service import startTaskProcess, TaskProcessStartError
from app.service.scan_recovery_service import isProcessAlive
from app.utils.cloud_drive_paths import canonicalCd2Target


def _finishOwnedProcess(settings, jobId, token, expectedPid):
    try:
        if expectedPid is not None and isProcessAlive(expectedPid):
            raise CloudInventoryError("worker_exit_unconfirmed")
    except (ValueError, TypeError, OSError):
        raise CloudInventoryError("worker_exit_unconfirmed") from None
    with inventoryDb(settings, write=True) as connection:
        row = _row(connection, jobId)
        if (row["worker_token"] != token or row["owner_pid"] != os.getpid()
                or row["owner_host"] != cloudDriveHostKey() or row["worker_pid"] not in {None, expectedPid}):
            raise CloudInventoryError("worker_ownership_lost")
        if row["status"] == "running" or (row["cancel_requested"] and row["status"] not in {"completed", "cancelled"}):
            connection.execute("UPDATE cloud_drive_inventory_directories SET status='partial' WHERE job_id=? AND status='reading'", (jobId,))
            _change(settings, connection, row, "cancelled" if row["cancel_requested"] else "interrupted",
                    "cancel_requested" if row["cancel_requested"] else "process_interrupted")
        connection.execute("UPDATE cloud_drive_inventory_jobs SET worker_token=NULL,worker_pid=NULL,owner_pid=NULL,"
            "owner_instance=NULL,owner_host=NULL,revision=revision+1,updated_at=? WHERE id=? AND worker_token=?", (time.time(), jobId, token))
        _audit(settings, connection, _row(connection, jobId), "inventory_worker_reaped")
        # 这里只释放已经回收的本机worker槽，绝不删除CD2未决RPC租约。


def runCloudInventoryJob(settings, jobId, *, endpoint, apiToken, trustedCaPem=None, stop=None):
    """内部监督入口，秘密仅通过匿名stdin交接；调用者须已有真实范围授权。"""
    inventoryId(jobId)
    canonicalCd2Target(endpoint)
    if (len(endpoint.encode()) > 4096 or not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192
            or any(not 33 <= ord(char) <= 126 for char in apiToken)):
        raise ValueError("盘点连接交接参数无效")
    if trustedCaPem is not None and (not isinstance(trustedCaPem, bytes) or len(trustedCaPem) > 16384):
        raise ValueError("盘点CA须为不超过16KiB的PEM")
    _requireConfiguredConnection(settings, endpoint, trustedCaPem)
    token = claimCloudInventoryJob(settings, jobId)
    return superviseClaimedCloudInventoryJob(settings, jobId, token, endpoint=endpoint,
        apiToken=apiToken, trustedCaPem=trustedCaPem, stop=stop)


def _requireConfiguredConnection(settings, endpoint, trustedCaPem):
    cloudConfigurationReference(settings)
    config = settings.cloudDrive
    if (canonicalCd2Target(endpoint) != canonicalCd2Target(config.endpoint)
            or trustedCaPem is not None and trustedCaPem.replace(b"\r\n", b"\n") != config.caCertificatePem.encode("ascii")):
        raise CloudInventoryError("web_scope_mismatch")


def superviseClaimedCloudInventoryJob(settings, jobId, token, *, endpoint, apiToken, trustedCaPem=None, stop=None):
    """监督已经原子领取的任务；仅供内部受控入口使用，不重新领取或排队。"""
    process = None
    try:
        _requireConfiguredConnection(settings, endpoint, trustedCaPem)
        if stop is not None and stop.is_set():
            return
        with inventoryDb(settings) as connection:
            expiresAt = _row(connection, jobId)["expires_at"]
        remaining = expiresAt - time.time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise CloudInventoryError("deadline_exceeded")
        deadline = time.monotonic() + min(remaining, 900)
        payload = {"job_id": jobId, "worker_token": token, "api_token": apiToken,
            "configuration_revision": cloudConfigurationReference(settings)}
        try:
            process = startTaskProcess(settings, "app.cloud_inventory_worker", payload,
                lambda pid: persistCloudInventoryPid(settings, jobId, token, pid))
        except TaskProcessStartError as exc:
            process = exc.process
            if exc.__cause__ is not None and not isinstance(exc.__cause__, Exception):
                raise exc.__cause__ from None
            raise CloudInventoryError("worker_start_failed") from None
        finally:
            payload.pop("api_token", None)
        while process.poll() is None:
            with inventoryDb(settings) as connection:
                row = _row(connection, jobId)
                try:
                    requireCloudActor(connection, row["actor_id"])
                    permitted = True
                except CloudBudgetError:
                    permitted = False
                now = time.time()
                expired = (not math.isfinite(now) or now < row["created_at"] or now >= row["expires_at"]
                           or time.monotonic() >= deadline)
                cancelled = row["cancel_requested"] or (stop is not None and stop.is_set())
                owned = row["worker_token"] == token and row["worker_pid"] == process.pid
            if expired or cancelled or not owned or not permitted:
                break
            time.sleep(0.2)
    except OSError:
        raise CloudInventoryError("worker_start_failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        _finishOwnedProcess(settings, jobId, token, process.pid if process is not None else None)


def inspectCloudInventoryWorker(settings, jobId):
    inventoryId(jobId)
    with inventoryDb(settings) as connection:
        row = _row(connection, jobId)
    return {"id": jobId, "status": row["status"], "workerSlotHeld": row["worker_token"] is not None,
            "workerPid": row["worker_pid"], "ownerPid": row["owner_pid"], "ownerHost": row["owner_host"],
            "replayed": False}


def releaseCloudInventoryWorkerAfterReview(settings, jobId, *, confirmClientsStopped, confirmRemoteIdle):
    inventoryId(jobId)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True:
        raise ValueError("释放盘点worker槽须确认所有相关客户端停止且远端静止")
    with inventoryDb(settings) as connection:
        observed = dict(_row(connection, jobId))
    if observed["worker_token"] is None:
        raise CloudInventoryError("worker_slot_not_held")
    try:
        if (observed["owner_host"] != cloudDriveHostKey() or type(observed["owner_pid"]) is not int
                or not 1 <= observed["owner_pid"] <= 2**31 - 1 or isProcessAlive(observed["owner_pid"])):
            raise CloudInventoryError("worker_exit_unconfirmed")
        if observed["worker_pid"] is not None and (type(observed["worker_pid"]) is not int
                or not 1 <= observed["worker_pid"] <= 2**31 - 1 or isProcessAlive(observed["worker_pid"])):
            raise CloudInventoryError("worker_exit_unconfirmed")
    except (ValueError, TypeError, OSError):
        raise CloudInventoryError("worker_exit_unconfirmed") from None
    with inventoryDb(settings, write=True) as connection:
        current = dict(_row(connection, jobId))
        if any(current[key] != observed[key] for key in ("worker_token", "worker_pid", "owner_pid", "owner_instance", "owner_host")):
            raise CloudInventoryError("worker_ownership_lost")
        if current["worker_pid"] is None:
            # PID提交先于stdin授权；本次未绑定子进程没有获得Token，仍拒绝任何矛盾的在途证据。
            if (connection.execute("SELECT 1 FROM cloud_drive_rpc_lease WHERE session_id=? LIMIT 1", (current["session_id"],)).fetchone()
                    or connection.execute("SELECT 1 FROM cloud_drive_inventory_directories WHERE job_id=? AND status='reading' LIMIT 1", (jobId,)).fetchone()):
                raise CloudInventoryError("worker_exit_unconfirmed")
        if current["status"] == "running":
            connection.execute("UPDATE cloud_drive_inventory_directories SET status='partial' WHERE job_id=? AND status='reading'", (jobId,))
            _change(settings, connection, current, "interrupted", "process_interrupted")
        connection.execute("UPDATE cloud_drive_inventory_jobs SET worker_token=NULL,worker_pid=NULL,owner_pid=NULL,"
            "owner_instance=NULL,owner_host=NULL,revision=revision+1,updated_at=? WHERE id=?", (time.time(), jobId))
        _audit(settings, connection, _row(connection, jobId), "inventory_worker_release_reviewed")
