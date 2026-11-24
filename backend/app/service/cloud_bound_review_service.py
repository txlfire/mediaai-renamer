"""从原盘点派生本地人工复核；保存建议不授予隔离或删除能力。"""

from hashlib import sha256
import json

from app.service import cloud_ad_review_service as reviews
from app.service import cloud_connection_store as store
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_inventory_service import _limit
from app.service.cloud_task_context_service import (
    prepareDerivedCloudIntent, resolveCloudAuthSessionTx, _resolveTx, _view, _policyTx,
)


def _reviewViewTx(settings, connection, row, snapshot):
    if snapshot is None:
        return {**json.loads(row['snapshot_json']), 'id': row['id'], 'requestId': row['request_id'],
            'createdAt': row['created_at'], 'snapshotHash': sha256(row['snapshot_json'].encode()).hexdigest(),
            'bindingState': 'legacy_unbound', 'contextId': None, 'source': None, 'sourceCurrent': False,
            'staleReason': 'legacy_unbound', 'networkAccess': False, 'contentReadBytes': 0,
            'writeAvailable': False, 'executionAuthorized': False}
    result = reviews._result(connection, localCloudPlanSettingsTx(settings, connection, snapshot), row)
    try:
        _resolveTx(connection, snapshot.values['id'], snapshot.values['actor_id'], snapshot.values['auth_session_id'], 'send')
    except store.CloudConnectionError as exc:
        if result['sourceCurrent']:
            result |= {'sourceCurrent': False, 'staleReason': exc.code}
    return result | _view(snapshot) | {'bindingState': 'bound'}


def createBoundCloudAdReview(settings, *, requestId, jobId, expectedRevision, items, confirmLocalOnly, accessToken, auditContext):
    _limit(expectedRevision, 0, 2**63 - 2)
    normalized = reviews._items(items)
    if confirmLocalOnly is not True:
        raise store.CloudConnectionError('confirmation_required')
    payload = {'jobId': jobId, 'inventoryRevision': expectedRevision, 'items': normalized}
    def factory(connection, snapshot, resourceId, originalRequestId):
        reviews.insertCloudAdReviewTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection,
            reviewId=resourceId, requestId=originalRequestId, requestHash=store.intentHash(payload), jobId=jobId,
            expectedRevision=expectedRevision, normalized=normalized, now=snapshot.values['created_at'],
            auditContext=auditContext, contextId=snapshot.values['id'])
    return prepareDerivedCloudIntent(settings, jobId, requestId=requestId, action='ad_review', intentPayload=payload,
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: _reviewViewTx(settings, connection, row, snapshot))


def getBoundCloudAdReview(settings, recordId, *, accessToken, auditContext, byRequest=False):
    store.cloudId(recordId)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'scan:run')
        authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
        column = 'request_id' if byRequest else 'id'
        row = connection.execute(f'SELECT * FROM cloud_ad_reviews WHERE {column}=? AND actor_id=?', (recordId, actorId)).fetchone()
        if row is None:
            raise store.CloudConnectionError('resource_not_found')
        snapshot = None if row['binding_state'] == 'legacy_unbound' else _resolveTx(connection, row['context_id'], actorId, authSessionId, 'history')
        if snapshot is not None and (snapshot.resourceKind != 'ad_review' or snapshot.resourceId != row['id']):
            raise store.CloudConnectionError('storage_unavailable')
        return _reviewViewTx(settings, connection, row, snapshot)


def listBoundCloudAdReviews(settings, *, accessToken, auditContext, page=1, pageSize=20,
                           connectionId=None, directoryProfileId=None):
    _limit(page, 1, 1000)
    _limit(pageSize, 1, 50)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'scan:run')
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        filters, values = ['r.actor_id=?', "(r.binding_state='legacy_unbound' OR (g.allowed=1 AND d.allowed=1))"], [actorId]
        if directoryProfileId is not None and connectionId is None:
            raise store.CloudConnectionError('invalid_scope')
        for column, value in (('connection_id', connectionId), ('profile_id', directoryProfileId)):
            if value is not None:
                store.cloudId(value)
                filters.append('c.' + column + '=?')
                values.append(value)
        joins = (' FROM cloud_ad_reviews r LEFT JOIN cloud_task_contexts c ON c.id=r.context_id '
            'LEFT JOIN cloud_connection_user_grants g ON g.connection_id=c.connection_id AND g.user_id=r.actor_id '
            'LEFT JOIN cloud_directory_user_grants d ON d.connection_id=c.connection_id AND d.profile_id=c.profile_id AND d.user_id=r.actor_id ')
        where = ' WHERE ' + ' AND '.join(filters)
        total = connection.execute('SELECT COUNT(*)' + joins + where, values).fetchone()[0]
        rows = connection.execute('SELECT r.id,r.request_id,r.job_id,r.created_at,r.item_count,r.binding_state,r.context_id,'
            'r.inventory_revision,r.path_epoch,r.config_hash,r.rule_version,j.revision AS job_revision,j.cache_state,'
            'c.selected_path,c.policy_hash,c.connection_grant_revision,c.directory_grant_revision,'
            'g.revision AS current_grant_revision,d.revision AS current_directory_grant_revision,'
            'c.connection_id,c.profile_id,c.connection_name_snapshot,c.profile_name_snapshot,'
            'current_connection.name AS current_connection_name,current_profile.name AS current_profile_name' + joins +
            'LEFT JOIN cloud_connections current_connection ON current_connection.id=c.connection_id '
            'LEFT JOIN cloud_directory_profiles current_profile ON current_profile.id=c.profile_id '
            'LEFT JOIN cloud_drive_inventory_jobs j ON j.id=r.job_id AND j.actor_id=r.actor_id' + where +
            ' ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?', (*values, pageSize, (page - 1) * pageSize)).fetchall()
        # 只核对有界元数据；同来源政策在当前读事务内复用，不载入复核正文。
        policies = {}
        epoch = reviews.cloudPathEpoch(connection)['revision']
        def current(row):
            if row['binding_state'] != 'bound':
                return False
            key = (row['connection_id'], row['profile_id'], row['selected_path'])
            if key not in policies:
                try:
                    policies[key] = _policyTx(connection, actorId, *key, 'ad_review')
                except store.CloudConnectionError as exc:
                    if exc.code not in {'source_disabled', 'resource_not_found'}:
                        raise
                    policies[key] = None
            policy = policies[key]
            return bool(policy and row['cache_state'] == 'available' and row['job_revision'] == row['inventory_revision']
                and row['path_epoch'] == epoch and row['rule_version'] == reviews.RULE_VERSION
                and row['policy_hash'] == policy['policyHash']
                and row['connection_grant_revision'] == row['current_grant_revision']
                and row['directory_grant_revision'] == row['current_directory_grant_revision']
                and row['config_hash'] == reviews._digest([policy['connectionVersion']['endpoint'],
                    policy['directoryVersion']['approved_root'], policy['directoryVersion']['mapping_key']]))
        return {'items': [{'id': row['id'], 'requestId': row['request_id'], 'jobId': row['job_id'], 'createdAt': row['created_at'],
            'itemCount': row['item_count'], 'sourceCurrent': current(row), 'bindingState': row['binding_state'], 'contextId': row['context_id'],
            'source': None if row['context_id'] is None else {'connectionId': row['connection_id'], 'directoryProfileId': row['profile_id'],
                'connectionName': row['connection_name_snapshot'], 'directoryName': row['profile_name_snapshot'],
                'currentConnectionName': row['current_connection_name'], 'currentDirectoryName': row['current_profile_name']}}
            for row in rows], 'page': page, 'pageSize': pageSize, 'total': total, 'networkAccess': False, 'writeAvailable': False}
