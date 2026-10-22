"""CD2全实例单槽及批准总预算；未决请求不按超时、PID或重启自动重放。"""

from contextlib import closing, contextmanager
from hashlib import sha256
import json
import math
import os
import re
import socket
import sqlite3
import time
from uuid import UUID, uuid4

from app.core.logger import get_logger
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.cloud_drive_error_codes import CD2_RPC_FAILURE_CODES
from app.service.scan_recovery_service import isProcessAlive
from app.service.cloud_directory_guard import directoryExecutionHeld
from app.utils.cloud_drive_paths import approvedCloudRoot, canonicalCd2Target, isWithinCloudRoot

_RPC_METHODS = {"GetApiTokenInfo", "GetRuntimeInfo", "GetSubFiles"}
_FAILURE_CODES = {"unauthenticated", "permission_denied", "token_root_mismatch",
    "unknown_token_permissions", "overprivileged_or_missing_list_permission", "invalid_response",
    "invalid_runtime_version", "deadline_exceeded", "unavailable", "resource_exhausted",
    "invalid_entry_path", "out_of_scope_or_unsupported_entry", "duplicate_entry",
    "entry_limit", "byte_limit", "message_limit", "request_incomplete"} | CD2_RPC_FAILURE_CODES


class CloudBudgetError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"CD2预算拒绝继续：{code}；不会自动重试远端请求")
        self.code = code


class CloudBudgetUnavailable(CloudBudgetError):
    pass


def _identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("须使用规范UUID标识") from None
    return value


def _integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("预算数量或有效期超出允许范围")


def _wallNow():
    value = time.time()
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise CloudBudgetUnavailable("clock_invalid")
    return value


def _hostKey():
    return sha256((os.name + ":" + socket.gethostname().lower()).encode()).hexdigest()


@contextmanager
def _connection(settings, *, deadline=None, write=False, allowBusy=False):
    timeout = 0.05
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloudBudgetError("wait_timeout")
        timeout = min(timeout, remaining)
    stage = "connect"
    try:
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=" + mode, uri=True,
                                    timeout=timeout)) as connection:
            connection.row_factory = sqlite3.Row
            stage = "begin" if write else "prepare_read"
            connection.execute("PRAGMA foreign_keys=ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
                from app.service.cloud_drive_configuration_service import requireCloudConfigurationRevision
                requireCloudConfigurationRevision(settings, connection)
            try:
                stage = "body"
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                if write:
                    stage = "commit"
                    connection.commit()
    except sqlite3.Error as exc:
        code = getattr(exc, "sqlite_errorcode", None)
        if allowBusy and type(code) is int and code & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise _BudgetBusy() from None
        get_logger(__name__).warning("CD2预算存储失败 stage=%s write=%s error_type=%s sqlite_code=%s",
            stage, write, type(exc).__name__, code if type(code) is int else None)
        raise CloudBudgetUnavailable("storage_unavailable") from None
    except OSError as exc:
        get_logger(__name__).warning("CD2预算存储失败 stage=%s write=%s error_type=%s sqlite_code=None",
            stage, write, type(exc).__name__)
        raise CloudBudgetUnavailable("storage_unavailable") from None


class _BudgetBusy(Exception):
    pass


def _audit(settings, connection, action, *, context=None, detail=None, targetId="1"):
    context = context or AuditWriteContext()
    record_audit_event(settings, event_type="cloud_drive.budget", action=action, result="success",
        summary="CD2共享预算状态变更", target_type="cloud_drive_budget", target_id=targetId,
        actor=context.actor, actor_name=None if context.actor else "cloud_drive_system",
        ip_address=context.ipAddress, user_agent=context.userAgent, detail=detail, connection=connection)


def _requireActor(connection, actorId, permission):
    row = connection.execute("SELECT enabled,permissions_json FROM users WHERE id=?", (actorId,)).fetchone()
    try:
        permissions = json.loads(row["permissions_json"]) if row else []
        if not isinstance(permissions, list) or any(not isinstance(value, str) for value in permissions):
            raise ValueError
        allowed = row is not None and row["enabled"] == 1 and permission in permissions
    except (ValueError, TypeError):
        raise CloudBudgetUnavailable("actor_state_invalid") from None
    if not allowed:
        raise CloudBudgetError("actor_permission_denied")


def _validatePool(row):
    if (row["state"] not in {"ready", "paused"}
            or not isinstance(row["revision"], int) or row["revision"] < 0
            or not isinstance(row["failure_cooldown"], int) or not 60 <= row["failure_cooldown"] <= 86400):
        raise CloudBudgetUnavailable("pool_state_invalid")
    for name in ("min_interval", "observed_at", "cooldown_until", "updated_at", "last_started_at"):
        value = row[name]
        if name == "last_started_at" and value is None:
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CloudBudgetUnavailable("pool_state_invalid")
    if not 2 <= row["min_interval"] <= 300:
        raise CloudBudgetUnavailable("pool_state_invalid")
    allowedReasons = _FAILURE_CODES | {"clock_rollback", "manual_pause", "local_review_complete"}
    if ((row["state"] == "ready" and row["pause_reason"] is not None)
            or (row["state"] == "paused" and row["pause_reason"] not in allowedReasons)):
        raise CloudBudgetUnavailable("pool_state_invalid")


def _validateLease(row):
    try:
        for name in ("id", "session_id", "owner_instance"):
            _identifier(row[name])
        if (not isinstance(row["owner_pid"], int) or not 1 <= row["owner_pid"] <= 2**31 - 1
                or not isinstance(row["owner_host"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["owner_host"])
                or row["phase"] not in {"reserved", "sending", "unknown"}
                or (row["rpc_method"] is not None and row["rpc_method"] not in _RPC_METHODS)
                or (row["failure_code"] is not None and row["failure_code"] not in _FAILURE_CODES)):
            raise ValueError
    except (ValueError, TypeError):
        raise CloudBudgetUnavailable("lease_state_invalid") from None


def _pauseTx(settings, connection, pool, reason, now, *, context=None):
    # 自动失败保留管理员的暂停意图，失败原因仍写入RPC账本及审计。
    connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
        "pause_reason=CASE WHEN pause_reason='manual_pause' THEN pause_reason ELSE ? END,"
        "cooldown_until=MAX(cooldown_until,?),revision=revision+1,updated_at=? WHERE id=1",
        (reason, now + pool["failure_cooldown"], now))
    _audit(settings, connection, "pause", context=context, detail={"reason": reason})


def _pool(settings, connection, now):
    policy = settings.cloudDrive
    connection.execute("INSERT OR IGNORE INTO cloud_drive_budget_pool "
        "(id,state,min_interval,failure_cooldown,observed_at,updated_at) VALUES(1,'ready',?,?,?,?)",
        (policy.minIntervalSeconds, policy.failureCooldownSeconds, now, now))
    row = connection.execute("SELECT * FROM cloud_drive_budget_pool WHERE id=1").fetchone()
    _validatePool(row)
    if now < row["observed_at"]:
        if row["pause_reason"] != "clock_rollback":
            stricter = dict(row)
            stricter["failure_cooldown"] = max(row["failure_cooldown"], policy.failureCooldownSeconds)
            _pauseTx(settings, connection, stricter, "clock_rollback", row["observed_at"])
            connection.commit()
        raise CloudBudgetError("clock_rollback")
    connection.execute("UPDATE cloud_drive_budget_pool SET min_interval=MAX(min_interval,?),"
        "failure_cooldown=MAX(failure_cooldown,?),observed_at=?,updated_at=? WHERE id=1",
        (policy.minIntervalSeconds, policy.failureCooldownSeconds, now, now))
    return connection.execute("SELECT * FROM cloud_drive_budget_pool WHERE id=1").fetchone()


def _session(connection, sessionId, now, *, active=True):
    row = connection.execute("SELECT * FROM cloud_drive_read_sessions WHERE id=?", (sessionId,)).fetchone()
    if row is None:
        raise CloudBudgetError("session_not_found")
    if (not isinstance(row["max_rpc"], int) or not 1 <= row["max_rpc"] <= 1000
            or not isinstance(row["used_rpc"], int) or not 0 <= row["used_rpc"] <= row["max_rpc"]
            or not isinstance(row["expires_at"], (int, float)) or not math.isfinite(row["expires_at"])):
        raise CloudBudgetUnavailable("session_state_invalid")
    _requireActor(connection, row["actor_id"], "scan:run")
    if active:
        if now >= row["expires_at"]:
            connection.execute("UPDATE cloud_drive_read_sessions SET status='expired',updated_at=? WHERE id=?", (now, sessionId))
            connection.commit()
            raise CloudBudgetError("session_expired")
        if row["used_rpc"] >= row["max_rpc"]:
            connection.execute("UPDATE cloud_drive_read_sessions SET status='exhausted',updated_at=? WHERE id=?", (now, sessionId))
            connection.commit()
            raise CloudBudgetError("session_exhausted")
        if row["status"] != "active":
            raise CloudBudgetError("session_not_active")
    return row


def createCloudReadSession(settings, *, requestId, endpoint, rootPath, maxRpc, durationSeconds,
                           auditContext: AuditWriteContext):
    """仅内部批准记录；不连接远端、不保存端点明文或凭据，不创建扫描任务。"""
    _identifier(requestId)
    rootPath = approvedCloudRoot(rootPath)
    if not settings.cloudDrive.approvedRoot or not isWithinCloudRoot(rootPath, settings.cloudDrive.approvedRoot):
        raise ValueError("批准目录超出配置的媒体范围")
    endpointHash = sha256(canonicalCd2Target(endpoint).encode()).hexdigest()
    _integer(maxRpc, 1, 1000)
    _integer(durationSeconds, 30, 900)
    if auditContext.actor is None:
        raise ValueError("只读会话必须关联已授权用户")
    actorId = auditContext.actor.id
    requestHash = sha256(json.dumps([endpointHash, rootPath, maxRpc, durationSeconds, actorId],
                                   ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    with _connection(settings, write=True) as connection:
        if directoryExecutionHeld(connection):
            raise CloudBudgetError("directory_execution_held")
        now = _wallNow()
        _requireActor(connection, actorId, "scan:run")
        existing = connection.execute("SELECT * FROM cloud_drive_read_sessions WHERE request_id=?", (requestId,)).fetchone()
        if existing is not None:
            if existing["request_hash"] != requestHash:
                raise CloudBudgetError("request_conflict")
            return dict(existing)
        pool = _pool(settings, connection, now)
        if pool["state"] != "ready":
            raise CloudBudgetError("pool_paused")
        if connection.execute("SELECT COUNT(*) FROM cloud_drive_read_sessions").fetchone()[0] >= 1000:
            raise CloudBudgetError("session_capacity_reached")
        sessionId = str(uuid4())
        connection.execute("INSERT INTO cloud_drive_read_sessions "
            "(id,request_id,request_hash,endpoint_hash,root_path,actor_id,max_rpc,status,expires_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'active',?,?,?)", (sessionId, requestId, requestHash, endpointHash, rootPath,
                actorId, maxRpc, now + durationSeconds, now, now))
        _audit(settings, connection, "approve_session", context=auditContext, targetId=sessionId,
               detail={"maxRpc": maxRpc, "durationSeconds": durationSeconds, "networkAccess": False})
        return dict(connection.execute("SELECT * FROM cloud_drive_read_sessions WHERE id=?", (sessionId,)).fetchone())


def cloudBudgetStatus(settings, *, includeLeaseIdentity=False):
    with _connection(settings) as connection:
        row = connection.execute("SELECT * FROM cloud_drive_budget_pool WHERE id=1").fetchone()
        lease = connection.execute("SELECT * FROM cloud_drive_rpc_lease WHERE pool_id=1").fetchone()
        directoryHeld = directoryExecutionHeld(connection)
    if row is not None:
        _validatePool(row)
    if lease is not None:
        _validateLease(lease)
    result = {"scope": "instance", "state": row["state"] if row else "ready",
        "pauseReason": row["pause_reason"] if row else None,
        "cooldownUntil": row["cooldown_until"] if row else 0,
        "revision": row["revision"] if row else 0,
        "minIntervalSeconds": max(settings.cloudDrive.minIntervalSeconds, row["min_interval"] if row else 2),
        "failureCooldownSeconds": max(settings.cloudDrive.failureCooldownSeconds, row["failure_cooldown"] if row else 60),
        "inFlight": lease is not None, "leasePhase": lease["phase"] if lease else None,
        "directoryExecutionHeld": directoryHeld,
        "executionAvailable": False, "writeAvailable": False}
    if includeLeaseIdentity:
        result["lease"] = ({"id": lease["id"], "ownerPid": lease["owner_pid"],
                            "ownerHost": lease["owner_host"], "phase": lease["phase"]} if lease else None)
    return result


def pauseCloudBudget(settings, *, auditContext):
    with _connection(settings, write=True) as connection:
        now = _wallNow()
        if auditContext.actor is None:
            raise ValueError("管理预算必须关联用户")
        _requireActor(connection, auditContext.actor.id, "settings:write")
        pool = _pool(settings, connection, now)
        connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',"
            "pause_reason='manual_pause',"
            "revision=revision+1,updated_at=? WHERE id=1", (now,))
        _audit(settings, connection, "pause", context=auditContext, detail={"reason": "manual_pause"})
    return cloudBudgetStatus(settings)


def resumeCloudBudget(settings, *, expectedRevision, confirmRemoteIdle, auditContext):
    _integer(expectedRevision, 0, 2**63 - 1)
    if confirmRemoteIdle is not True:
        raise ValueError("恢复前须明确确认远端已静止；不会自动确认或重放请求")
    with _connection(settings, write=True) as connection:
        now = _wallNow()
        if auditContext.actor is None:
            raise ValueError("管理预算必须关联用户")
        _requireActor(connection, auditContext.actor.id, "settings:write")
        pool = _pool(settings, connection, now)
        if pool["revision"] != expectedRevision:
            raise CloudBudgetError("revision_conflict")
        if directoryExecutionHeld(connection):
            raise CloudBudgetError("directory_execution_held")
        if connection.execute("SELECT 1 FROM cloud_drive_rpc_lease WHERE pool_id=1").fetchone():
            raise CloudBudgetError("lease_requires_local_review")
        if now < pool["cooldown_until"]:
            raise CloudBudgetError("cooldown_not_finished")
        if pool["state"] == "paused":
            connection.execute("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL,"
                "revision=revision+1,updated_at=? WHERE id=1", (now,))
            _audit(settings, connection, "resume", context=auditContext,
                   detail={"remoteIdleConfirmed": True, "replayed": False})
    return cloudBudgetStatus(settings)


def releaseDirectoryManualPause(settings, *, expectedRevision, confirmClientsStopped, confirmRemoteIdle, auditContext):
    """仅撤销目录维护期间的手动暂停意图；不释放目录保护、不发RPC。"""
    _integer(expectedRevision, 0, 2**63 - 1)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True:
        raise ValueError("须明确确认目录工作进程已退出且相关远端静止；原未知请求和保护槽继续保留")
    with _connection(settings, write=True) as connection:
        if auditContext.actor is None:
            raise ValueError("管理预算必须关联用户")
        _requireActor(connection, auditContext.actor.id, "settings:write")
        now = _wallNow()
        pool = _pool(settings, connection, now)
        if pool["revision"] != expectedRevision:
            raise CloudBudgetError("revision_conflict")
        if pool["state"] != "paused" or pool["pause_reason"] != "manual_pause":
            raise CloudBudgetError("manual_pause_required")
        if now < pool["cooldown_until"]:
            raise CloudBudgetError("cooldown_not_finished")
        if connection.execute("SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1").fetchone():
            raise CloudBudgetError("lease_requires_local_review")
        originals = connection.execute("SELECT * FROM cloud_directory_executions WHERE slot_held=1").fetchall()
        quarantine = connection.execute("SELECT * FROM cloud_quarantine_runs WHERE kind='move' AND slot_held=1").fetchall()
        candidates = [(r, r["plan_id"]) for r in originals] + [(r, r["id"]) for r in quarantine]
        if (len(candidates) != 1 or candidates[0][0]["worker_pid"] is not None
                or candidates[0][0]["owner_host"] != cloudDriveHostKey() or candidates[0][0]["status"] != "review"
                or connection.execute("SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE phase='sending' LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_directory_recovery_rpcs WHERE phase='sending' LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_quarantine_runs WHERE kind='observe' AND (slot_held=1 OR worker_pid IS NOT NULL) LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_quarantine_rpcs WHERE phase='sending' LIMIT 1").fetchone()):
            raise CloudBudgetError("directory_clients_require_review")
        # 仍是paused，仅恢复申请可以在独立确认后取得新只读预算。
        connection.execute("UPDATE cloud_drive_budget_pool SET pause_reason='request_incomplete',"
            "revision=revision+1,updated_at=? WHERE id=1", (now,))
        _audit(settings, connection, "release_directory_manual_pause", context=auditContext,
            detail={"clientsStoppedConfirmed": True, "remoteIdleConfirmed": True,
                "directorySlotsPreserved": True, "originalRpcChanged": False, "networkAccess": False},
            targetId=candidates[0][1])
    return cloudBudgetStatus(settings)


def resumeCloudReadSession(settings, sessionId, *, auditContext, confirmContinue):
    _identifier(sessionId)
    if confirmContinue is not True or auditContext.actor is None:
        raise ValueError("继续只读会话须关联用户并明确确认")
    with _connection(settings, write=True) as connection:
        now = _wallNow()
        pool = _pool(settings, connection, now)
        row = _session(connection, sessionId, now, active=False)
        if row["actor_id"] != auditContext.actor.id:
            raise CloudBudgetError("actor_permission_denied")
        if pool["state"] != "ready" or connection.execute("SELECT 1 FROM cloud_drive_rpc_lease").fetchone():
            raise CloudBudgetError("pool_not_ready")
        if row["status"] != "paused" or now >= row["expires_at"] or row["used_rpc"] >= row["max_rpc"]:
            raise CloudBudgetError("session_not_resumable")
        connection.execute("UPDATE cloud_drive_read_sessions SET status='active',updated_at=? WHERE id=?", (now, sessionId))
        _audit(settings, connection, "continue_session", context=auditContext, targetId=sessionId,
               detail={"usedRpc": row["used_rpc"], "maxRpc": row["max_rpc"], "replayed": False})


class CloudDriveAdmission:
    """每个RPC单独准入；不同会话和端点仍竞争同一个持久化槽。"""

    def __init__(self, settings, sessionId, *, endpoint, rootPath):
        self.settings = settings
        self.sessionId = _identifier(sessionId)
        self.endpointHash = sha256(canonicalCd2Target(endpoint).encode()).hexdigest()
        self.rootPath = approvedCloudRoot(rootPath)
        self.ownerPid = os.getpid()
        self.ownerInstance = str(uuid4())
        self.ownerHost = _hostKey()

    def _take(self, deadline):
        with _connection(self.settings, deadline=deadline, write=True, allowBusy=True) as connection:
            if directoryExecutionHeld(connection):
                raise CloudBudgetError("directory_execution_held")
            now = _wallNow()
            pool = _pool(self.settings, connection, now)
            session = _session(connection, self.sessionId, now)
            if session["endpoint_hash"] != self.endpointHash or session["root_path"] != self.rootPath:
                raise CloudBudgetError("session_binding_mismatch")
            if not self.settings.cloudDrive.approvedRoot or not isWithinCloudRoot(self.rootPath, self.settings.cloudDrive.approvedRoot):
                raise CloudBudgetError("scope_revoked")
            if pool["state"] != "ready":
                raise CloudBudgetError("pool_paused")
            if connection.execute("SELECT 1 FROM cloud_drive_rpc_lease WHERE pool_id=1").fetchone():
                return None
            if pool["last_started_at"] is not None and now < pool["last_started_at"] + pool["min_interval"]:
                return None
            if time.monotonic() >= deadline:
                raise CloudBudgetError("wait_timeout")
            leaseId = str(uuid4())
            connection.execute("INSERT INTO cloud_drive_rpc_lease "
                "(pool_id,id,session_id,owner_pid,owner_instance,owner_host,phase,created_at) "
                "VALUES(1,?,?,?,?,?,'reserved',?)", (leaseId, self.sessionId, self.ownerPid, self.ownerInstance, self.ownerHost, now))
            return CloudRpcPermit(self, leaseId, deadline)

    @contextmanager
    def __call__(self, deadline):
        if (isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or not math.isfinite(deadline)
                or os.getpid() != self.ownerPid):
            raise CloudBudgetError("invalid_deadline_or_owner")
        permit = None
        while time.monotonic() < deadline:
            try:
                permit = self._take(deadline)
            except _BudgetBusy:
                pass
            if permit is not None:
                break
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if permit is None:
            raise CloudBudgetError("wait_timeout")
        try:
            yield permit
        except BaseException as exc:
            permit.succeeded = False
            code = getattr(exc, "code", None)
            permit.failureCode = code if isinstance(code, str) and code in _FAILURE_CODES else "request_incomplete"
            raise
        finally:
            permit.finish()


class CloudRpcPermit:
    def __init__(self, admission, leaseId, deadline):
        self.admission = admission
        self.leaseId = leaseId
        self.deadline = deadline
        self.succeeded = False
        self.failureCode = "request_incomplete"

    def _lease(self, connection):
        owner = self.admission
        row = connection.execute("SELECT * FROM cloud_drive_rpc_lease WHERE pool_id=1 AND id=? AND session_id=? "
            "AND owner_pid=? AND owner_instance=? AND owner_host=?",
            (self.leaseId, owner.sessionId, os.getpid(), owner.ownerInstance, owner.ownerHost)).fetchone()
        if row is None:
            raise CloudBudgetError("lease_ownership_lost")
        return row

    def markSending(self, method):
        if method not in _RPC_METHODS:
            raise CloudBudgetError("rpc_not_allowed")
        owner = self.admission
        with _connection(owner.settings, deadline=self.deadline, write=True) as connection:
            now = _wallNow()
            pool = _pool(owner.settings, connection, now)
            row = self._lease(connection)
            _session(connection, owner.sessionId, now)
            if row["phase"] != "reserved" or pool["state"] != "ready":
                raise CloudBudgetError("pool_or_lease_not_ready")
            if pool["last_started_at"] is not None and now < pool["last_started_at"] + pool["min_interval"]:
                raise CloudBudgetError("spacing_changed_before_send")
            if time.monotonic() >= self.deadline:
                raise CloudBudgetError("wait_timeout")
            connection.execute("UPDATE cloud_drive_rpc_lease SET phase='sending',rpc_method=? WHERE id=?", (method, self.leaseId))
            connection.execute("UPDATE cloud_drive_read_sessions SET used_rpc=used_rpc+1,updated_at=? WHERE id=?", (now, owner.sessionId))
            connection.execute("UPDATE cloud_drive_budget_pool SET last_started_at=? WHERE id=1", (now,))

    def markSucceeded(self):
        self.succeeded = True

    def markIncomplete(self, reason):
        self.succeeded = False
        self.failureCode = reason if reason in _FAILURE_CODES else "request_incomplete"

    def finish(self):
        owner = self.admission
        with _connection(owner.settings, write=True) as connection:
            now = _wallNow()
            row = self._lease(connection)
            if row["phase"] == "reserved":
                connection.execute("DELETE FROM cloud_drive_rpc_lease WHERE id=?", (self.leaseId,))
                return
            if row["phase"] != "sending":
                raise CloudBudgetError("lease_requires_local_review")
            if self.succeeded:
                connection.execute("DELETE FROM cloud_drive_rpc_lease WHERE id=?", (self.leaseId,))
                connection.execute("UPDATE cloud_drive_read_sessions SET status=CASE WHEN used_rpc>=max_rpc "
                    "THEN 'exhausted' WHEN expires_at<=? THEN 'expired' ELSE status END,updated_at=? WHERE id=?",
                    (now, now, owner.sessionId))
            else:
                pool = _pool(owner.settings, connection, now)
                connection.execute("UPDATE cloud_drive_rpc_lease SET phase='unknown',failure_code=? WHERE id=?", (self.failureCode, self.leaseId))
                connection.execute("UPDATE cloud_drive_read_sessions SET status='paused',updated_at=? WHERE id=?", (now, owner.sessionId))
                _pauseTx(owner.settings, connection, pool, self.failureCode, now)


def releaseCloudLeaseAfterReview(settings, leaseId, *, confirmClientsStopped, confirmRemoteIdle):
    _identifier(leaseId)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True:
        raise ValueError("本机释放须确认相关客户端已停止且远端静止")
    with _connection(settings) as connection:
        observed = connection.execute("SELECT * FROM cloud_drive_rpc_lease WHERE id=?", (leaseId,)).fetchone()
    if observed is None:
        raise CloudBudgetError("lease_not_found")
    _validateLease(observed)
    if observed["owner_host"] != _hostKey():
        raise CloudBudgetError("owner_not_confirmed_stopped_on_this_host")
    try:
        alive = isProcessAlive(observed["owner_pid"])
    except (OSError, ValueError, TypeError):
        raise CloudBudgetError("owner_process_state_unknown") from None
    if alive:
        raise CloudBudgetError("owner_not_confirmed_stopped_on_this_host")
    with _connection(settings, write=True) as connection:
        now = _wallNow()
        current = connection.execute("SELECT * FROM cloud_drive_rpc_lease WHERE id=?", (leaseId,)).fetchone()
        if current is None or dict(current) != dict(observed):
            raise CloudBudgetError("lease_changed_during_review")
        pool = _pool(settings, connection, now)
        connection.execute("DELETE FROM cloud_drive_rpc_lease WHERE id=?", (leaseId,))
        connection.execute("UPDATE cloud_drive_read_sessions SET status='paused',updated_at=? WHERE id=?", (now, current["session_id"]))
        _pauseTx(settings, connection, pool, "local_review_complete", now)
        _audit(settings, connection, "release_reviewed_lease",
               detail={"originalPhase": current["phase"], "processExited": True, "remoteIdleConfirmed": True, "replayed": False})


def createBudgetedCd2Client(settings, sessionId, *, endpoint, token, rootPath, **options):
    """内部工厂；没有HTTP入口，秘密仅传入连接器内存。"""
    from app.service.cd2_metadata_client import Cd2MetadataClient
    admission = CloudDriveAdmission(settings, sessionId, endpoint=endpoint, rootPath=rootPath)
    return Cd2MetadataClient(endpoint, token, rootPath, admission, **options)


def requireCloudActor(connection, actorId):
    """供盘点事务复用的直接用户权限检查，不产生网络或提交借用事务。"""
    _requireActor(connection, actorId, "scan:run")


def cloudDriveHostKey():
    return _hostKey()


def requireCloudSessionForWork(settings, connection, sessionId):
    """任务创建/领取前核对批准；不刷新期限、计数或自动回收租约。"""
    row = _session(connection, sessionId, _wallNow(), active=False)
    now = _wallNow()
    if row["status"] != "active" or now >= row["expires_at"] or row["used_rpc"] >= row["max_rpc"]:
        raise CloudBudgetError("session_not_active")
    if not settings.cloudDrive.approvedRoot or not isWithinCloudRoot(row["root_path"], settings.cloudDrive.approvedRoot):
        raise CloudBudgetError("scope_revoked")
    pool = connection.execute("SELECT * FROM cloud_drive_budget_pool WHERE id=1").fetchone()
    if pool is None:
        raise CloudBudgetUnavailable("pool_state_invalid")
    _validatePool(pool)
    if pool["state"] != "ready" or connection.execute("SELECT 1 FROM cloud_drive_rpc_lease").fetchone():
        raise CloudBudgetError("pool_not_ready")
    return row
