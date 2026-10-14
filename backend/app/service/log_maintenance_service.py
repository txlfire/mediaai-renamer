"""空闲时也执行有界维护；串行后台运行，不占用异步事件循环。"""

import asyncio
from dataclasses import dataclass

from app.core.config import AppSettings, LoggingSettings
from app.core.logger import get_logger, loggingConfigurationLock, setup_logging
from app.service.file_action_audit_service import flushFileActionAuditIntents
from app.service.external_submission_authorization_service import flushExternalSubmissionAudits
from app.service.log_service import cleanup_logs
from app.service.operation_log_service import cleanup_operation_logs
from app.service.settings_service import get_effective_settings, resolveRuntimeLogging
from app.service.audit_intent_retention_service import cleanupAuditIntentRecords
from app.service.auth_service import cleanupExpiredSessions
from app.service.background_job_service import cleanupBackgroundResults

logger = get_logger(__name__)


@dataclass
class MaintenanceState:
    auditCursor: int = 0
    externalAuditCursor: str = ""
    auditCycles: int = 0
    externalAuditCycles: int = 0
    runtimeLogging: LoggingSettings | None = None


def _warnMaintenance(job: str, exc: Exception) -> None:
    try:
        logger.warning("后台日志维护失败 job=%s error_type=%s", job, type(exc).__name__)
    except Exception:  # noqa: BLE001 - 维护诊断不影响业务或后续维护任务。
        pass


def runMaintenanceCycle(settings: AppSettings, state: MaintenanceState) -> dict:
    report = {}
    try:
        with loggingConfigurationLock:
            runtime = resolveRuntimeLogging(settings)
            if runtime != state.runtimeLogging:
                setup_logging(runtime)
                state.runtimeLogging = runtime
        report["runtime"] = True
    except Exception as exc:
        _warnMaintenance("runtime", exc)
        report["runtime"] = False
    state.auditCycles += 1
    state.externalAuditCycles += 1
    auditStart = 0 if state.auditCycles % 5 == 0 else state.auditCursor
    externalAuditStart = "" if state.externalAuditCycles % 5 == 0 else state.externalAuditCursor
    for name, job in (("expired_sessions", lambda: cleanupExpiredSessions(settings, limit=200)),
                      ("background_results", lambda: cleanupBackgroundResults(settings)),
                      ("intent_retention", lambda: cleanupAuditIntentRecords(settings)),
                      ("files", lambda: cleanup_logs(settings)),
                      ("operations", lambda: cleanup_operation_logs(settings)),
                      ("audits", lambda: flushFileActionAuditIntents(settings, limit=200, afterId=auditStart)),
                      ("external_audits", lambda: flushExternalSubmissionAudits(settings, limit=200, afterId=externalAuditStart))):
        try:
            result = job()
            report[name] = result
            if name == "audits":
                state.auditCursor = result["next_id"] if result["has_more"] else 0
            if name == "external_audits":
                state.externalAuditCursor = result["next_id"] if result["has_more"] else ""
        except Exception as exc:
            _warnMaintenance(name, exc)
            report[name] = None
    return report


async def _waitForNextCycle(stop: asyncio.Event, seconds: int) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False


async def runMaintenanceLoop(settings: AppSettings, stop: asyncio.Event) -> None:
    state = MaintenanceState()
    while not stop.is_set():
        interval = 60
        try:
            await asyncio.to_thread(runMaintenanceCycle, settings, state)
            effective = await asyncio.to_thread(get_effective_settings, settings)
            interval = max(30, min(86400, int(effective.get("logging.cleanup_interval_seconds") or 60)))
        except Exception as exc:
            _warnMaintenance("cycle", exc)
        if await _waitForNextCycle(stop, interval):
            break
