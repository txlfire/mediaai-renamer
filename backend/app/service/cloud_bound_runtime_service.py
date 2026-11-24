"""内部不可变运行引用；发送校验与原worker事实入账分开，不接受HTTP设置覆盖。"""

from dataclasses import dataclass, field
import os

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service.cloud_task_context_service import (
    _loadContextTx, _checkResourceTx, _resolveTx, requireCloudAuthSessionIdTx,
)


@dataclass(frozen=True)
class BoundCloudSettings:
    base: object = field(repr=False)
    cloudDrive: CloudDriveSettings = field(repr=False)
    contextId: str
    resourceId: str
    workerToken: str | None = field(default=None, repr=False)

    def __getattr__(self, name):
        return getattr(self.base, name)


def _runtimeTx(settings, connection, contextId, jobId, workerToken=None):
    snapshot = _loadContextTx(connection, contextId)
    if snapshot.resourceKind != 'inventory' or snapshot.resourceId != jobId:
        raise store.CloudConnectionError('resource_not_found')
    _checkResourceTx(connection, snapshot)
    policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    version, directory = snapshot.connectionVersion, snapshot.directoryVersion
    config = CloudDriveSettings(approvedRoot=directory['approved_root'], endpoint=version['endpoint'],
        caCertificatePem=version['ca_pem'], minIntervalSeconds=policy['min_interval'],
        failureCooldownSeconds=policy['failure_cooldown'], webExecutionEnabled=bool(directory['inventory_enabled']),
        directoryMappingRevision=directory['mapping_key'], quarantineFolderName=directory['quarantine_name'])
    # 此链只读，不能因版本记录中其他动作开关开启就获得写能力。
    return BoundCloudSettings(settings, config, contextId, jobId, workerToken)


def boundRuntimeTx(settings, connection, snapshot):
    return _runtimeTx(settings, connection, snapshot.values['id'], snapshot.resourceId)


def requireBoundRuntimeTx(settings, connection):
    """仅核对原来源与本机能力；允许撤权/到期后记录原响应和回收槽。"""
    if not isinstance(settings, BoundCloudSettings):
        raise store.CloudConnectionError('context_stale')
    from app.service.cloud_drive_budget_service import cloudDriveHostKey
    snapshot = _loadContextTx(connection, settings.contextId)
    if snapshot.resourceKind != 'inventory' or snapshot.resourceId != settings.resourceId:
        raise store.CloudConnectionError('context_stale')
    job = _checkResourceTx(connection, snapshot)
    original = snapshot.connectionVersion
    directory = snapshot.directoryVersion
    config = settings.cloudDrive
    if (config.endpoint != original['endpoint'] or config.caCertificatePem != original['ca_pem']
            or config.approvedRoot != directory['approved_root'] or config.directoryMappingRevision != directory['mapping_key']
            or config.directoryRenameEnabled or config.quarantineEnabled):
        raise store.CloudConnectionError('context_stale')
    if settings.workerToken is None:
        if job['status'] != 'queued' or job['worker_token'] is not None or job['worker_pid'] is not None:
            raise store.CloudConnectionError('worker_ownership_lost')
    elif (job['worker_token'] != settings.workerToken or os.getpid() not in {job['owner_pid'], job['worker_pid']}
          or job['owner_host'] != cloudDriveHostKey()):
        raise store.CloudConnectionError('worker_ownership_lost')
    return snapshot, job


def requireBoundSendTx(settings, connection, *, sessionId=None, network=False):
    """每次领取/发送重查当前权限与原会话，不能用于丢弃原发送事实。"""
    snapshot, job = requireBoundRuntimeTx(settings, connection)
    if network and (settings.workerToken is None or job['status'] != 'running' or job['worker_pid'] != os.getpid()):
        raise store.CloudConnectionError('worker_ownership_lost')
    if sessionId is not None and job['session_id'] != sessionId:
        raise store.CloudConnectionError('context_stale')
    if job['status'] not in {'queued', 'running'} or job['cancel_requested']:
        raise store.CloudConnectionError('context_stale')
    row = snapshot.values
    from app.service.cloud_drive_budget_service import requireCloudActor
    requireCloudActor(connection, row['actor_id'])
    requireCloudAuthSessionIdTx(connection, row['actor_id'], row['auth_session_id'])
    return _resolveTx(connection, row['id'], row['actor_id'], row['auth_session_id'], 'send')


def loadBoundInventoryWorker(settings, *, contextId, jobId, workerToken):
    store.cloudId(workerToken)
    with store.connectionDb(settings) as connection:
        runtime = _runtimeTx(settings, connection, contextId, jobId, workerToken)
        requireBoundRuntimeTx(runtime, connection)
        return runtime


def requireBoundSend(settings, connection, sessionId, *, network=False, method=None):
    """预算层适配，只输出稳定的拒绝原因，不输出版本内容或能力值。"""
    from app.service.cloud_connection_diagnostic_service import BoundDiagnosticSettings, requireDiagnosticSendTx
    from app.service.cloud_drive_budget_service import CloudBudgetError
    if isinstance(settings, BoundDiagnosticSettings):
        try:
            requireDiagnosticSendTx(settings, connection, sessionId, network=network, method=method)
        except store.CloudConnectionError as exc:
            raise CloudBudgetError(exc.code) from None
        return
    if not isinstance(settings, BoundCloudSettings):
        if network:
            from app.core.cloud_connection_schema import cloudMultiStoragePresent
            if cloudMultiStoragePresent(connection):
                raise CloudBudgetError('context_stale')
        return
    try:
        requireBoundSendTx(settings, connection, sessionId=sessionId, network=network)
    except store.CloudConnectionError as exc:
        raise CloudBudgetError(exc.code) from None
