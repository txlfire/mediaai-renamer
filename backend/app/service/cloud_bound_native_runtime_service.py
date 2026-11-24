"""原生目录执行的固定运行引用；原事实与当前发送权限分离。"""

from dataclasses import dataclass, field
import os

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service.cloud_drive_budget_service import cloudDriveHostKey
from app.service.cloud_task_context_service import _loadContextTx, _checkResourceTx, _resolveTx, requireCloudAuthSessionIdTx


@dataclass(frozen=True)
class BoundNativeCloudSettings:
    base: object = field(repr=False)
    cloudDrive: CloudDriveSettings = field(repr=False)
    contextId: str
    resourceId: str
    workerToken: str = field(repr=False)

    def __getattr__(self, name):
        return getattr(self.base, name)


def nativeRuntimeTx(settings, connection, contextId, token):
    store.cloudId(token)
    snapshot = _loadContextTx(connection, contextId)
    if snapshot.resourceKind not in {'directory_execute', 'directory_observe', 'quarantine_move', 'quarantine_observe'}:
        raise store.CloudConnectionError('invalid_scope')
    _checkResourceTx(connection, snapshot)
    version, directory = snapshot.connectionVersion, snapshot.directoryVersion
    policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    config = CloudDriveSettings(endpoint=version['endpoint'], caCertificatePem=version['ca_pem'],
        approvedRoot=directory['approved_root'], directoryMappingRevision=directory['mapping_key'],
        minIntervalSeconds=policy['min_interval'], failureCooldownSeconds=policy['failure_cooldown'],
        webExecutionEnabled=bool(directory['inventory_enabled']), directoryRenameEnabled=bool(directory['rename_enabled']),
        quarantineEnabled=bool(directory['quarantine_enabled']), quarantineFolderName=directory['quarantine_name'])
    return BoundNativeCloudSettings(settings, config, contextId, snapshot.resourceId, token)


def requireNativeFactsTx(settings, connection):
    snapshot = _loadContextTx(connection, settings.contextId)
    if snapshot.resourceKind not in {'directory_execute', 'directory_observe', 'quarantine_move', 'quarantine_observe'} or snapshot.resourceId != settings.resourceId:
        raise store.CloudConnectionError('context_stale')
    row = _checkResourceTx(connection, snapshot)
    config, version, directory = settings.cloudDrive, snapshot.connectionVersion, snapshot.directoryVersion
    if (config.endpoint != version['endpoint'] or config.caCertificatePem != version['ca_pem']
            or config.approvedRoot != directory['approved_root'] or config.directoryMappingRevision != directory['mapping_key']
            or config.directoryRenameEnabled != bool(directory['rename_enabled'])
            or config.quarantineEnabled != bool(directory['quarantine_enabled'])
            or config.webExecutionEnabled != bool(directory['inventory_enabled'])
            or config.quarantineFolderName != directory['quarantine_name']):
        raise store.CloudConnectionError('context_stale')
    if (row['worker_token'] != settings.workerToken or row['owner_host'] != cloudDriveHostKey()
            or os.getpid() not in {row['owner_pid'], row['worker_pid']}):
        raise store.CloudConnectionError('worker_ownership_lost')
    return snapshot, row


def requireNativeSourceTx(settings, connection, *, mode='send'):
    if mode not in {'history', 'send'}:
        raise store.CloudConnectionError('invalid_fields')
    snapshot, _ = requireNativeFactsTx(settings, connection)
    context = snapshot.values
    requireCloudAuthSessionIdTx(connection, context['actor_id'], context['auth_session_id'])
    from app.service.cloud_directory_plan_service import _permission
    permissions = ('scan:run', 'cloud-quarantine:plan', 'cloud-quarantine:execute') if snapshot.resourceKind.startswith('quarantine_') else \
        ('cloud-directory:plan', 'cloud-directory:execute')
    for permission in permissions:
        _permission(connection, context['actor_id'], permission)
    return _resolveTx(connection, context['id'], context['actor_id'], context['auth_session_id'], mode)


def requireNativeSendTx(settings, connection, *, network=False):
    snapshot = requireNativeSourceTx(settings, connection)
    row = _checkResourceTx(connection, snapshot)
    if row['status'] not in {'accepted', 'running'} or not row['slot_held'] or row['cancel_requested']:
        raise store.CloudConnectionError('context_stale')
    if network and (row['status'] != 'running' or row['worker_pid'] != os.getpid()):
        raise store.CloudConnectionError('worker_ownership_lost')
    return snapshot


def loadNativeWorker(settings, contextId, resourceId, token):
    with store.connectionDb(settings) as connection:
        runtime = nativeRuntimeTx(settings, connection, contextId, token)
        if runtime.resourceId != resourceId:
            raise store.CloudConnectionError('resource_not_found')
        requireNativeSendTx(runtime, connection, network=True)
        return runtime


def loadNativeDirectoryWorker(settings, contextId, planId, token):
    return loadNativeWorker(settings, contextId, planId, token)
