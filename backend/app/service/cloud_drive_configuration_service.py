"""CD2 配置唯一持久入口；保存只操作本地库，工作进程按修订读取。"""

from contextlib import closing
from dataclasses import asdict, replace
from hashlib import sha256
import json
import sqlite3
import time

from cryptography import x509

from app.core.config import CloudDriveSettings
from app.core.logger import get_logger
from app.service.audit_service import record_audit_event

_FIELDS = {
    "approvedRoot", "minIntervalSeconds", "failureCooldownSeconds", "webExecutionEnabled", "endpoint",
    "caCertificatePem", "directoryRenameEnabled", "directoryMappingRevision", "quarantineEnabled", "quarantineFolderName",
}


class CloudConfigurationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _parse(values):
    if not isinstance(values, dict) or values.keys() - _FIELDS:
        raise CloudConfigurationError("invalid_configuration_fields")
    pem = values.get("caCertificatePem", "")
    if not isinstance(pem, str) or len(pem) > 16384:
        raise CloudConfigurationError("invalid_public_ca")
    if pem:
        try:
            raw = pem.encode("ascii")
            if b"PRIVATE KEY" in raw or not raw.strip().startswith(b"-----BEGIN CERTIFICATE-----"):
                raise ValueError
            certificates = x509.load_pem_x509_certificates(raw)
            # 不允许夹带私钥、任意文本或其他 PEM 对象。
            from cryptography.hazmat.primitives.serialization import Encoding
            canonical = b"".join(cert.public_bytes(Encoding.PEM) for cert in certificates)
            if not certificates or b"".join(raw.split()) != b"".join(canonical.split()):
                raise ValueError
            pem = canonical.decode("ascii")
        except (ValueError, UnicodeError):
            raise CloudConfigurationError("invalid_public_ca") from None
    try:
        return CloudDriveSettings(**{**values, "caCertificatePem": pem})
    except (ValueError, TypeError):
        raise CloudConfigurationError("invalid_configuration") from None


def _read(connection):
    row = connection.execute("SELECT revision,values_json,updated_at FROM cloud_drive_configuration WHERE id=1").fetchone()
    if row is None or type(row[0]) is not int or row[0] < 0:
        raise CloudConfigurationError("configuration_unavailable")
    try:
        config = _parse(json.loads(row[1]))
    except (ValueError, TypeError):
        raise CloudConfigurationError("configuration_unavailable") from None
    return replace(config, configurationRevision=row[0]), row[2]


def _connect(settings, *, write=False):
    mode = "rw" if write else "ro"
    connection = sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=0.2)
    connection.row_factory = sqlite3.Row
    return connection


def resolveCloudDriveSettings(settings, *, expectedRevision=None):
    try:
        with closing(_connect(settings)) as connection:
            config, _ = _read(connection)
    except (sqlite3.Error, OSError) as exc:
        get_logger(__name__).warning("CD2配置读取失败 error_type=%s sqlite_code=%s", type(exc).__name__,
                                    getattr(exc, "sqlite_errorcode", None))
        raise CloudConfigurationError("configuration_unavailable") from None
    if expectedRevision is not None and (type(expectedRevision) is not int or config.configurationRevision != expectedRevision):
        raise CloudConfigurationError("configuration_changed")
    return replace(settings, cloudDrive=config)


def requireCloudConfigurationRevision(settings, connection):
    revision = settings.cloudDrive.configurationRevision
    # 显式构造的离线服务单元测试快照没有数据库修订；网页及子进程不使用此路径。
    if revision is not None:
        row = connection.execute("SELECT revision FROM cloud_drive_configuration WHERE id=1").fetchone()
        if row is None or row[0] != revision:
            raise CloudConfigurationError("configuration_changed")


def cloudConfigurationReference(settings):
    revision = settings.cloudDrive.configurationRevision
    if type(revision) is not int or revision < 0:
        raise CloudConfigurationError("configuration_unbound")
    return revision


def _blocked(connection):
    from app.service.file_task_queue_service import TASKS
    checks = (
        "SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1",
        "SELECT 1 FROM cloud_directory_probe_leases LIMIT 1",
        "SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running') LIMIT 1",
        "SELECT 1 FROM cloud_directory_executions WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1",
        "SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1",
        "SELECT 1 FROM cloud_quarantine_runs WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1",
        "SELECT 1 FROM cloud_directory_rpc_intents WHERE phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL LIMIT 1",
        "SELECT 1 FROM cloud_directory_recovery_rpcs WHERE phase IN ('sending','unknown') LIMIT 1",
        "SELECT 1 FROM cloud_quarantine_rpcs WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL LIMIT 1",
        "SELECT 1 FROM scan_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1",
        "SELECT 1 FROM background_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL LIMIT 1",
        "SELECT 1 FROM local_file_actions WHERE status IN ('prepared','applied','recovery_required') LIMIT 1",
        "SELECT 1 FROM remote_operation_items WHERE status NOT IN ('completed','failed') LIMIT 1",
        "SELECT 1 FROM remote_operation_locks WHERE status='active' LIMIT 1",
    )
    if any(connection.execute(query).fetchone() for query in checks):
        return True
    # 表名只取自现有固定任务注册表；保留令牌的终态也不能被当作已回收。
    return any(connection.execute(f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1").fetchone()
        for table, *_ in TASKS.values()
        for condition in ("execution_token IS NOT NULL", "status IN ('queued','running')"))


def _view(config, updatedAt, blocked):
    values = {key: value for key, value in asdict(config).items() if key in _FIELDS}
    return {"values": values, "revision": config.configurationRevision, "updatedAt": updatedAt,
            "changeBlocked": blocked, "reason": "cloud_work_or_unknown_pending" if blocked else None,
            "caSha256": sha256(config.caCertificatePem.encode()).hexdigest() if config.caCertificatePem else None,
            "source": "database", "networkAccess": False}


def getCloudDriveConfiguration(settings):
    try:
        with closing(_connect(settings)) as connection:
            connection.execute("BEGIN")
            config, updated = _read(connection)
            return _view(config, updated, _blocked(connection))
    except (sqlite3.Error, OSError) as exc:
        get_logger(__name__).warning("CD2配置读取失败 stage=settings_read error_type=%s sqlite_code=%s",
            type(exc).__name__, getattr(exc, "sqlite_errorcode", None))
        raise CloudConfigurationError("configuration_unavailable") from None


def saveCloudDriveConfiguration(settings, *, values, expectedRevision, auditContext):
    config = _parse(values)
    if type(expectedRevision) is not int or not 0 <= expectedRevision < 2**63 - 1:
        raise CloudConfigurationError("invalid_revision")
    try:
        with closing(_connect(settings, write=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                # 二次核对操作者，不能只依赖保存前的 HTTP 权限快照。
                from app.service.cloud_drive_budget_service import _requireActor, CloudBudgetError
                try:
                    _requireActor(connection, auditContext.actor.id if auditContext.actor else None, "settings:write")
                except CloudBudgetError:
                    raise CloudConfigurationError("configuration_permission_denied") from None
                old, _ = _read(connection)
                if old.configurationRevision != expectedRevision:
                    raise CloudConfigurationError("configuration_changed")
                if _blocked(connection):
                    raise CloudConfigurationError("cloud_work_or_unknown_pending")
                oldValues = {key: value for key, value in asdict(old).items() if key in _FIELDS}
                newValues = {key: value for key, value in asdict(config).items() if key in _FIELDS}
                changed = sorted(key for key in _FIELDS if oldValues[key] != newValues[key])
                if not changed:
                    return _view(old, connection.execute("SELECT updated_at FROM cloud_drive_configuration WHERE id=1").fetchone()[0], False)
                now = time.time()
                revision = expectedRevision + 1
                connection.execute("UPDATE cloud_drive_configuration SET revision=?,values_json=?,updated_at=? WHERE id=1",
                    (revision, json.dumps(newValues, ensure_ascii=False, separators=(",", ":")), now))
                # 同事务使旧观察/计划失效，但不改写历史路径、不自动重新盘点。
                connection.row_factory = sqlite3.Row
                from app.service.cloud_directory_path_service import advanceCloudPathEpoch
                epoch = advanceCloudPathEpoch(connection, now)
                # 无活动请求时更新政策；不清除暂停、冷却或任何历史账本。
                connection.execute("UPDATE cloud_drive_budget_pool SET min_interval=?,failure_cooldown=?,revision=revision+1 WHERE id=1",
                                   (config.minIntervalSeconds, config.failureCooldownSeconds))
                record_audit_event(settings, event_type="cloud_drive.configuration", action="update", result="success",
                    summary="更新目录整理配置；未发送云请求", target_type="cloud_drive_configuration", target_id=1,
                    actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent,
                    detail={"changedFields": changed, "revision": revision, "pathEpoch": epoch,
                            "caSha256": sha256(config.caCertificatePem.encode()).hexdigest() if config.caCertificatePem else None},
                    connection=connection)
                connection.commit()
                return _view(replace(config, configurationRevision=revision), now, False)
            except BaseException:
                connection.rollback()
                raise
    except (sqlite3.Error, OSError) as exc:
        get_logger(__name__).warning("CD2配置保存失败 stage=settings_write error_type=%s sqlite_code=%s",
            type(exc).__name__, getattr(exc, "sqlite_errorcode", None))
        raise CloudConfigurationError("configuration_storage_failed") from None
