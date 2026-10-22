"""受控目录动作入口：仅显式受理一次，以原UUID查询，不自动启动历史意图。"""

import threading
import time

from app.core.logger import get_logger
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.service.cloud_directory_admission_service import approveDirectoryExecution, _row, _audit
from app.service.cloud_directory_guard import directoryExecutionHeld
from app.service.cloud_directory_path_service import commitDirectoryPathRevision
from app.service.cloud_directory_plan_service import CloudDirectoryError, _owned, _permission, _revision, directoryPolicyHash
from app.service.cloud_directory_worker_service import superviseDirectoryWorker, finishDirectoryWorker, safeDirectoryReason
from app.service.cloud_inventory_service import inventoryDb, inventoryId


def directoryWebPolicy(settings):
    config = settings.cloudDrive
    limits = {"items": 5, "baseRpc": 3, "rpcPerItem": 7, "seconds": 180, "bytes": 1024 * 1024}
    reason, digest = None, ""
    if not config.directoryRenameEnabled:
        reason = "directory_execution_disabled"
    else:
        try:
            digest = directoryPolicyHash(settings)
            with inventoryDb(settings) as connection:
                if directoryExecutionHeld(connection):
                    reason = "directory_execution_held"
        except Exception as exc:
            reason = safeDirectoryReason(getattr(exc, "code", None))
    return {"available": reason is None, "reason": reason, "policyHash": digest, "limits": limits,
            "controlledMode": True, "atomicIdentityGuaranteed": False, "oldPlansGloballyInvalidated": True,
            "automaticRescan": False, "networkAccess": False}


def _view(connection, row):
    from app.service.cloud_directory_recovery_service import recoveryView
    latestRecovery = connection.execute("SELECT * FROM cloud_directory_recoveries WHERE plan_id=? ORDER BY attempt DESC LIMIT 1", (row["plan_id"],)).fetchone()
    items = connection.execute("SELECT p.ordinal,p.source_name,p.target_name,o.state,o.reason,o.sent_at,o.verified_at "
        "FROM cloud_directory_plan_items p LEFT JOIN cloud_directory_item_outcomes o ON o.plan_id=p.plan_id AND o.ordinal=p.ordinal "
        "WHERE p.plan_id=? ORDER BY p.ordinal", (row["plan_id"],)).fetchall()
    events = connection.execute("SELECT ordinal,method,item_ordinal,phase,response_code,received_bytes,sent_at,responded_at "
        "FROM cloud_directory_rpc_intents WHERE plan_id=? ORDER BY ordinal LIMIT 38", (row["plan_id"],)).fetchall()
    return {"planId": row["plan_id"], "requestId": row["request_id"], "status": row["status"], "reason": row["reason"],
        "revision": row["revision"], "cancelRequested": bool(row["cancel_requested"]), "workerSlotHeld": row["worker_pid"] is not None,
        # 超时只停止页面轮询，不能据此释放槽或认定远端请求已经结束。
        "pollingRecommended": (row["status"] in {"accepted", "running"} or row["worker_pid"] is not None) and time.time() < row["expires_at"],
        "globalSlotHeld": bool(row["slot_held"]), "usedRpc": row["used_rpc"], "maxRpc": row["max_rpc"],
        "receivedBytes": row["received_bytes"], "maxBytes": row["max_bytes"], "createdAt": row["created_at"], "expiresAt": row["expires_at"],
        "logId": row["log_id"], "logAvailable": connection.execute("SELECT 1 FROM operation_logs WHERE task_type='cloud_directory_execution' AND task_id=? LIMIT 1", (row["log_id"],)).fetchone() is not None,
        "latestRecovery": recoveryView(connection, latestRecovery) if latestRecovery else None,
        "items": [{"ordinal": item["ordinal"], "sourceName": item["source_name"], "targetName": item["target_name"],
            "state": item["state"] or "not_started", "reason": item["reason"], "sentAt": item["sent_at"], "verifiedAt": item["verified_at"]} for item in items],
        "events": [{"ordinal": item["ordinal"], "method": item["method"], "itemOrdinal": item["item_ordinal"],
            "phase": item["phase"], "responseCode": item["response_code"], "receivedBytes": item["received_bytes"],
            "sentAt": item["sent_at"], "respondedAt": item["responded_at"]} for item in events],
        "networkAccess": False, "replayed": False}


def getDirectoryExecution(settings, planId, actorId):
    inventoryId(planId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _owned(connection, planId, actorId)
        row = _row(connection, planId)
        return _view(connection, row)


def findDirectoryExecutionRequest(settings, requestId, actorId):
    inventoryId(requestId)
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permission(connection, actorId)
        row = connection.execute("SELECT * FROM cloud_directory_executions WHERE request_id=? AND actor_id=?", (requestId, actorId)).fetchone()
        if row is None:
            raise CloudDirectoryError("not_found")
        return _view(connection, row)


def requestDirectoryCancel(settings, planId, *, expectedRevision, auditContext):
    _revision(expectedRevision)
    with inventoryDb(settings, write=True) as connection:
        _owned(connection, planId, auditContext.actor.id)
        row = _row(connection, planId)
        if row["cancel_requested"] or row["status"] in {"completed", "cancelled"}:
            return _view(connection, row)
        if row["revision"] != expectedRevision:
            raise CloudDirectoryError("revision_conflict")
        connection.execute("UPDATE cloud_directory_executions SET cancel_requested=1,revision=revision+1,updated_at=? WHERE plan_id=?", (time.time(), planId))
        _audit(settings, connection, row, "cancel_requested", context=auditContext,
               detail={"remoteOutcomeNotAssumed": True, "replayed": False})
        return _view(connection, _row(connection, planId))


class CloudDirectoryRunner:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._opened = False

    def open(self):
        with self._lock:
            if self._thread is not None:
                raise CloudDirectoryError("worker_slot_busy")
            self._stop.clear()
            self._opened = True

    def start(self, planId, *, requestId, expectedRevision, apiToken, policyHash, confirmMaintenance, confirmGlobalFence, auditContext):
        settings = resolveCloudDriveSettings(self.settings)
        if confirmGlobalFence is not True or confirmMaintenance is not True:
            raise ValueError("须确认受控维护窗口、外部并发风险及旧计划失效，不会自动补扫")
        if (not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192 or any(not 33 <= ord(char) <= 126 for char in apiToken)):
            raise ValueError("CD2临时令牌格式无效")
        if policyHash != directoryPolicyHash(settings):
            raise CloudDirectoryError("policy_changed")
        with self._lock:
            if not self._opened or self._stop.is_set():
                raise CloudDirectoryError("web_runner_unavailable")
            if self._thread is not None:
                raise CloudDirectoryError("worker_slot_busy")
            token = approveDirectoryExecution(settings, planId, requestId=requestId, expectedRevision=expectedRevision,
                confirmMaintenance=confirmMaintenance, confirmGlobalFence=confirmGlobalFence, auditContext=auditContext)
            thread = threading.Thread(target=self._execute, args=(settings, planId, token, apiToken, auditContext.actor.id),
                name="cloud-directory-supervisor", daemon=False)
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                finishDirectoryWorker(settings, planId, token, None, stopReason="worker_start_failed")
                raise CloudDirectoryError("worker_start_failed") from None

    def _execute(self, settings, planId, token, apiToken, actorId):
        try:
            superviseDirectoryWorker(settings, planId, token, apiToken, stop=self._stop)
            # 这里只提交已经完整核验的本地修订；失败保留槽，不重发RenameFile。
            with inventoryDb(settings) as connection:
                ready = _row(connection, planId)["reason"] == "awaiting_path_sync"
            if ready:
                commitDirectoryPathRevision(settings, planId, actorId=actorId)
        except Exception as exc:
            get_logger(__name__).error("C2监督或本地同步停止 plan_id=%s error_type=%s code=%s", planId,
                                      type(exc).__name__, safeDirectoryReason(getattr(exc, "code", None)))
        finally:
            apiToken = ""
            with self._lock:
                self._thread = None

    def startRecovery(self, planId, *, requestId, expectedRevision, apiToken, confirmMaintenance, confirmRemoteIdle, auditContext):
        settings = resolveCloudDriveSettings(self.settings)
        from app.service.cloud_directory_recovery_service import approveDirectoryRecovery
        from app.service.cloud_directory_recovery_worker_service import finishRecoveryWorker
        if not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192 or any(not 33 <= ord(char) <= 126 for char in apiToken):
            raise ValueError("CD2只读临时令牌格式无效")
        with self._lock:
            if not self._opened or self._stop.is_set():
                raise CloudDirectoryError("web_runner_unavailable")
            if self._thread is not None:
                raise CloudDirectoryError("worker_slot_busy")
            recoveryId, token = approveDirectoryRecovery(settings, planId, requestId=requestId, expectedRevision=expectedRevision,
                confirmMaintenance=confirmMaintenance, confirmRemoteIdle=confirmRemoteIdle, auditContext=auditContext)
            thread = threading.Thread(target=self._recover, args=(settings, recoveryId, token, apiToken), name="cloud-directory-read-recovery", daemon=False)
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                finishRecoveryWorker(settings, recoveryId, token, None, reason="worker_start_failed")
                raise CloudDirectoryError("worker_start_failed") from None
            return recoveryId

    def _recover(self, settings, recoveryId, token, apiToken):
        from app.service.cloud_directory_recovery_worker_service import superviseDirectoryRecovery
        try:
            superviseDirectoryRecovery(settings, recoveryId, token, apiToken, stop=self._stop)
        except Exception as exc:
            get_logger(__name__).error("C2只读恢复监督停止 recovery_id=%s error_type=%s code=%s", recoveryId,
                type(exc).__name__, safeDirectoryReason(getattr(exc, "code", None)))
        finally:
            apiToken = ""
            with self._lock:
                self._thread = None

    def close(self):
        with self._lock:
            self._opened = False
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join()
