"""从原人工复核派生固定来源隔离计划；本模块没有网络能力。"""

from dataclasses import replace

from app.service import cloud_connection_store as store
from app.service import cloud_quarantine_plan_service as plans
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_task_context_service import prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view, _policyTx


def createBoundQuarantinePlan(settings, *, requestId, reviewId, entryIds, targetEntryId, accessToken, auditContext):
    normalized = plans.normalizeQuarantinePlanInput(entryIds, targetEntryId)
    payload = {'reviewId': reviewId, 'entryIds': normalized, 'targetEntryId': targetEntryId}
    def factory(connection, snapshot, resourceId, originalRequestId):
        plans.insertQuarantinePlanTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection,
            planId=resourceId, requestId=originalRequestId, requestHash=store.intentHash(payload), reviewId=reviewId,
            entryIds=normalized, targetEntryId=targetEntryId, now=snapshot.values['created_at'],
            auditContext=auditContext, contextId=snapshot.values['id'])
    return prepareDerivedCloudIntent(settings, reviewId, requestId=requestId, action='quarantine_plan', intentPayload=payload,
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: _planViewTx(settings, connection, row, snapshot))


def _ownedPlanTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'cloud-quarantine:plan')
    store.requireActor(connection, auditContext, 'scan:run')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'id'
    row = connection.execute(f'SELECT * FROM cloud_quarantine_plans WHERE {column}=? AND actor_id=?', (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    if snapshot.resourceKind not in {'quarantine_plan', 'quarantine_reverse'} or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _planViewTx(settings, connection, row, snapshot):
    if snapshot is None:
        return plans._view(connection, settings, row, forceInvalidReason='legacy_unbound') | {
            'bindingState': 'legacy_unbound', 'contextId': None, 'source': None}
    result = plans._view(connection, localCloudPlanSettingsTx(settings, connection, snapshot), row)
    try:
        _resolveTx(connection, snapshot.values['id'], snapshot.values['actor_id'], snapshot.values['auth_session_id'], 'send')
    except store.CloudConnectionError as exc:
        result |= {'valid': False, 'invalidReason': exc.code}
    return result | _view(snapshot) | {'bindingState': 'bound'}


def getBoundQuarantinePlan(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedPlanTx(connection, recordId, accessToken=accessToken, auditContext=auditContext, byRequest=byRequest)
        return _planViewTx(settings, connection, row, snapshot)


def createBoundReverseQuarantinePlan(settings, planId, *, requestId, expectedRevision, accessToken, auditContext):
    from app.service.cloud_quarantine_recovery_service import insertReverseQuarantinePlanTx
    store.revisionValue(expectedRevision)
    # 先取得明确原计划的执行编号；派生事务仍会重新核对完整来源与当前位置依据。
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedPlanTx(connection, planId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        original = connection.execute("SELECT id FROM cloud_quarantine_runs WHERE plan_id=? AND kind='move'", (planId,)).fetchone()
        if original is None:
            raise plans.QuarantineError('reverse_basis_changed')
        originalId = original['id']
    payload = {'originPlanId': planId, 'planRevision': expectedRevision}
    def factory(connection, snapshot, resourceId, originalRequestId):
        insertReverseQuarantinePlanTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, planId,
            requestId=originalRequestId, expectedRevision=expectedRevision, auditContext=auditContext,
            newId=resourceId, contextId=snapshot.values['id'], createdAt=snapshot.values['created_at'])
    return prepareDerivedCloudIntent(settings, originalId, requestId=requestId, action='quarantine_reverse',
        intentPayload=payload, resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: _planViewTx(settings, connection, row, snapshot))


def cancelBoundQuarantinePlan(settings, planId, *, expectedRevision, accessToken, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedPlanTx(connection, planId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        plans.cancelQuarantinePlanTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection, planId,
            expectedRevision=expectedRevision, auditContext=auditContext)
        row = connection.execute('SELECT * FROM cloud_quarantine_plans WHERE id=?', (planId,)).fetchone()
        return _planViewTx(settings, connection, row, snapshot)


def boundQuarantineTargets(settings, reviewId, entryId, *, accessToken, auditContext):
    store.cloudId(reviewId)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'cloud-quarantine:plan')
        store.requireActor(connection, auditContext, 'scan:run')
        sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
        review = connection.execute('SELECT * FROM cloud_ad_reviews WHERE id=? AND actor_id=?', (reviewId, actorId)).fetchone()
        if review is None:
            raise store.CloudConnectionError('resource_not_found')
        if review['binding_state'] != 'bound':
            raise store.CloudConnectionError('legacy_unbound')
        snapshot = _resolveTx(connection, review['context_id'], actorId, sessionId, 'history')
        if snapshot.resourceKind != 'ad_review' or snapshot.resourceId != reviewId:
            raise store.CloudConnectionError('storage_unavailable')
        policy = _policyTx(connection, actorId, snapshot.values['connection_id'], snapshot.values['profile_id'],
            snapshot.values['selected_path'], 'quarantine_plan')
        if policy['identityHash'] != snapshot.values['recovery_identity_hash']:
            raise store.CloudConnectionError('context_stale')
        # 当前目标名来自同一来源的明确版本；这只是本地配置投影，没有worker能力。
        current = replace(snapshot, connectionVersion=policy['connectionVersion'], directoryVersion=policy['directoryVersion'])
        return plans.quarantineTargetsTx(localCloudPlanSettingsTx(settings, connection, current), connection, reviewId, entryId, actorId)


def listBoundQuarantinePlans(settings, *, accessToken, auditContext, page=1, pageSize=20,
                             connectionId=None, directoryProfileId=None):
    plans._limit(page, 1, 1000)
    plans._limit(pageSize, 1, 50)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'cloud-quarantine:plan')
        store.requireActor(connection, auditContext, 'scan:run')
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        if directoryProfileId is not None and connectionId is None:
            raise store.CloudConnectionError('invalid_scope')
        filters, values = ['p.actor_id=?', "(p.binding_state='legacy_unbound' OR (g.allowed=1 AND d.allowed=1))"], [actorId]
        for key, value in (('connection_id', connectionId), ('profile_id', directoryProfileId)):
            if value is not None:
                store.cloudId(value)
                filters.append('c.' + key + '=?')
                values.append(value)
        joins = (' FROM cloud_quarantine_plans p LEFT JOIN cloud_task_contexts c ON c.id=p.context_id '
            'LEFT JOIN cloud_connection_user_grants g ON g.connection_id=c.connection_id AND g.user_id=p.actor_id '
            'LEFT JOIN cloud_directory_user_grants d ON d.connection_id=c.connection_id AND d.profile_id=c.profile_id AND d.user_id=p.actor_id ')
        where = ' WHERE ' + ' AND '.join(filters)
        total = connection.execute('SELECT COUNT(*)' + joins + where, values).fetchone()[0]
        rows = connection.execute('SELECT p.id,p.request_id,p.review_id,p.job_id,p.status,p.kind,p.revision,p.item_count,p.created_at,p.expires_at,'
            'p.binding_state,p.context_id,c.connection_id,c.profile_id,c.connection_name_snapshot,c.profile_name_snapshot,'
            'cc.name AS current_connection_name,cp.name AS current_profile_name' + joins +
            'LEFT JOIN cloud_connections cc ON cc.id=c.connection_id LEFT JOIN cloud_directory_profiles cp ON cp.id=c.profile_id' + where +
            ' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?', (*values, pageSize, (page - 1) * pageSize)).fetchall()
        return {'items': [{'id': row['id'], 'requestId': row['request_id'], 'reviewId': row['review_id'], 'inventoryJobId': row['job_id'],
            'status': row['status'], 'kind': row['kind'], 'revision': row['revision'], 'itemCount': row['item_count'],
            'createdAt': row['created_at'], 'expiresAt': row['expires_at'], 'bindingState': row['binding_state'], 'contextId': row['context_id'],
            'source': None if row['context_id'] is None else {'connectionId': row['connection_id'], 'directoryProfileId': row['profile_id'],
                'connectionName': row['connection_name_snapshot'], 'directoryName': row['profile_name_snapshot'],
                'currentConnectionName': row['current_connection_name'], 'currentDirectoryName': row['current_profile_name']}}
            for row in rows], 'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False, 'writeAvailable': False}
