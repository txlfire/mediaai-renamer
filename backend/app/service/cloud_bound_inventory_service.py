"""多连接盘点本地受理与原来源查询；不接收CD2 Token，不启动worker。"""

from hashlib import sha256

from app.service import cloud_connection_store as store
from app.service.cloud_drive_budget_service import insertCloudReadSessionTx, CloudBudgetError
from app.service.cloud_inventory_service import (
    insertCloudInventoryJobTx, cloudInventorySummaryTx, CloudInventoryError,
    cloudInventoryEntriesTx, cloudInventoryDirectoriesTx, cancelCloudInventoryJobTx,
)
from app.service.cloud_task_context_service import (
    INVENTORY_LIMITS, prepareCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view,
)


def _factory(settings, auditContext):
    def create(connection, snapshot, resourceId, requestId):
        context = snapshot.values
        now = context['created_at']
        try:
            policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
            connection.execute('INSERT OR IGNORE INTO cloud_drive_budget_pool '
                "(id,state,min_interval,failure_cooldown,observed_at,updated_at) VALUES(1,'ready',?,?,?,?)",
                (policy['min_interval'], policy['failure_cooldown'], now, now))
            session = insertCloudReadSessionTx(settings, connection, requestId=requestId,
                requestHash=store.intentHash({'purpose': 'bound-session', 'contextId': context['id']}),
                endpointHash=sha256(snapshot.connectionVersion['target_key'].encode()).hexdigest(),
                rootPath=context['selected_path'], actorId=context['actor_id'], maxRpc=INVENTORY_LIMITS['rpc'],
                durationSeconds=INVENTORY_LIMITS['seconds'], now=now, auditContext=auditContext, contextId=context['id'])
            insertCloudInventoryJobTx(settings, connection, jobId=resourceId, requestId=requestId,
                requestHash=store.intentHash({'purpose': 'bound-inventory', 'contextId': context['id']}),
                session=session, plan={'scopes': [{'path': context['selected_path']}]}, exclusions='[]', mode='overview',
                maxEntries=INVENTORY_LIMITS['entries'], maxBytes=INVENTORY_LIMITS['bytes'], maxDirectories=1,
                maxDepth=0, quantumDirectories=1, now=now, auditContext=auditContext, contextId=context['id'])
        except (CloudBudgetError, CloudInventoryError) as exc:
            raise store.CloudConnectionError('capacity_reached' if exc.code in {
                'session_capacity_reached', 'queue_capacity'} else 'storage_unavailable') from None
    return create


def prepareBoundCloudInventory(settings, *, requestId, connectionId, directoryProfileId, selectedPath, expected,
                               policyHash, confirmRead, accessToken, auditContext):
    if confirmRead is not True:
        raise store.CloudConnectionError('confirmation_required')
    return prepareCloudIntent(settings, requestId=requestId, connectionId=connectionId,
        directoryProfileId=directoryProfileId, selectedPath=selectedPath, expected=expected, policyHash=policyHash,
        accessToken=accessToken, auditContext=auditContext, resourceFactory=_factory(settings, auditContext))


def _ownedJobTx(connection, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'scan:run')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'request_id' if byRequest else 'id'
    row = connection.execute(f'SELECT * FROM cloud_drive_inventory_jobs WHERE {column}=? AND actor_id=?',
                             (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    if row['binding_state'] == 'legacy_unbound':
        return row, None
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    if snapshot.resourceKind != 'inventory' or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _summaryTx(connection, row, snapshot):
    result = cloudInventorySummaryTx(connection, row)
    if snapshot is None:
        return result | {'bindingState': 'legacy_unbound', 'contextId': None, 'source': None,
                         'requestId': row['request_id'], 'networkAccess': False}
    return result | _view(snapshot) | {'bindingState': 'bound', 'jobId': row['id']}


def boundCloudInventorySummary(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedJobTx(connection, recordId, accessToken=accessToken,
                                   auditContext=auditContext, byRequest=byRequest)
        return _summaryTx(connection, row, snapshot)


def boundCloudInventoryObservations(settings, jobId, *, accessToken, auditContext, afterId=0, limit=100,
                                    directories=False, page=None):
    with store.connectionDb(settings) as connection:
        row, _ = _ownedJobTx(connection, jobId, accessToken=accessToken, auditContext=auditContext)
        if directories:
            return cloudInventoryDirectoriesTx(connection, row, afterId=afterId, limit=limit, page=page)
        return cloudInventoryEntriesTx(connection, row, afterId=afterId, limit=limit)


def cancelBoundCloudInventory(settings, jobId, *, accessToken, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedJobTx(connection, jobId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        cancelCloudInventoryJobTx(settings, connection, row, auditContext=auditContext)
        current = connection.execute('SELECT * FROM cloud_drive_inventory_jobs WHERE id=?', (row['id'],)).fetchone()
        return _summaryTx(connection, current, snapshot)


def boundCloudInventoryAdvice(settings, jobId, *, accessToken, auditContext, **options):
    from app.service.cloud_inventory_advice_service import cloudInventoryAdviceTx
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedJobTx(connection, jobId, accessToken=accessToken, auditContext=auditContext)
        result = cloudInventoryAdviceTx(connection, row, **options)
        return result | {'bindingState': row['binding_state'],
                         'source': _view(snapshot)['source'] if snapshot is not None else None}


def setBoundCloudInventoryKeep(settings, jobId, entryId, *, kept, expectedRevision, accessToken, auditContext):
    from app.service.cloud_inventory_lifecycle_service import setCloudInventoryKeepTx
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedJobTx(connection, jobId, accessToken=accessToken, auditContext=auditContext)
        if snapshot is None:
            raise store.CloudConnectionError('legacy_unbound')
        current = setCloudInventoryKeepTx(settings, connection, row, entryId, kept=kept,
            expectedRevision=expectedRevision, auditContext=auditContext)
        return _summaryTx(connection, current, snapshot)


def clearBoundCloudInventoryCache(settings, jobId, *, expectedRevision, confirmClear, accessToken, auditContext):
    from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCacheBatchTx
    from app.service.cloud_inventory_service import _limit
    _limit(expectedRevision, 0, 2**63 - 2)
    if confirmClear is not True:
        raise store.CloudConnectionError('confirmation_required')
    for _ in range(4):
        with store.connectionDb(settings, write=True) as connection:
            row, snapshot = _ownedJobTx(connection, jobId, accessToken=accessToken, auditContext=auditContext)
            if snapshot is None:
                raise store.CloudConnectionError('legacy_unbound')
            current = clearCloudInventoryCacheBatchTx(settings, connection, row,
                expectedRevision=expectedRevision, auditContext=auditContext)
            expectedRevision = current['revision']
            result = _summaryTx(connection, current, snapshot)
        if current['cache_state'] == 'cleared':
            break
    return result


def listBoundCloudInventories(settings, *, accessToken, auditContext, page=1, pageSize=20,
                             connectionId=None, directoryProfileId=None):
    if type(page) is not int or not 1 <= page <= 1000 or type(pageSize) is not int or not 1 <= pageSize <= 50:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'scan:run')
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        filters, values = ['j.actor_id=?'], [actorId]
        for column, value in (('connection_id', connectionId), ('profile_id', directoryProfileId)):
            if value is not None:
                store.cloudId(value)
                filters.append('c.' + column + '=?')
                values.append(value)
        # 计数与分页共用授权条件；归档仅保留历史，不按当前生命周期抹去来源。
        filters.append("(j.binding_state='legacy_unbound' OR (g.allowed=1 AND d.allowed=1))")
        joins = (' FROM cloud_drive_inventory_jobs j LEFT JOIN cloud_task_contexts c ON c.id=j.context_id '
            'LEFT JOIN cloud_connections current_connection ON current_connection.id=c.connection_id '
            'LEFT JOIN cloud_directory_profiles current_profile ON current_profile.id=c.profile_id '
            'LEFT JOIN cloud_connection_user_grants g ON g.connection_id=c.connection_id AND g.user_id=j.actor_id '
            'LEFT JOIN cloud_directory_user_grants d ON d.connection_id=c.connection_id AND d.profile_id=c.profile_id '
            'AND d.user_id=j.actor_id WHERE ' + ' AND '.join(filters))
        total = connection.execute('SELECT COUNT(*)' + joins, values).fetchone()[0]
        rows = connection.execute('SELECT j.id,j.root_path,j.status,j.reason,j.revision,j.binding_state,j.created_at,'
            'j.stored_entries,c.id AS context_id,c.connection_id,c.profile_id,c.connection_name_snapshot,c.profile_name_snapshot,'
            'current_connection.name AS current_connection_name,current_profile.name AS current_profile_name'
            + joins + ' ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?',
            (*values, pageSize, (page - 1) * pageSize)).fetchall()
        return {'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False,
            'items': [{'id': row['id'], 'approvedRoot': row['root_path'], 'status': row['status'], 'reason': row['reason'],
                'revision': row['revision'], 'storedEntries': row['stored_entries'], 'createdAt': row['created_at'],
                'bindingState': row['binding_state'], 'contextId': row['context_id'], 'source': None if row['context_id'] is None else {
                    'connectionId': row['connection_id'], 'directoryProfileId': row['profile_id'],
                    'connectionName': row['connection_name_snapshot'], 'directoryName': row['profile_name_snapshot'],
                    'currentConnectionName': row['current_connection_name'], 'currentDirectoryName': row['current_profile_name']}}
                for row in rows]}
