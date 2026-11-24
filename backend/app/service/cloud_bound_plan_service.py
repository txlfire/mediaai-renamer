"""由原盘点派生本地计划；固定上下文与计划同事务，不授予云写能力。"""

from dataclasses import replace

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service import cloud_directory_plan_service as plans
from app.service.cloud_task_context_service import (
    prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view,
)


def localCloudPlanSettingsTx(settings, connection, snapshot):
    policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    source, directory = snapshot.connectionVersion, snapshot.directoryVersion
    # 仅供本地事务方法消费；没有worker能力，不能传给原生执行或网络准入。
    return replace(settings, cloudDrive=CloudDriveSettings(endpoint=source['endpoint'],
        caCertificatePem=source['ca_pem'], approvedRoot=directory['approved_root'],
        directoryMappingRevision=directory['mapping_key'], minIntervalSeconds=policy['min_interval'],
        failureCooldownSeconds=policy['failure_cooldown'], webExecutionEnabled=bool(directory['inventory_enabled']),
        directoryRenameEnabled=bool(directory['rename_enabled']), quarantineEnabled=bool(directory['quarantine_enabled']),
        quarantineFolderName=directory['quarantine_name']))


def createBoundDirectoryPlan(settings, *, requestId, jobId, expectedRevision, entryIds, folderTerms, accessToken, auditContext):
    plans._revision(expectedRevision)
    terms = plans.normalizeDirectoryPlanInput(entryIds, folderTerms)
    payload = {'jobId': jobId, 'inventoryRevision': expectedRevision, 'entryIds': sorted(entryIds), 'terms': terms}
    def factory(connection, snapshot, resourceId, originalRequestId):
        plans.insertDirectoryPlanTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection,
            planId=resourceId, requestId=originalRequestId, requestHash=store.intentHash(payload), jobId=jobId,
            expectedRevision=expectedRevision, entryIds=entryIds, terms=terms, now=snapshot.values['created_at'],
            auditContext=auditContext, contextId=snapshot.values['id'])
    return prepareDerivedCloudIntent(settings, jobId, requestId=requestId, action='directory_plan', intentPayload=payload,
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: _planViewTx(settings, connection, row, snapshot))


def createBoundReverseDirectoryPlan(settings, originPlanId, *, requestId, expectedRevision, accessToken, auditContext):
    from app.service.cloud_directory_reverse_service import insertReverseDirectoryPlanTx
    plans._revision(expectedRevision)
    payload = {'originPlanId': originPlanId, 'executionRevision': expectedRevision}
    def factory(connection, snapshot, resourceId, originalRequestId):
        insertReverseDirectoryPlanTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, originPlanId,
            planId=resourceId, requestId=originalRequestId, requestHash=store.intentHash(payload), expectedRevision=expectedRevision,
            auditContext=auditContext, now=snapshot.values['created_at'], contextId=snapshot.values['id'])
    return prepareDerivedCloudIntent(settings, originPlanId, requestId=requestId, action='directory_reverse', intentPayload=payload,
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: _planViewTx(settings, connection, row, snapshot))


def _ownedPlanTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'cloud-directory:plan')
    authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'id'
    row = connection.execute(f'SELECT * FROM cloud_directory_plans WHERE {column}=? AND actor_id=?', (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, authSessionId, 'history')
    if snapshot.resourceKind not in {'directory_plan', 'directory_reverse'} or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _planViewTx(settings, connection, row, snapshot):
    if snapshot is None:
        return plans._view(connection, settings, row, forceInvalidReason='legacy_unbound') | {'bindingState': 'legacy_unbound', 'source': None,
            'contextId': None, 'valid': False, 'invalidReason': 'legacy_unbound'}
    result = plans._view(connection, localCloudPlanSettingsTx(settings, connection, snapshot), row)
    try:
        # 这里只判断计划来源是否仍有效，不验证或授予原会话的网络发送能力。
        _resolveTx(connection, snapshot.values['id'], snapshot.values['actor_id'], snapshot.values['auth_session_id'], 'send')
    except store.CloudConnectionError as exc:
        result |= {'valid': False, 'invalidReason': exc.code}
    return result | _view(snapshot) | {'bindingState': 'bound'}


def getBoundDirectoryPlan(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedPlanTx(connection, recordId, accessToken=accessToken, auditContext=auditContext, byRequest=byRequest)
        return _planViewTx(settings, connection, row, snapshot)


def cancelBoundDirectoryPlan(settings, planId, *, expectedRevision, accessToken, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedPlanTx(connection, planId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        plans.cancelDirectoryPlanTx(settings, connection, row, expectedRevision=expectedRevision, auditContext=auditContext)
        current = connection.execute('SELECT * FROM cloud_directory_plans WHERE id=?', (planId,)).fetchone()
        return _planViewTx(settings, connection, current, snapshot)


def listBoundDirectoryPlans(settings, *, accessToken, auditContext, page=1, pageSize=20,
                            connectionId=None, directoryProfileId=None):
    if type(page) is not int or not 1 <= page <= 1000 or type(pageSize) is not int or not 1 <= pageSize <= 50:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'cloud-directory:plan')
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        filters, values = ['p.actor_id=?', "(p.binding_state='legacy_unbound' OR (g.allowed=1 AND d.allowed=1))"], [actorId]
        if directoryProfileId is not None and connectionId is None:
            raise store.CloudConnectionError('invalid_scope')
        for column, value in (('connection_id', connectionId), ('profile_id', directoryProfileId)):
            if value is not None:
                store.cloudId(value)
                filters.append('c.' + column + '=?')
                values.append(value)
        joins = (' FROM cloud_directory_plans p LEFT JOIN cloud_task_contexts c ON c.id=p.context_id '
            'LEFT JOIN cloud_connection_user_grants g ON g.connection_id=c.connection_id AND g.user_id=p.actor_id '
            'LEFT JOIN cloud_directory_user_grants d ON d.connection_id=c.connection_id AND d.profile_id=c.profile_id AND d.user_id=p.actor_id ')
        where = ' WHERE ' + ' AND '.join(filters)
        total = connection.execute('SELECT COUNT(*)' + joins + where, values).fetchone()[0]
        rows = connection.execute('SELECT p.id,p.request_id,p.kind,p.parent_path,p.status,p.created_at,p.expires_at,'
            'p.binding_state,p.context_id,c.connection_id,c.profile_id,c.connection_name_snapshot,c.profile_name_snapshot,'
            'current_connection.name AS current_connection_name,current_profile.name AS current_profile_name,'
            'e.request_id AS execution_request,e.status AS execution_status,e.reason AS execution_reason,e.used_rpc,e.max_rpc,'
            '(SELECT request_id FROM cloud_directory_recoveries r WHERE r.plan_id=p.id ORDER BY attempt DESC LIMIT 1) AS recovery_request,'
            '(SELECT COUNT(*) FROM cloud_directory_plan_items i WHERE i.plan_id=p.id) AS item_count' + joins +
            'LEFT JOIN cloud_connections current_connection ON current_connection.id=c.connection_id '
            'LEFT JOIN cloud_directory_profiles current_profile ON current_profile.id=c.profile_id '
            'LEFT JOIN cloud_directory_executions e ON e.plan_id=p.id' + where +
            ' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?', (*values, pageSize, (page - 1) * pageSize)).fetchall()
        return {'items': [{'id': row['id'], 'requestId': row['request_id'], 'kind': row['kind'], 'parentPath': row['parent_path'],
            'status': row['status'], 'createdAt': row['created_at'], 'expiresAt': row['expires_at'], 'itemCount': row['item_count'],
            'executionRequestId': row['execution_request'], 'executionStatus': row['execution_status'], 'executionReason': row['execution_reason'],
            'recoveryRequestId': row['recovery_request'], 'usedRpc': row['used_rpc'], 'maxRpc': row['max_rpc'],
            'bindingState': row['binding_state'], 'contextId': row['context_id'], 'source': None if row['context_id'] is None else {
                'connectionId': row['connection_id'], 'directoryProfileId': row['profile_id'],
                'connectionName': row['connection_name_snapshot'], 'directoryName': row['profile_name_snapshot'],
                'currentConnectionName': row['current_connection_name'], 'currentDirectoryName': row['current_profile_name']}}
            for row in rows], 'page': page, 'pageSize': pageSize, 'total': total, 'networkAccess': False, 'replayed': False}
