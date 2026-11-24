"""固定来源隔离动作的内部原子受理；不接收CD2令牌或发送请求。"""

from app.service import cloud_connection_store as store
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_task_context_service import prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view


def _acceptBoundQuarantineMove(settings, planId, *, requestId, expectedRevision, policyHash,
                               confirmMaintenance, confirmGlobalFence, accessToken, auditContext):
    if confirmMaintenance is not True or confirmGlobalFence is not True:
        raise store.CloudConnectionError('confirmation_required')
    token = None
    payload = {'planId': planId, 'planRevision': expectedRevision, 'planPolicyHash': policyHash,
               'maintenanceConfirmed': True, 'globalFenceConfirmed': True}
    def factory(connection, snapshot, resourceId, originalRequestId):
        nonlocal token
        _, token = actions.approveQuarantineRunTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, planId,
            requestId=originalRequestId, expectedRevision=expectedRevision, policyHash=policyHash,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=auditContext,
            runId=resourceId, contextId=snapshot.values['id'], approvedAt=snapshot.values['created_at'])
    receipt = prepareDerivedCloudIntent(settings, planId, requestId=requestId, action='quarantine_move',
        intentPayload=payload, resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: actions._view(connection, row))
    return receipt, token


def _ownedRunTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'cloud-quarantine:plan')
    store.requireActor(connection, auditContext, 'scan:run')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'id'
    row = connection.execute(f'SELECT * FROM cloud_quarantine_runs WHERE {column}=? AND actor_id=?', (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    expectedKind = 'quarantine_move' if row['kind'] == 'move' else 'quarantine_observe'
    if snapshot.resourceKind != expectedKind or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _runViewTx(connection, row, snapshot):
    source = _view(snapshot) if snapshot else {'source': None, 'contextId': None, 'bindingState': 'legacy_unbound'}
    return actions._view(connection, row) | source


def getBoundQuarantineRun(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedRunTx(connection, recordId, accessToken=accessToken, auditContext=auditContext, byRequest=byRequest)
        return _runViewTx(connection, row, snapshot)


def cancelBoundQuarantineRun(settings, runId, *, expectedRevision, accessToken, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRunTx(connection, runId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        actions.requestQuarantineCancelTx(settings, connection, runId, expectedRevision=expectedRevision,
            actorId=row['actor_id'], auditContext=auditContext)
        return _runViewTx(connection, actions._row(connection, runId), snapshot)


def commitBoundQuarantineRun(settings, runId, *, expectedRevision, confirmLocalOnly, accessToken, auditContext):
    from app.service.cloud_quarantine_recovery_service import commitQuarantineLocalTx, commitQuarantineObservationTx
    from app.service.cloud_task_context_service import _policyTx
    store.revisionValue(expectedRevision)
    if confirmLocalOnly is not True:
        raise store.CloudConnectionError('confirmation_required')
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRunTx(connection, runId, accessToken=accessToken, auditContext=auditContext)
        store.requireActor(connection, auditContext, 'cloud-quarantine:execute')
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        if row['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if row['committed_epoch'] is None:
            current = _policyTx(connection, row['actor_id'], snapshot.values['connection_id'], snapshot.values['profile_id'],
                snapshot.values['selected_path'], 'diagnostic_tls')
            if (current['identityHash'] != snapshot.values['recovery_identity_hash']
                    or current['source']['path_epoch'] != snapshot.values['source_epoch']):
                raise store.CloudConnectionError('context_stale')
        commit = commitQuarantineLocalTx if row['kind'] == 'move' else commitQuarantineObservationTx
        commit(localCloudPlanSettingsTx(settings, connection, snapshot), connection, runId,
            expectedRevision=expectedRevision, confirmLocalOnly=True, actorId=row['actor_id'])
        return _runViewTx(connection, actions._row(connection, runId), snapshot)


def reviewBoundQuarantineWorker(settings, runId, *, expectedRevision, confirmClientsStopped,
                                confirmRemoteIdle, accessToken, auditContext):
    from app.service.cloud_quarantine_recovery_service import reviewQuarantineWorkerTx
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedRunTx(connection, runId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        reviewQuarantineWorkerTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, runId,
            expectedRevision=expectedRevision, confirmClientsStopped=confirmClientsStopped,
            confirmRemoteIdle=confirmRemoteIdle, auditContext=auditContext)
        return _runViewTx(connection, actions._row(connection, runId), snapshot)
