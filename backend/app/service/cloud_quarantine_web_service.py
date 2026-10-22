"""明确请求才启动一次隔离/核对worker；Token仅线程内存和匿名stdin。"""

import threading

from app.core.logger import get_logger
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_quarantine_recovery_service import approveQuarantineObservation
from app.service.cloud_quarantine_worker_service import superviseQuarantineWorker, finishQuarantineWorker, safeQuarantineReason


class CloudQuarantineRunner:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._opened = False

    def open(self):
        with self._lock:
            if self._thread is not None:
                raise actions.QuarantineError("worker_slot_busy")
            self._stop.clear()
            self._opened = True

    def start(self, planId, *, apiToken, **values):
        return self._start(planId, apiToken, False, values)

    def startObservation(self, runId, *, apiToken, **values):
        return self._start(runId, apiToken, True, values)

    def _start(self, targetId, apiToken, observation, values):
        settings = resolveCloudDriveSettings(self.settings)
        if not isinstance(apiToken,str) or not 1 <= len(apiToken) <= 8192 or any(not 33 <= ord(c) <= 126 for c in apiToken):
            raise ValueError("CD2临时Token格式无效")
        with self._lock:
            if not self._opened or self._stop.is_set():
                raise actions.QuarantineError("web_runner_unavailable")
            if self._thread is not None:
                raise actions.QuarantineError("worker_slot_busy")
            approve = approveQuarantineObservation if observation else actions.approveQuarantineRun
            runId,token = approve(settings,targetId,**values)
            thread = threading.Thread(target=self._execute,args=(settings,runId,token,apiToken),name="cloud-quarantine-supervisor",daemon=False)
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                finishQuarantineWorker(settings,runId,token,None,stopReason="worker_start_failed")
                raise actions.QuarantineError("worker_start_failed") from None
            return runId

    def _execute(self, settings, runId, token, apiToken):
        try:
            superviseQuarantineWorker(settings,runId,token,apiToken,stop=self._stop)
            # 不自动同步或反向；即使全部验证通过，仍等待用户明确提交本地修订。
        except Exception as exc:
            get_logger(__name__).error("C3监督停止 run_id=%s code=%s error_type=%s",runId,
                safeQuarantineReason(getattr(exc,"code",None)),type(exc).__name__)
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
