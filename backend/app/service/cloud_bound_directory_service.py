"""固定来源的目录执行受理事务；本模块不接收CD2秘密、不启动或发送请求。"""

from app.service import cloud_connection_store as store
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_directory_admission_service import approveDirectoryExecutionTx
from app.service.cloud_directory_plan_service import CloudDirectoryError, directoryPolicyHash
from app.service.cloud_directory_web_service import _view as executionView
from app.service.cloud_task_context_service import prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view


def _acceptBoundDirectoryExecution(settings, planId, *, requestId, expectedRevision, policyHash,
                                   confirmMaintenance, confirmGlobalFence, accessToken, auditContext):
    """只供后续受监督runner调用；重放回执不提供新的worker能力。"""
    if confirmMaintenance is not True or confirmGlobalFence is not True:
        raise store.CloudConnectionError('confirmation_required')
    token = None
    payload = {'planId': planId, 'planRevision': expectedRevision, 'planPolicyHash': policyHash,
               'maintenanceConfirmed': True, 'globalFenceConfirmed': True}
    def factory(connection, snapshot, resourceId, originalRequestId):
        nonlocal token
        if resourceId != planId:
            raise store.CloudConnectionError('storage_unavailable')
        resolved = localCloudPlanSettingsTx(settings, connection, snapshot)
        if policyHash != directoryPolicyHash(resolved):
            raise CloudDirectoryError('policy_changed')
        token = approveDirectoryExecutionTx(resolved, connection, planId, requestId=originalRequestId,
            expectedRevision=expectedRevision, confirmMaintenance=True, confirmGlobalFence=True, auditContext=auditContext,
            contextId=snapshot.values['id'], approvedAt=snapshot.values['created_at'])
    receipt = prepareDerivedCloudIntent(settings, planId, requestId=requestId, action='directory_execute',
        intentPayload=payload, resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: executionView(connection, row))
    return receipt, token


def _ownedExecutionTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'cloud-directory:plan')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'plan_id'
    row = connection.execute(f'SELECT * FROM cloud_directory_executions WHERE {column}=? AND actor_id=?',
        (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    if snapshot.resourceKind != 'directory_execute' or snapshot.resourceId != row['plan_id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _executionViewTx(connection, row, snapshot):
    source = _view(snapshot) if snapshot else {'contextId': None, 'source': None, 'bindingState': 'legacy_unbound'}
    return executionView(connection, row) | source


def getBoundDirectoryExecution(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedExecutionTx(connection, recordId, accessToken=accessToken,
            auditContext=auditContext, byRequest=byRequest)
        return _executionViewTx(connection, row, snapshot)


def cancelBoundDirectoryExecution(settings, planId, *, expectedRevision, accessToken, auditContext):
    from app.service.cloud_directory_web_service import requestDirectoryCancelTx
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedExecutionTx(connection, planId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        requestDirectoryCancelTx(settings, connection, planId, expectedRevision=expectedRevision, auditContext=auditContext)
        row = connection.execute('SELECT * FROM cloud_directory_executions WHERE plan_id=?', (planId,)).fetchone()
        return _executionViewTx(connection, row, snapshot)


def commitBoundDirectoryLocal(settings, planId, *, expectedRevision, confirmLocalOnly, accessToken, auditContext):
    from app.service.cloud_directory_plan_service import _revision
    from app.service.cloud_directory_path_service import commitDirectoryPathRevisionTx, cloudPathEpoch
    from app.service.cloud_task_context_service import _policyTx
    _revision(expectedRevision)
    if confirmLocalOnly is not True:
        raise store.CloudConnectionError('confirmation_required')
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedExecutionTx(connection, planId, accessToken=accessToken, auditContext=auditContext)
        store.requireActor(connection, auditContext, 'cloud-directory:execute')
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        if row['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if row['status'] != 'completed' or row['slot_held']:
            current = _policyTx(connection, row['actor_id'], snapshot.values['connection_id'], snapshot.values['profile_id'],
                snapshot.values['selected_path'], 'diagnostic_tls')
            plan = connection.execute('SELECT p.*,j.path_epoch AS inventory_epoch FROM cloud_directory_plans p '
                'LEFT JOIN cloud_drive_inventory_jobs j ON j.id=p.inventory_job_id WHERE p.id=?', (planId,)).fetchone()
            basis = plan['basis_epoch'] if plan['kind'] == 'reverse' else plan['inventory_epoch']
            if (current['identityHash'] != snapshot.values['recovery_identity_hash']
                    or current['source']['path_epoch'] != snapshot.values['source_epoch']
                    or basis != cloudPathEpoch(connection)['revision']):
                raise store.CloudConnectionError('context_stale')
        commitDirectoryPathRevisionTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, planId,
            actorId=row['actor_id'], auditContext=auditContext)
        row = connection.execute('SELECT * FROM cloud_directory_executions WHERE plan_id=?', (planId,)).fetchone()
        return _executionViewTx(connection, row, snapshot)
