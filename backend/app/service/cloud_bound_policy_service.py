"""显式来源的C2/C3页面策略；只读本地配置，不探测CD2或授予发送能力。"""

from types import SimpleNamespace

from app.service import cloud_connection_store as store
from app.service import cloud_quarantine_plan_service as plans
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_directory_plan_service import directoryPolicyHash, CloudDirectoryError, _permission
from app.service.cloud_task_context_service import _path, _policyTx, _requireContextActorTx, _requireReadyPool, resolveCloudAuthSessionTx


def boundCloudActionPolicy(settings, *, connectionId, directoryProfileId, selectedPath,
                           action, accessToken, auditContext):
    if action not in {'directory', 'quarantine'}:
        raise store.CloudConnectionError('invalid_fields')
    store.cloudId(connectionId)
    store.cloudId(directoryProfileId)
    path = _path(selectedPath)
    with store.connectionDb(settings) as connection:
        kind = 'directory_plan' if action == 'directory' else 'quarantine_plan'
        actorId = _requireContextActorTx(connection, auditContext, kind)
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        scope = _policyTx(connection, actorId, connectionId, directoryProfileId, path, kind)
        projection = SimpleNamespace(connectionVersion=scope['connectionVersion'], directoryVersion=scope['directoryVersion'])
        resolved = localCloudPlanSettingsTx(settings, connection, projection)
        reason, canExecute = None, True
        try:
            _permission(connection, actorId, 'cloud-directory:execute' if action == 'directory' else 'cloud-quarantine:execute')
        except CloudDirectoryError:
            reason, canExecute = 'permission_denied', False
        enabled = resolved.cloudDrive.directoryRenameEnabled if action == 'directory' else resolved.cloudDrive.quarantineEnabled
        if reason is None and not enabled:
            reason = 'directory_execution_disabled' if action == 'directory' else 'quarantine_execution_disabled'
        if reason is None:
            try:
                store.requireIdle(connection)
                _requireReadyPool(connection)
            except store.CloudConnectionError as exc:
                reason = exc.code
        common = {'connectionId': connectionId, 'directoryProfileId': directoryProfileId, 'selectedPath': path,
            'revisions': scope['revisions'], 'networkAccess': False, 'writeAvailable': False,
            'controlledMode': True, 'atomicIdentityGuaranteed': False, 'automaticRescan': False}
        if action == 'directory':
            return common | {'available': reason is None, 'reason': reason, 'reasonCode': reason,
                'policyHash': directoryPolicyHash(resolved), 'oldPlansGloballyInvalidated': True,
                'limits': {'items': 5, 'baseRpc': 3, 'rpcPerItem': 7, 'seconds': 180, 'bytes': 1048576}}
        return common | {'planAvailable': True, 'reason': None, 'policyHash': plans.quarantinePolicyHash(resolved),
            'executionEnabled': enabled, 'executionImplemented': True, 'canExecute': canExecute,
            'executionAvailable': reason is None, 'executionReason': reason, 'targetName': resolved.cloudDrive.quarantineFolderName,
            'limits': {'items': plans.MAX_ITEMS, 'seconds': plans.PLAN_SECONDS, 'plans': plans.MAX_PLANS,
                'snapshotBytes': plans.MAX_SNAPSHOT_BYTES, 'totalLogicalBytes': plans.MAX_TOTAL_BYTES,
                'executionSeconds': 180, 'baseRpc': 3, 'rpcPerItem': 9, 'bytes': 1048576, 'observeSeconds': 90}}
