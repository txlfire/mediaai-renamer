"""原目录动作的独立只读核对上下文；不重放原写、不保存CD2令牌。"""

from app.service import cloud_connection_store as store
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_directory_recovery_service import approveDirectoryRecoveryTx, recoveryView
from app.service.cloud_task_context_service import prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view


def _acceptBoundDirectoryRecovery(settings, planId, *, requestId, expectedRevision, confirmMaintenance,
                                  confirmRemoteIdle, accessToken, auditContext):
    if confirmMaintenance is not True or confirmRemoteIdle is not True:
        raise store.CloudConnectionError('confirmation_required')
    token = None
    def factory(connection, snapshot, resourceId, originalRequestId):
        nonlocal token
        _, token = approveDirectoryRecoveryTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, planId,
            requestId=originalRequestId, expectedRevision=expectedRevision, confirmMaintenance=True, confirmRemoteIdle=True,
            auditContext=auditContext, recoveryId=resourceId, contextId=snapshot.values['id'], approvedAt=snapshot.values['created_at'])
    receipt = prepareDerivedCloudIntent(settings, planId, requestId=requestId, action='directory_observe',
        intentPayload={'planId': planId, 'executionRevision': expectedRevision, 'maintenanceConfirmed': True, 'remoteIdleConfirmed': True},
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: recoveryView(connection, row))
    return receipt, token


def _ownedRecoveryTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'cloud-directory:plan')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'id'
    row = connection.execute(f'SELECT * FROM cloud_directory_recoveries WHERE {column}=? AND actor_id=?', (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    if snapshot.resourceKind != 'directory_observe' or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _recoveryViewTx(connection, row, snapshot):
    source = _view(snapshot) if snapshot else {'source': None, 'contextId': None, 'bindingState': 'legacy_unbound'}
    return recoveryView(connection, row) | source


def getBoundDirectoryRecovery(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedRecoveryTx(connection, recordId, accessToken=accessToken, auditContext=auditContext, byRequest=byRequest)
        return _recoveryViewTx(connection, row, snapshot)


def cancelBoundDirectoryRecovery(settings, recoveryId, *, expectedRevision, accessToken, auditContext):
    from app.service.cloud_directory_recovery_service import cancelDirectoryRecoveryTx, _row
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRecoveryTx(connection, recoveryId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        cancelDirectoryRecoveryTx(settings, connection, recoveryId, expectedRevision=expectedRevision, actorId=row['actor_id'])
        return _recoveryViewTx(connection, _row(connection, recoveryId), snapshot)


def commitBoundDirectoryRecovery(settings, recoveryId, *, expectedRevision, confirmCurrentPositions, accessToken, auditContext):
    from app.service.cloud_directory_recovery_service import commitRecoveryPositionsTx, _row
    from app.service.cloud_directory_plan_service import _revision
    from app.service.cloud_task_context_service import _policyTx
    _revision(expectedRevision)
    if confirmCurrentPositions is not True:
        raise store.CloudConnectionError('confirmation_required')
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRecoveryTx(connection, recoveryId, accessToken=accessToken, auditContext=auditContext)
        store.requireActor(connection, auditContext, 'cloud-directory:execute')
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        if row['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if row['status'] != 'committed':
            current = _policyTx(connection, row['actor_id'], snapshot.values['connection_id'], snapshot.values['profile_id'],
                snapshot.values['selected_path'], 'diagnostic_tls')
            if (current['identityHash'] != snapshot.values['recovery_identity_hash']
                    or current['source']['path_epoch'] != snapshot.values['source_epoch']):
                raise store.CloudConnectionError('context_stale')
        commitRecoveryPositionsTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, recoveryId,
            expectedRevision=expectedRevision, confirmCurrentPositions=True, actorId=row['actor_id'])
        return _recoveryViewTx(connection, _row(connection, recoveryId), snapshot)


def reviewBoundDirectoryRecoveryWorker(settings, recoveryId, *, expectedRevision, confirmClientsStopped,
                                       confirmRemoteIdle, accessToken, auditContext):
    from app.service.cloud_directory_recovery_worker_service import reviewDirectoryRecoveryWorkerTx
    from app.service.cloud_directory_recovery_service import _row
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRecoveryTx(connection, recoveryId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        reviewDirectoryRecoveryWorkerTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, recoveryId,
            expectedRevision=expectedRevision, confirmClientsStopped=confirmClientsStopped,
            confirmRemoteIdle=confirmRemoteIdle, auditContext=auditContext)
        return _recoveryViewTx(connection, _row(connection, recoveryId), snapshot)
