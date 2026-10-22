"""一次性隔离worker与监督；退出不证明远端未移动，无后台领取或重放循环。"""

import os
import time

from app.service.cloud_drive_configuration_service import cloudConfigurationReference

from app.core.logger import get_logger
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_quarantine_item_service import prepareQuarantineItem, verifyQuarantineItem
from app.service.cd2_quarantine_client import Cd2QuarantineClient
from app.service.cloud_inventory_service import inventoryDb
from app.service.cloud_drive_budget_service import cloudDriveHostKey
from app.service.isolated_task_service import startTaskProcess, TaskProcessStartError
from app.service.scan_recovery_service import isProcessAlive
from app.service.cloud_directory_worker_service import _REASONS as DIRECTORY_REASONS

QuarantineError = actions.QuarantineError
_REASONS = DIRECTORY_REASONS | {"target_conflict_or_not_applied", "object_identity_unsupported", "identity_precheck_expired", "byte_limit"}


def safeQuarantineReason(value):
    return value if isinstance(value, str) and value in _REASONS else "worker_failed"


def stopQuarantineWorker(settings, runId, token, reason):
    with inventoryDb(settings, write=True) as connection:
        actions._worker(connection, runId, token)
        connection.execute("UPDATE cloud_quarantine_runs SET status='review',reason=?,revision=revision+1,updated_at=? WHERE id=?",
            (safeQuarantineReason(reason), time.time(), runId))


def executeQuarantineBatch(settings, runId, token, apiToken):
    with inventoryDb(settings) as connection:
        row = actions._worker(connection, runId, token)
        actions._active(settings, connection, row, time.time())
        _, snapshot = actions._snapshot(connection, row)
        remaining = row["expires_at"]-time.time()
    if row["kind"] == "observe":
        from app.service.cloud_quarantine_recovery_service import executeQuarantineObservation
        return executeQuarantineObservation(settings, runId, token, apiToken)
    deadline = time.monotonic()+min(180, remaining)
    with Cd2QuarantineClient(settings, runId, token, apiToken) as client:
        client.verifyConnection(deadline)
        client.findObject("/", deadline)
        for ordinal in range(1, len(snapshot["items"])+1):
            _, source, target, folder = actions.itemPaths(snapshot, ordinal)
            sourceParent, targetParent = actions.itemParents(snapshot, ordinal)
            client.findObject(sourceParent, deadline, itemOrdinal=ordinal)
            client.findObject(targetParent, deadline, itemOrdinal=ordinal)
            client.findObject(source, deadline, itemOrdinal=ordinal)
            client.findObject(target, deadline, itemOrdinal=ordinal, missingAllowed=True)
            prepareQuarantineItem(settings, runId, token, ordinal)
            client.moveItem(ordinal, deadline)
            client.findObject(target, deadline, itemOrdinal=ordinal, missingAllowed=True)
            client.findObject(source, deadline, itemOrdinal=ordinal, missingAllowed=True)
            client.findObject(sourceParent, deadline, itemOrdinal=ordinal)
            client.findObject(targetParent, deadline, itemOrdinal=ordinal)
            state = verifyQuarantineItem(settings, runId, token, ordinal)
            get_logger(__name__).info("C3隔离后检 run_id=%s ordinal=%s state=%s", runId, ordinal, state)
            if state != "verified":
                return
    with inventoryDb(settings, write=True) as connection:
        actions._worker(connection, runId, token)
        if connection.execute("SELECT COUNT(*) FROM cloud_quarantine_outcomes WHERE run_id=? AND state='verified'", (runId,)).fetchone()[0] != len(snapshot["items"]):
            raise QuarantineError("post_identity_changed")
        connection.execute("UPDATE cloud_quarantine_runs SET status='review',reason='awaiting_path_sync',revision=revision+1,updated_at=? WHERE id=?", (time.time(), runId))


def finishQuarantineWorker(settings, runId, token, expectedPid, *, stopReason="worker_interrupted"):
    if expectedPid is not None and isProcessAlive(expectedPid):
        raise QuarantineError("worker_exit_unconfirmed")
    with inventoryDb(settings, write=True) as connection:
        row = actions._row(connection, runId)
        if (row["worker_token"] != token or row["owner_pid"] != os.getpid() or row["owner_host"] != cloudDriveHostKey()
                or row["worker_pid"] not in {None, expectedPid} or not row["slot_held"]
                or connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE original_run_id=? LIMIT 1", (runId,)).fetchone()):
            raise QuarantineError("worker_ownership_lost")
        unknown = connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND phase IN ('sending','unknown') LIMIT 1", (runId,)).fetchone()
        sent = connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE run_id=? AND method='MoveFile' LIMIT 1", (runId,)).fetchone()
        connection.execute("UPDATE cloud_quarantine_rpcs SET phase='unknown' WHERE run_id=? AND phase='sending'", (runId,))
        if unknown:
            actions._pauseUnknown(settings, connection, time.time())
        if row["kind"] == "observe":
            status, reason, held = "review", row["reason"] or safeQuarantineReason(stopReason), 0
        elif unknown or sent:
            status, reason, held = "review", row["reason"] or "worker_interrupted", 1
        else:
            status, reason, held = "cancelled", row["reason"] or safeQuarantineReason(stopReason), 0
        connection.execute("UPDATE cloud_quarantine_runs SET status=?,reason=?,slot_held=?,worker_pid=NULL,revision=revision+1,updated_at=? WHERE id=?",
            (status, reason, held, time.time(), runId))
        actions._audit(settings, connection, row, "worker_reaped", detail={"workerExited": True, "slotHeld": bool(held), "status": status})


def superviseQuarantineWorker(settings, runId, token, apiToken, *, stop=None):
    process, payload = None, {}
    reason = "worker_interrupted"
    try:
        with inventoryDb(settings) as connection:
            row = actions._row(connection, runId)
            remaining = row["expires_at"]-time.time()
        if remaining <= 0 or stop is not None and stop.is_set():
            return
        deadline = time.monotonic()+min(180, remaining)
        payload = {"run_id": runId, "worker_token": token, "api_token": apiToken,
            "configuration_revision": cloudConfigurationReference(settings)}
        try:
            process = startTaskProcess(settings, "app.cloud_quarantine_worker", payload,
                lambda pid: actions.bindQuarantineWorker(settings, runId, token, pid))
        except TaskProcessStartError as exc:
            process = exc.process
            raise QuarantineError("worker_start_failed") from None
        finally:
            payload.pop("api_token", None)
            apiToken = ""
        while process.poll() is None:
            with inventoryDb(settings) as connection:
                row = actions._row(connection, runId)
                try:
                    actions._executePermission(connection, row["actor_id"])
                    permitted = True
                except QuarantineError:
                    permitted = False
            if not permitted or row["cancel_requested"] or stop is not None and stop.is_set() or time.monotonic() >= deadline or time.time() >= row["expires_at"]:
                reason = "permission_denied" if not permitted else "cancel_requested" if row["cancel_requested"] else "deadline_exceeded"
                break
            time.sleep(0.1)
    except Exception as exc:
        reason = safeQuarantineReason(getattr(exc, "code", None))
        raise
    finally:
        payload.pop("api_token", None)
        apiToken = ""
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        finishQuarantineWorker(settings, runId, token, process.pid if process else None, stopReason=reason)
