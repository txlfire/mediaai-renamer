"""网页受控盘点：本地确认固定预算，内存交接秘密，不循环领取或重放任务。"""

from hashlib import sha256
import json
from pathlib import Path
import ssl
import threading

from app.core.logger import get_logger
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.service.cloud_drive_budget_service import createCloudReadSession, requireCloudActor
from app.service.cloud_inventory_service import (
    CloudInventoryError, inventoryDb, inventoryId, createCloudInventoryJob, cloudInventorySummary,
    claimCloudInventoryJob, safeCloudInventoryReason, _row,
)
from app.service.cloud_inventory_worker_service import superviseClaimedCloudInventoryJob, _finishOwnedProcess
from app.utils.cloud_drive_paths import approvedCloudRoot, canonicalCd2Target, isWithinCloudRoot
from app.service.cloud_directory_guard import directoryExecutionHeld

_LIMITS = {"rpc": 4, "seconds": 30, "entries": 100, "bytes": 1024 * 1024, "directories": 1, "depth": 0}


def _trustedCa(settings):
    if settings.cloudDrive.configurationRevision is not None:
        pem = settings.cloudDrive.caCertificatePem
        return pem.encode("ascii") if pem else None
    path = settings.cloudDrive.caCertificateFile
    if not path:
        return None
    try:
        # 上限包含换行和整条链；配置文件仅管理员可写，不接受HTTP上传的CA。
        with Path(path).open("rb") as stream:
            pem = stream.read(16385)
        if not pem or len(pem) > 16384 or b"PRIVATE KEY" in pem:
            raise ValueError
        ssl.create_default_context(cadata=pem.decode("ascii"))
        return pem
    except (OSError, ValueError, UnicodeError):
        raise CloudInventoryError("trust_unavailable") from None


def _policy(settings):
    config = settings.cloudDrive
    if not config.webExecutionEnabled:
        raise CloudInventoryError("web_execution_disabled")
    ca = _trustedCa(settings)
    digest = sha256(json.dumps([config.approvedRoot, canonicalCd2Target(config.endpoint),
        sha256(ca).hexdigest() if ca else "system-ca", config.minIntervalSeconds,
        config.failureCooldownSeconds, _LIMITS], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, ca


def cloudInventoryWebPolicy(settings):
    try:
        digest, _ = _policy(settings)
        with inventoryDb(settings) as connection:
            if directoryExecutionHeld(connection):
                raise CloudInventoryError("directory_execution_held")
    except CloudInventoryError as exc:
        return {"available": False, "reason": exc.code, "approvedRoot": settings.cloudDrive.approvedRoot,
            "policyHash": "", "limits": dict(_LIMITS), "networkAccess": False, "writeAvailable": False}
    return {"available": True, "reason": None, "approvedRoot": settings.cloudDrive.approvedRoot,
        "policyHash": digest, "limits": dict(_LIMITS), "networkAccess": False, "writeAvailable": False}


def _requirePolicy(settings, policyHash):
    digest, ca = _policy(settings)
    if policyHash != digest:
        raise CloudInventoryError("policy_changed")
    return ca


def prepareCloudInventoryWebJob(settings, *, requestId, selectedPath, policyHash, confirmRead, auditContext):
    if confirmRead is not True or auditContext.actor is None:
        raise ValueError("须明确确认单目录本层盘点")
    _requirePolicy(settings, policyHash)
    path = approvedCloudRoot(selectedPath)
    if not isWithinCloudRoot(path, settings.cloudDrive.approvedRoot):
        raise ValueError("所选目录超出批准媒体根")
    session = createCloudReadSession(settings, requestId=requestId, endpoint=settings.cloudDrive.endpoint,
        rootPath=path, maxRpc=_LIMITS["rpc"], durationSeconds=_LIMITS["seconds"], auditContext=auditContext)
    jobId = createCloudInventoryJob(settings, requestId=requestId, sessionId=session["id"], selectedPaths=[path],
        excludedPaths=[], mode="overview", maxEntries=_LIMITS["entries"], maxBytes=_LIMITS["bytes"],
        maxDirectories=1, maxDepth=0, quantumDirectories=1, auditContext=auditContext)
    return cloudInventorySummary(settings, jobId, auditContext.actor.id)


def cloudInventoryWebRequest(settings, requestId, actorId):
    inventoryId(requestId)
    with inventoryDb(settings) as connection:
        requireCloudActor(connection, actorId)
        row = connection.execute("SELECT id FROM cloud_drive_inventory_jobs WHERE request_id=? AND actor_id=?",
                                 (requestId, actorId)).fetchone()
    if row is None:
        raise CloudInventoryError("job_not_found")
    return cloudInventorySummary(settings, row["id"], actorId)


def validateCloudInventoryWebJob(settings, jobId, actorId):
    inventoryId(jobId)
    with inventoryDb(settings) as connection:
        job = _row(connection, jobId)
        if job["actor_id"] != actorId:
            raise CloudInventoryError("job_not_found")
        requireCloudActor(connection, actorId)
        session = connection.execute("SELECT * FROM cloud_drive_read_sessions WHERE id=?", (job["session_id"],)).fetchone()
        selected = json.loads(job["selected_json"])
        if (session is None or session["request_id"] != job["request_id"] or session["actor_id"] != actorId
                or session["endpoint_hash"] != sha256(canonicalCd2Target(settings.cloudDrive.endpoint).encode()).hexdigest()
                or session["root_path"] != job["root_path"] or selected != [job["root_path"]]
                or not isWithinCloudRoot(job["root_path"], settings.cloudDrive.approvedRoot)
                or job["mode"] != "overview" or job["exclusions_json"] != "[]"
                or job["max_entries"] != _LIMITS["entries"] or job["max_bytes"] != _LIMITS["bytes"]
                or job["max_directories"] != 1 or job["max_depth"] != 0 or job["quantum_directories"] != 1
                or session["max_rpc"] != _LIMITS["rpc"]
                or session["expires_at"] - session["created_at"] != _LIMITS["seconds"]):
            raise CloudInventoryError("web_scope_mismatch")


def startCloudInventoryWebJob(settings, runner, jobId, *, actorId, expectedRevision, apiToken, policyHash, confirmRead):
    if confirmRead is not True:
        raise ValueError("须明确确认单目录本层盘点")
    _requirePolicy(settings, policyHash)
    validateCloudInventoryWebJob(settings, jobId, actorId)
    runner.start(jobId, actorId=actorId, expectedRevision=expectedRevision,
                 apiToken=apiToken, policyHash=policyHash)
    return cloudInventorySummary(settings, jobId, actorId)


class CloudInventoryWebRunner:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._open = False

    def open(self):
        with self._lock:
            if self._thread is not None:
                raise CloudInventoryError("worker_slot_busy")
            self._stop.clear()
            self._open = True

    def start(self, jobId, *, actorId, expectedRevision, apiToken, policyHash):
        settings = resolveCloudDriveSettings(self.settings)
        if (not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in apiToken)):
            raise ValueError("CD2受限令牌格式无效")
        ca = _requirePolicy(settings, policyHash)
        validateCloudInventoryWebJob(settings, jobId, actorId)
        with self._lock:
            if not self._open or self._stop.is_set():
                raise CloudInventoryError("web_runner_unavailable")
            if self._thread is not None:
                raise CloudInventoryError("worker_slot_busy")
            token = claimCloudInventoryJob(settings, jobId, actorId=actorId, expectedRevision=expectedRevision)
            thread = threading.Thread(target=self._execute, args=(settings, jobId, token, apiToken, ca),
                                      name="cloud-inventory-supervisor", daemon=False)
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                _finishOwnedProcess(settings, jobId, token, None)
                raise CloudInventoryError("worker_start_failed") from None

    def _execute(self, settings, jobId, token, apiToken, ca):
        try:
            superviseClaimedCloudInventoryJob(settings, jobId, token, endpoint=settings.cloudDrive.endpoint,
                apiToken=apiToken, trustedCaPem=ca, stop=self._stop)
        except Exception as exc:
            get_logger(__name__).error("CD2网页监督停止 job_id=%s error_type=%s code=%s", jobId,
                type(exc).__name__, safeCloudInventoryReason(getattr(exc, "code", None)))
        finally:
            with self._lock:
                self._thread = None

    def close(self):
        with self._lock:
            self._open = False
            self._stop.set()
            thread = self._thread
        # 等待真实子进程回收；不把join超时解释为远端或worker已退出。
        if thread is not None:
            thread.join()
