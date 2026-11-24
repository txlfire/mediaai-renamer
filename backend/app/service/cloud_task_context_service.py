"""不可变来源、当前登录会话及原子意图；此模块不建立网络连接。"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re
import sqlite3
import time
from types import MappingProxyType
from uuid import uuid4

from app.service import cloud_connection_store as store
from app.service.auth_service import _hash_token
from app.service.cloud_connection_grant_service import _scope
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.utils.cloud_drive_paths import approvedCloudRoot, isWithinCloudRoot

INVENTORY_LIMITS = {'rpc': 4, 'seconds': 30, 'entries': 100, 'bytes': 1048576, 'directories': 1, 'depth': 0}
DIAGNOSTIC_LIMITS = {'rpc': 2, 'seconds': 30, 'bytes': 65536}
ORDINARY_CONTEXT_LIMIT = 49000
RECOVERY_CONTEXT_LIMIT = 50000
_REVISION_KEYS = ('connectionSecurityRevision', 'directorySecurityRevision', 'connectionStateRevision',
                  'directoryStateRevision', 'grantRevision', 'directoryGrantRevision')
_KINDS = {'inventory': ('inventory', None), 'diagnostic_tls': ('diagnostic', 'tls'),
          'diagnostic_token_runtime': ('diagnostic', 'token-runtime'),
          'directory_plan': ('directory_plan', None), 'ad_review': ('ad_review', None),
          'directory_execute': ('directory_execute', None), 'directory_observe': ('directory_observe', None),
          'directory_reverse': ('directory_reverse', None), 'quarantine_plan': ('quarantine_plan', None),
          'quarantine_move': ('quarantine_move', None), 'quarantine_observe': ('quarantine_observe', None),
          'quarantine_reverse': ('quarantine_reverse', None)}
_LOCAL_DERIVED = {'directory_plan': ('cloud_directory_plans', 'inventory_job_id', 'cloud-directory:plan'),
                  'ad_review': ('cloud_ad_reviews', 'job_id', 'scan:run')}
_DERIVED_KINDS = set(_LOCAL_DERIVED) | {'directory_execute', 'directory_observe', 'directory_reverse', 'quarantine_plan', 'quarantine_move', 'quarantine_observe', 'quarantine_reverse'}
_DERIVED_PARENTS = {'directory_execute': ('cloud_directory_plans', 'id', ('directory_plan', 'directory_reverse')),
                    'directory_observe': ('cloud_directory_executions', 'plan_id', ('directory_execute',)),
                    'directory_reverse': ('cloud_directory_executions', 'plan_id', ('directory_execute',)),
                    'quarantine_plan': ('cloud_ad_reviews', 'id', ('ad_review',)),
                    'quarantine_move': ('cloud_quarantine_plans', 'id', ('quarantine_plan', 'quarantine_reverse')),
                    'quarantine_observe': ('cloud_quarantine_runs', 'id', ('quarantine_move',)),
                    'quarantine_reverse': ('cloud_quarantine_runs', 'id', ('quarantine_move',))}
_LEGACY_TABLES = ('cloud_drive_read_sessions', 'cloud_drive_inventory_jobs', 'cloud_directory_plans',
                  'cloud_directory_executions', 'cloud_directory_recoveries', 'cloud_quarantine_plans',
                  'cloud_quarantine_runs', 'cloud_ad_reviews')


@dataclass(frozen=True)
class CloudContextSnapshot:
    values: object = field(repr=False)
    connectionVersion: object = field(repr=False)
    directoryVersion: object = field(repr=False)
    resourceKind: str
    resourceId: str
    requestId: str
    currentNames: tuple[str, str] = field(repr=False)


def resolveCloudAuthSessionTx(connection, actorId, accessToken):
    if (not isinstance(accessToken, str) or not 1 <= len(accessToken) <= 8192
            or any(not 33 <= ord(char) <= 126 for char in accessToken)):
        raise store.CloudConnectionError('authentication_required')
    now = time.time()
    if not math.isfinite(now) or now <= 0:
        raise store.CloudConnectionError('authentication_required')
    row = connection.execute('SELECT s.id FROM user_sessions s JOIN users u ON u.id=s.user_id '
        'WHERE s.token_hash=? AND s.user_id=? AND u.enabled=1 AND s.revoked_at IS NULL '
        "AND julianday(s.expires_at)>julianday(?,'unixepoch') AND julianday(s.created_at)<=julianday(?,'unixepoch')",
        (_hash_token(accessToken), actorId, now, now)).fetchone()
    if row is None:
        raise store.CloudConnectionError('authentication_required')
    return row['id']


def requireCloudAuthSessionIdTx(connection, actorId, sessionId):
    """worker只持原会话编号，不交接MediaAI Bearer。缺行与过期均拒绝。"""
    now = time.time()
    if type(sessionId) is not int or sessionId <= 0 or not math.isfinite(now) or now <= 0:
        raise store.CloudConnectionError('authentication_required')
    row = connection.execute('SELECT 1 FROM user_sessions s JOIN users u ON u.id=s.user_id '
        'WHERE s.id=? AND s.user_id=? AND u.enabled=1 AND s.revoked_at IS NULL '
        "AND julianday(s.expires_at)>julianday(?,'unixepoch') AND julianday(s.created_at)<=julianday(?,'unixepoch')",
        (sessionId, actorId, now, now)).fetchone()
    if row is None:
        raise store.CloudConnectionError('authentication_required')


def _kind(action, stage):
    for kind, pair in _KINDS.items():
        if pair == (action, stage):
            return kind
    raise store.CloudConnectionError('invalid_fields')


def _requireContextActorTx(connection, auditContext, kind):
    permissions = ('cloud-directory:plan', 'cloud-directory:execute') if kind in {'directory_execute', 'directory_observe'} else \
        ('cloud-directory:plan',) if kind == 'directory_reverse' else \
        ('scan:run', 'cloud-quarantine:plan', 'cloud-quarantine:execute') if kind in {'quarantine_move', 'quarantine_observe'} else \
        ('scan:run', 'cloud-quarantine:plan') if kind in {'quarantine_plan', 'quarantine_reverse'} else \
        (_LOCAL_DERIVED[kind][2],) if kind in _LOCAL_DERIVED else ('scan:run',)
    for permission in permissions:
        actorId = store.requireActor(connection, auditContext, permission)
    return actorId


def _path(value):
    try:
        return approvedCloudRoot(value)
    except (ValueError, TypeError):
        raise store.CloudConnectionError('invalid_scope') from None


def _policyTx(connection, actorId, connectionId, profileId, path, kind):
    action, stage = _KINDS[kind]
    revisions = _scope(connection, connectionId, profileId, actorId, allowDisabled=action in {'diagnostic', 'directory_observe', 'quarantine_observe'})
    versions = []
    for table, key, recordId, revision in (
        ('cloud_connection_versions', 'connection_id', connectionId, revisions['connectionSecurityRevision']),
        ('cloud_directory_profile_versions', 'profile_id', profileId, revisions['directorySecurityRevision'])):
        row = connection.execute(f'SELECT * FROM {table} WHERE {key}=? AND revision=?', (recordId, revision)).fetchone()
        if row is None:
            raise store.CloudConnectionError('storage_unavailable')
        versions.append(dict(row))
    connectionVersion, directoryVersion = versions
    if not isWithinCloudRoot(path, directoryVersion['approved_root']):
        raise store.CloudConnectionError('invalid_scope')
    source = connection.execute('SELECT * FROM cloud_source_namespaces WHERE id=?', (directoryVersion['source_namespace_id'],)).fetchone()
    globalPolicy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    if source is None or globalPolicy is None:
        raise store.CloudConnectionError('storage_unavailable')
    names = connection.execute('SELECT c.name,p.name FROM cloud_connections c JOIN cloud_directory_profiles p '
        'ON p.connection_id=c.id WHERE c.id=? AND p.id=?', (connectionId, profileId)).fetchone()
    limits = dict(INVENTORY_LIMITS if action == 'inventory' else DIAGNOSTIC_LIMITS if action == 'diagnostic' else
                  {'items': 5, 'rpc': 38, 'seconds': 180, 'bytes': 1048576} if action == 'directory_execute' else
                  {'items': 5, 'rpc': 48, 'seconds': 180, 'bytes': 1048576} if action == 'quarantine_move' else
                  {'items': 5, 'rpc': 16, 'seconds': 90, 'bytes': 1048576} if action == 'quarantine_observe' else
                  {'items': 5, 'rpc': 14, 'seconds': 90, 'bytes': 1048576} if action == 'directory_observe' else
                  {'items': 5, 'seconds': 1800, 'contentBytes': 0} if action in {'directory_plan', 'directory_reverse'} else
                  {'items': 5, 'seconds': 1800, 'bytes': 65536, 'contentBytes': 0} if action in {'quarantine_plan', 'quarantine_reverse'} else
                  {'items': 100, 'bytes': 524288, 'contentBytes': 0})
    # 授权修订单独比较；名称不进入执行政策，旧全局路径世代不能遗漏。
    policyHash = store.intentHash({'contractVersion': 1, 'purpose': 'cloud-policy', 'kind': kind,
        'connectionId': connectionId, 'profileId': profileId, 'path': path, 'limits': limits,
        'security': [revisions[key] for key in _REVISION_KEYS[:4]], 'globalPolicyRevision': globalPolicy['revision'],
        'pathEpoch': cloudPathEpoch(connection)['revision'], 'source': [source['id'], source['revision'], source['path_epoch']]})
    identityHash = store.intentHash({'contractVersion': 1, 'purpose': 'cloud-identity',
        'target': connectionVersion['target_key'], 'trust': connectionVersion['trust_hash'],
        'root': directoryVersion['approved_root'], 'mapping': directoryVersion['mapping_key'],
        'source': [source['id'], source['revision']], 'path': path, 'rules': 'metadata-v1'})
    available = bool(directoryVersion['inventory_enabled']) if action == 'inventory' else \
        bool(directoryVersion['rename_enabled']) if action == 'directory_execute' else \
        bool(directoryVersion['quarantine_enabled']) if action == 'quarantine_move' else True
    return {'connectionId': connectionId, 'directoryProfileId': profileId, 'selectedPath': path,
        'revisions': {key: revisions[key] for key in _REVISION_KEYS}, 'policyHash': policyHash,
        'identityHash': identityHash, 'limits': limits, 'kind': kind, 'action': action, 'stage': stage,
        'source': dict(source), 'connectionVersion': connectionVersion, 'directoryVersion': directoryVersion,
        'names': tuple(names), 'available': available, 'reasonCode': None if available else
        'directory_execution_disabled' if action == 'directory_execute' else
        'quarantine_execution_disabled' if action == 'quarantine_move' else 'web_execution_disabled'}


def _requireReadyPool(connection):
    pool = connection.execute('SELECT state,cooldown_until FROM cloud_drive_budget_pool WHERE id=1').fetchone()
    if pool is not None and (pool['state'] != 'ready' or pool['cooldown_until'] > time.time()):
        raise store.CloudConnectionError('global_protection_held')


def _requireDirectoryRecoveryWindowTx(connection, planId):
    """只豁免本次原目录冻结；其他活动、未知和人工暂停仍阻止新核对。"""
    original = connection.execute('SELECT slot_held,worker_pid,status FROM cloud_directory_executions WHERE plan_id=?', (planId,)).fetchone()
    if original is None or not original['slot_held'] or original['worker_pid'] is not None or original['status'] in {'accepted', 'running'}:
        raise store.CloudConnectionError('global_protection_held')
    checks = (
        ("SELECT 1 FROM cloud_directory_executions WHERE plan_id<>? AND (slot_held=1 OR worker_pid IS NOT NULL) LIMIT 1", (planId,)),
        ("SELECT 1 FROM cloud_directory_rpc_intents WHERE plan_id<>? AND phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL LIMIT 1", (planId,)),
        ("SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1", ()),
        ("SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running') LIMIT 1", ()),
        ("SELECT 1 FROM cloud_connection_diagnostics WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('accepted','running','review') LIMIT 1", ()),
        ('SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1', ()),
        ('SELECT 1 FROM cloud_directory_probe_leases LIMIT 1', ()),
        ('SELECT 1 FROM cloud_quarantine_runs WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1', ()),
        ("SELECT 1 FROM cloud_quarantine_rpcs WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL LIMIT 1", ()),
    )
    if any(connection.execute(query, args).fetchone() for query, args in checks):
        raise store.CloudConnectionError('global_protection_held')
    pool = connection.execute('SELECT state,pause_reason,cooldown_until FROM cloud_drive_budget_pool WHERE id=1').fetchone()
    if pool is not None and (pool['cooldown_until'] > time.time()
            or pool['state'] != 'ready' and pool['pause_reason'] != 'request_incomplete'):
        raise store.CloudConnectionError('global_protection_held')


def _requireQuarantineRecoveryWindowTx(connection, runId):
    """豁免原动作与已退出的同源只读核对；不释放未知，也不放行其他来源。"""
    from app.service.cloud_drive_budget_service import cloudDriveHostKey
    original = connection.execute('SELECT kind,slot_held,worker_pid,status FROM cloud_quarantine_runs WHERE id=?', (runId,)).fetchone()
    if (original is None or original['kind'] != 'move' or not original['slot_held']
            or original['worker_pid'] is not None or original['status'] in {'accepted', 'running'}):
        raise store.CloudConnectionError('global_protection_held')
    checks = (
        ('SELECT 1 FROM cloud_quarantine_runs WHERE id<>? AND (slot_held=1 OR worker_pid IS NOT NULL) LIMIT 1', (runId,)),
        ("SELECT 1 FROM cloud_quarantine_rpcs q WHERE q.run_id<>? AND q.phase IN ('sending','unknown') "
         "AND q.reviewed_by_run IS NULL AND NOT (q.method IN ('GetApiTokenInfo','GetRuntimeInfo','FindFileByPath') "
         "AND EXISTS(SELECT 1 FROM cloud_quarantine_runs r WHERE r.id=q.run_id AND r.kind='observe' "
         "AND r.original_run_id=? AND r.owner_host=? AND r.slot_held=0 AND r.worker_pid IS NULL "
         "AND r.status NOT IN ('accepted','running'))) LIMIT 1", (runId, runId, cloudDriveHostKey())),
        ('SELECT 1 FROM cloud_directory_executions WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1', ()),
        ("SELECT 1 FROM cloud_directory_rpc_intents WHERE phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL LIMIT 1", ()),
        ('SELECT 1 FROM cloud_directory_recoveries WHERE slot_held=1 OR worker_pid IS NOT NULL LIMIT 1', ()),
        ("SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running') LIMIT 1", ()),
        ("SELECT 1 FROM cloud_connection_diagnostics WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('accepted','running','review') LIMIT 1", ()),
        ('SELECT 1 FROM cloud_drive_rpc_lease LIMIT 1', ()),
        ('SELECT 1 FROM cloud_directory_probe_leases LIMIT 1', ()),
    )
    if any(connection.execute(query, args).fetchone() for query, args in checks):
        raise store.CloudConnectionError('global_protection_held')
    pool = connection.execute('SELECT state,pause_reason,cooldown_until FROM cloud_drive_budget_pool WHERE id=1').fetchone()
    if pool is not None and (pool['cooldown_until'] > time.time()
            or pool['state'] != 'ready' and pool['pause_reason'] != 'request_incomplete'):
        raise store.CloudConnectionError('global_protection_held')


def cloudContextPolicy(settings, *, connectionId, directoryProfileId, selectedPath, accessToken, auditContext,
                       action='inventory', stage=None):
    kind, path = _kind(action, stage), _path(selectedPath)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, 'scan:run')
        resolveCloudAuthSessionTx(connection, actorId, accessToken)
        policy = _policyTx(connection, actorId, connectionId, directoryProfileId, path, kind)
        try:
            store.requireIdle(connection)
            _requireReadyPool(connection)
        except store.CloudConnectionError as exc:
            policy = {**policy, 'available': False, 'reasonCode': exc.code}
        return {key: policy[key] for key in ('connectionId', 'directoryProfileId', 'selectedPath', 'revisions',
                                           'policyHash', 'limits', 'available', 'reasonCode')} | {'networkAccess': False}


def _currentContentGrant(connection, actorId, context):
    if context['actor_id'] != actorId:
        raise store.CloudConnectionError('resource_not_found')
    # 历史可以归档或依据过期；内容权限必须是当前仍具备的明确连接/目录授权。
    row = connection.execute('SELECT 1 FROM cloud_connection_user_grants g JOIN cloud_directory_user_grants d '
        'ON d.connection_id=g.connection_id AND d.user_id=g.user_id WHERE g.connection_id=? AND g.user_id=? '
        'AND g.allowed=1 AND d.profile_id=? AND d.allowed=1', (context['connection_id'], actorId, context['profile_id'])).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')


def _loadContextTx(connection, contextId):
    store.cloudId(contextId)
    row = connection.execute('SELECT * FROM cloud_task_contexts WHERE id=?', (contextId,)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    requests = connection.execute('SELECT * FROM cloud_request_registry WHERE context_id=? LIMIT 2', (contextId,)).fetchall()
    if len(requests) != 1 or requests[0]['resource_kind'] not in _KINDS:
        raise store.CloudConnectionError('storage_unavailable')
    request = requests[0]
    if request['actor_id'] != row['actor_id'] or _KINDS[request['resource_kind']][0] != row['action']:
        raise store.CloudConnectionError('storage_unavailable')
    connectionVersion = connection.execute('SELECT * FROM cloud_connection_versions WHERE connection_id=? AND revision=?',
        (row['connection_id'], row['connection_security_revision'])).fetchone()
    directoryVersion = connection.execute('SELECT * FROM cloud_directory_profile_versions WHERE profile_id=? AND revision=?',
        (row['profile_id'], row['profile_security_revision'])).fetchone()
    if connectionVersion is None or directoryVersion is None:
        raise store.CloudConnectionError('storage_unavailable')
    names = connection.execute('SELECT c.name,p.name FROM cloud_connections c JOIN cloud_directory_profiles p '
        'ON p.connection_id=c.id WHERE c.id=? AND p.id=?', (row['connection_id'], row['profile_id'])).fetchone()
    if names is None:
        raise store.CloudConnectionError('storage_unavailable')
    return CloudContextSnapshot(MappingProxyType(dict(row)), MappingProxyType(dict(connectionVersion)),
        MappingProxyType(dict(directoryVersion)), request['resource_kind'], request['resource_id'], request['request_id'], tuple(names))


def _checkResourceTx(connection, snapshot, *, _verifyParent=True):
    context = snapshot.values
    if snapshot.resourceKind == 'quarantine_reverse':
        row = connection.execute('SELECT * FROM cloud_quarantine_plans WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['kind'] != 'reverse'
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'quarantine_move' or parent.resourceId != row['origin_run_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        if _verifyParent:
            _checkResourceTx(connection, parent, _verifyParent=False)
        try:
            raw = row['snapshot_json'].encode('utf-8')
            if len(raw) > 65536:
                raise ValueError
            data = json.loads(raw)
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or data['rootPath'] != context['selected_path'] or data['parentPath'] != context['selected_path']
                    or data['originRunId'] != parent.resourceId or data['pathEpoch'] != row['basis_epoch']
                    or data['inventoryJobId'] != row['job_id'] or data['reviewId'] != row['review_id']
                    or not 1 <= len(data['items']) == row['item_count'] <= 5
                    or row['snapshot_hash'] != sha256(raw).hexdigest() or row['logical_bytes'] != len(raw)
                    or row['created_at'] != context['created_at'] or row['expires_at'] - row['created_at'] != 1800):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise store.CloudConnectionError('storage_unavailable') from None
        return row
    if snapshot.resourceKind == 'quarantine_observe':
        row = connection.execute('SELECT * FROM cloud_quarantine_runs WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['kind'] != 'observe'
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'quarantine_move' or parent.resourceId != row['original_run_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        original = _checkResourceTx(connection, parent, _verifyParent=False) if _verifyParent else connection.execute(
            'SELECT * FROM cloud_quarantine_runs WHERE id=?', (parent.resourceId,)).fetchone()
        plan = connection.execute('SELECT * FROM cloud_quarantine_plans WHERE id=?', (row['plan_id'],)).fetchone()
        if (original is None or plan is None or not 1 <= plan['item_count'] <= 5
                or row['plan_id'] != original['plan_id'] or row['policy_hash'] != original['policy_hash']
                or row['max_rpc'] != 6 + 2 * plan['item_count'] or row['max_bytes'] != 1048576
                or row['expires_at'] - row['created_at'] != 90 or row['created_at'] != context['created_at']):
            raise store.CloudConnectionError('storage_unavailable')
        return row
    if snapshot.resourceKind == 'quarantine_move':
        row = connection.execute('SELECT * FROM cloud_quarantine_runs WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['kind'] != 'move'
                or row['original_run_id'] is not None or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind not in {'quarantine_plan', 'quarantine_reverse'} or parent.resourceId != row['plan_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        plan = _checkResourceTx(connection, parent, _verifyParent=False) if _verifyParent else connection.execute(
            'SELECT * FROM cloud_quarantine_plans WHERE id=?', (parent.resourceId,)).fetchone()
        if (plan is None or row['policy_hash'] != plan['policy_hash'] or row['max_rpc'] != 3 + 9 * plan['item_count']
                or row['max_bytes'] != 1048576 or not 0 < row['expires_at'] - row['created_at'] <= 180
                or row['created_at'] != context['created_at'] or row['expires_at'] > plan['expires_at']):
            raise store.CloudConnectionError('storage_unavailable')
        return row
    if snapshot.resourceKind == 'quarantine_plan':
        row = connection.execute('SELECT * FROM cloud_quarantine_plans WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['kind'] != 'forward'
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'ad_review' or parent.resourceId != row['review_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        if _verifyParent:
            review = _checkResourceTx(connection, parent, _verifyParent=False)
        else:
            review = connection.execute('SELECT * FROM cloud_ad_reviews WHERE id=?', (parent.resourceId,)).fetchone()
        try:
            raw = row['snapshot_json'].encode('utf-8')
            if len(raw) > 65536:
                raise ValueError
            data = json.loads(raw)
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or review is None or row['job_id'] != review['job_id'] or data['inventoryJobId'] != row['job_id']
                    or data['reviewSnapshotHash'] != sha256(review['snapshot_json'].encode()).hexdigest()
                    or data['rootPath'] != context['selected_path'] or data['parentPath'] != context['selected_path']
                    or data['reviewId'] != parent.resourceId or not 1 <= len(data['items']) == row['item_count'] <= 5
                    or row['snapshot_hash'] != sha256(raw).hexdigest() or row['logical_bytes'] != len(raw)
                    or row['created_at'] != context['created_at'] or row['expires_at'] - row['created_at'] != 1800):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise store.CloudConnectionError('storage_unavailable') from None
        return row
    if snapshot.resourceKind == 'directory_reverse':
        row = connection.execute('SELECT * FROM cloud_directory_plans WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['kind'] != 'reverse'
                or row['root_path'] != context['selected_path'] or row['parent_path'] != context['selected_path']
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'directory_execute' or parent.resourceId != row['origin_plan_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        if _verifyParent:
            _checkResourceTx(connection, parent, _verifyParent=False)
        return row
    if snapshot.resourceKind == 'directory_observe':
        row = connection.execute('SELECT * FROM cloud_directory_recoveries WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id']
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'directory_execute' or parent.resourceId != row['plan_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        original = _checkResourceTx(connection, parent, _verifyParent=False) if _verifyParent else connection.execute(
            'SELECT * FROM cloud_directory_executions WHERE plan_id=?', (parent.resourceId,)).fetchone()
        if original is None:
            raise store.CloudConnectionError('storage_unavailable')
        count = connection.execute('SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?', (row['plan_id'],)).fetchone()[0]
        if (not 1 <= count <= 5 or row['policy_hash'] != original['policy_hash'] or row['max_rpc'] != 4 + 2 * count
                or row['max_bytes'] != 1048576 or row['expires_at'] - row['created_at'] != 90
                or row['created_at'] != context['created_at']):
            raise store.CloudConnectionError('storage_unavailable')
        return row
    if snapshot.resourceKind == 'directory_execute':
        row = connection.execute('SELECT * FROM cloud_directory_executions WHERE plan_id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id']
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind not in {'directory_plan', 'directory_reverse'} or parent.resourceId != row['plan_id']
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        plan = _checkResourceTx(connection, parent, _verifyParent=False) if _verifyParent else connection.execute(
            'SELECT * FROM cloud_directory_plans WHERE id=?', (parent.resourceId,)).fetchone()
        if plan is None:
            raise store.CloudConnectionError('storage_unavailable')
        count = connection.execute('SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?', (row['plan_id'],)).fetchone()[0]
        if (not 1 <= count <= 5 or row['policy_hash'] != plan['policy_hash'] or row['max_rpc'] != 3 + 7 * count
                or row['max_bytes'] != 1048576 or not 0 < row['expires_at'] - row['created_at'] <= 180
                or row['created_at'] != context['created_at'] or row['expires_at'] > plan['expires_at']):
            raise store.CloudConnectionError('storage_unavailable')
        return row
    if snapshot.resourceKind in _LOCAL_DERIVED:
        table, parentColumn, _ = _LOCAL_DERIVED[snapshot.resourceKind]
        row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (snapshot.resourceId,)).fetchone()
        request = connection.execute('SELECT parent_context_id FROM cloud_request_registry WHERE request_id=?', (snapshot.requestId,)).fetchone()
        if (row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId
                or row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id']
                or request is None or request['parent_context_id'] is None):
            raise store.CloudConnectionError('storage_unavailable')
        parent = _loadContextTx(connection, request['parent_context_id'])
        if (parent.resourceKind != 'inventory' or row[parentColumn] != parent.resourceId
                or any(context[key] != parent.values[key] for key in
                    ('connection_id', 'profile_id', 'selected_path', 'recovery_identity_hash', 'actor_id'))):
            raise store.CloudConnectionError('storage_unavailable')
        if snapshot.resourceKind == 'directory_plan' and (row['kind'] != 'inventory'
                or row['root_path'] != context['selected_path'] or row['parent_path'] != context['selected_path']):
            raise store.CloudConnectionError('storage_unavailable')
        if _verifyParent:
            _checkResourceTx(connection, parent, _verifyParent=False)
        return row
    table = 'cloud_drive_inventory_jobs' if snapshot.resourceKind == 'inventory' else 'cloud_connection_diagnostics'
    row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (snapshot.resourceId,)).fetchone()
    if row is None or row['context_id'] != context['id'] or row['request_id'] != snapshot.requestId:
        raise store.CloudConnectionError('storage_unavailable')
    session = connection.execute('SELECT * FROM cloud_drive_read_sessions WHERE id=?', (row['session_id'],)).fetchone()
    limits = INVENTORY_LIMITS if snapshot.resourceKind == 'inventory' else DIAGNOSTIC_LIMITS
    if (session is None or session['context_id'] != context['id'] or session['actor_id'] != context['actor_id']
            or session['root_path'] != context['selected_path'] or session['request_id'] != snapshot.requestId
            or session['endpoint_hash'] != sha256(snapshot.connectionVersion['target_key'].encode()).hexdigest()
            or session['max_rpc'] != limits['rpc'] or session['expires_at'] - session['created_at'] != limits['seconds']):
        raise store.CloudConnectionError('storage_unavailable')
    if snapshot.resourceKind == 'inventory':
        if (row['binding_state'] != 'bound' or row['actor_id'] != context['actor_id'] or row['root_path'] != context['selected_path']
                or row['mode'] != 'overview' or row['exclusions_json'] != '[]' or row['max_entries'] != 100
                or row['max_bytes'] != 1048576 or row['max_directories'] != 1 or row['max_depth'] != 0
                or row['quantum_directories'] != 1):
            raise store.CloudConnectionError('storage_unavailable')
        try:
            selected = json.loads(row['selected_json'])
        except (ValueError, TypeError):
            raise store.CloudConnectionError('storage_unavailable') from None
        if selected != [context['selected_path']]:
            raise store.CloudConnectionError('storage_unavailable')
    elif (row['created_at'] != session['created_at'] or row['expires_at'] != session['expires_at']
          or row['used_rpc'] != session['used_rpc']):
        raise store.CloudConnectionError('storage_unavailable')
    return row


def _view(snapshot, *, replayed=False, resource=None):
    row = snapshot.values
    result = {'contextId': row['id'], 'requestId': snapshot.requestId, 'resourceId': snapshot.resourceId,
        'resourceKind': snapshot.resourceKind, 'replayed': replayed, 'networkAccess': False,
        'source': {'connectionId': row['connection_id'], 'directoryProfileId': row['profile_id'],
            'connectionName': row['connection_name_snapshot'], 'directoryName': row['profile_name_snapshot'],
            'currentConnectionName': snapshot.currentNames[0], 'currentDirectoryName': snapshot.currentNames[1],
            'selectedPath': row['selected_path']}}
    if resource is not None:
        result |= {'id': resource['plan_id'] if snapshot.resourceKind == 'directory_execute' else resource['id'],
                   'status': 'saved' if snapshot.resourceKind == 'ad_review' else resource['status'],
                   'revision': 0 if snapshot.resourceKind == 'ad_review' else resource['revision']}
        if snapshot.resourceKind == 'inventory':
            result |= {'jobId': resource['id'], 'limits': dict(INVENTORY_LIMITS), 'bindingState': 'bound',
                       'executionAvailable': False, 'writeAvailable': False}
    return result


def _expected(value):
    if not isinstance(value, dict) or set(value) != set(_REVISION_KEYS):
        raise store.CloudConnectionError('invalid_fields')
    return {key: store.revisionValue(value[key]) for key in _REVISION_KEYS}


def _legacyCollision(connection, requestId, actorId):
    for table in _LEGACY_TABLES:
        row = connection.execute(f'SELECT actor_id FROM {table} WHERE request_id=?', (requestId,)).fetchone()
        if row is not None:
            raise store.CloudConnectionError('legacy_unbound' if row['actor_id'] == actorId else 'resource_not_found')


def prepareCloudIntent(settings, *, requestId, connectionId, directoryProfileId, selectedPath, expected, policyHash,
                       accessToken, auditContext, resourceFactory, action='inventory', stage=None):
    with store.connectionDb(settings, write=True) as connection:
        return _prepareCloudIntentTx(settings, connection, requestId=requestId, connectionId=connectionId,
            directoryProfileId=directoryProfileId, selectedPath=selectedPath, expected=expected, policyHash=policyHash,
            accessToken=accessToken, auditContext=auditContext, resourceFactory=resourceFactory, action=action, stage=stage)


def _prepareCloudIntentTx(settings, connection, *, requestId, connectionId, directoryProfileId, selectedPath, expected, policyHash,
                          accessToken, auditContext, resourceFactory, action='inventory', stage=None,
                          parentContextId=None, intentPayload=None):
    store.cloudId(requestId)
    kind, path, expected = _kind(action, stage), _path(selectedPath), _expected(expected)
    if not isinstance(policyHash, str) or not re.fullmatch('[0-9a-f]{64}', policyHash) or not callable(resourceFactory):
        raise store.CloudConnectionError('invalid_fields')
    actorId = _requireContextActorTx(connection, auditContext, kind)
    authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    intent = {'contractVersion': 1, 'purpose': 'cloud-intent', 'actorId': actorId,
        'authSessionId': authSessionId, 'connectionId': connectionId, 'profileId': directoryProfileId,
        'path': path, 'revisions': expected, 'policyHash': policyHash, 'kind': kind}
    if kind in _DERIVED_KINDS:
        if parentContextId is None:
            raise store.CloudConnectionError('invalid_scope')
        parent = _resolveTx(connection, parentContextId, actorId, authSessionId, 'history')
        if parent.resourceKind not in _DERIVED_PARENTS.get(kind, (None, None, ('inventory',)))[2] or any(parent.values[key] != value for key, value in
                (('connection_id', connectionId), ('profile_id', directoryProfileId), ('selected_path', path))):
            raise store.CloudConnectionError('invalid_scope')
        intent |= {'parentContextId': parentContextId, 'payload': intentPayload}
    elif parentContextId is not None or intentPayload is not None:
        raise store.CloudConnectionError('invalid_scope')
    fingerprint = store.intentHash(intent)
    existing = connection.execute('SELECT * FROM cloud_request_registry WHERE request_id=?', (requestId,)).fetchone()
    if existing is not None:
        snapshot = _loadContextTx(connection, existing['context_id'])
        _currentContentGrant(connection, actorId, snapshot.values)
        if existing['intent_hash'] != fingerprint:
            raise store.CloudConnectionError('request_context_conflict')
        resource = _checkResourceTx(connection, snapshot)
        return _view(snapshot, replayed=True, resource=resource)
    _legacyCollision(connection, requestId, actorId)
    policy = _policyTx(connection, actorId, connectionId, directoryProfileId, path, kind)
    if policy['revisions'] != expected or policy['policyHash'] != policyHash:
        raise store.CloudConnectionError('context_stale')
    if kind in _DERIVED_KINDS and policy['identityHash'] != parent.values['recovery_identity_hash']:
        raise store.CloudConnectionError('context_stale')
    if not policy['available']:
        raise store.CloudConnectionError(policy['reasonCode'])
    contextLimit = RECOVERY_CONTEXT_LIMIT if kind in {'directory_observe', 'quarantine_observe'} else ORDINARY_CONTEXT_LIMIT
    for table in ('cloud_task_contexts', 'cloud_request_registry'):
        if connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] >= contextLimit:
            raise store.CloudConnectionError('capacity_reached')
    if action == 'diagnostic' and connection.execute('SELECT COUNT(*) FROM cloud_connection_diagnostics').fetchone()[0] >= 2000:
        raise store.CloudConnectionError('capacity_reached')
    if kind not in {'directory_observe', 'quarantine_observe'}:
        store.requireIdle(connection)
        _requireReadyPool(connection)
    elif kind == 'directory_observe':
        _requireDirectoryRecoveryWindowTx(connection, parent.resourceId)
    else:
        _requireQuarantineRecoveryWindowTx(connection, parent.resourceId)
    contextId, resourceId, now = str(uuid4()), str(uuid4()), time.time()
    if kind == 'directory_execute':
        resourceId = parent.resourceId
    values = (contextId, actorId, authSessionId, action, connectionId, expected['connectionSecurityRevision'],
        expected['connectionStateRevision'], directoryProfileId, expected['directorySecurityRevision'],
        expected['directoryStateRevision'], expected['grantRevision'], expected['directoryGrantRevision'],
        policy['source']['id'], policy['source']['path_epoch'], policy['source']['revision'], path, policyHash,
        policy['identityHash'], *policy['names'], now)
    connection.execute('INSERT INTO cloud_task_contexts VALUES(' + ','.join('?' for _ in values) + ')', values)
    connection.execute('INSERT INTO cloud_request_registry VALUES(?,?,?,?,?,?,?,?)',
        (requestId, contextId, parentContextId, actorId, kind, resourceId, fingerprint, now))
    snapshot = _loadContextTx(connection, contextId)
    forbidden = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
    connection.set_authorizer(lambda operation, *_: sqlite3.SQLITE_DENY if operation in forbidden else sqlite3.SQLITE_OK)
    try:
        resourceFactory(connection, snapshot, resourceId, requestId)
    finally:
        connection.set_authorizer(None)
    resource = _checkResourceTx(connection, snapshot)
    store.auditChange(settings, connection, auditContext, contextId, 'prepare_cloud_intent',
                      {'requestId': requestId, 'kind': kind, 'contextId': contextId})
    return _view(snapshot, resource=resource)


def prepareDerivedCloudIntent(settings, parentJobId, *, requestId, action, intentPayload, resourceFactory,
                              accessToken, auditContext, resourceReader=None):
    if action not in _DERIVED_KINDS:
        raise store.CloudConnectionError('invalid_scope')
    store.cloudId(parentJobId)
    with store.connectionDb(settings, write=True) as connection:
        actorId = _requireContextActorTx(connection, auditContext, action)
        authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
        table, key, _ = _DERIVED_PARENTS.get(action, ('cloud_drive_inventory_jobs', 'id', 'inventory'))
        job = connection.execute(f'SELECT * FROM {table} WHERE {key}=? AND actor_id=?', (parentJobId, actorId)).fetchone()
        if job is None:
            raise store.CloudConnectionError('resource_not_found')
        if job['binding_state'] != 'bound':
            raise store.CloudConnectionError('legacy_unbound')
        parent = _resolveTx(connection, job['context_id'], actorId, authSessionId, 'history')
        policy = _policyTx(connection, actorId, parent.values['connection_id'], parent.values['profile_id'],
                           parent.values['selected_path'], action)
        receipt = _prepareCloudIntentTx(settings, connection, requestId=requestId, action=action,
            connectionId=parent.values['connection_id'], directoryProfileId=parent.values['profile_id'],
            selectedPath=parent.values['selected_path'], expected=policy['revisions'], policyHash=policy['policyHash'],
            accessToken=accessToken, auditContext=auditContext, resourceFactory=resourceFactory,
            parentContextId=parent.values['id'], intentPayload=intentPayload)
        if resourceReader is None:
            return receipt
        snapshot = _loadContextTx(connection, receipt['contextId'])
        resource = _checkResourceTx(connection, snapshot)
        forbidden = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
        connection.set_authorizer(lambda operation, *_: sqlite3.SQLITE_DENY if operation in forbidden else sqlite3.SQLITE_OK)
        try:
            return resourceReader(connection, snapshot, resource) | receipt
        finally:
            connection.set_authorizer(None)


def _resolveTx(connection, contextId, actorId, authSessionId, mode):
    snapshot = _loadContextTx(connection, contextId)
    row = snapshot.values
    _currentContentGrant(connection, actorId, row)
    _checkResourceTx(connection, snapshot)
    if mode == 'send':
        if row['auth_session_id'] != authSessionId:
            raise store.CloudConnectionError('context_stale')
        try:
            policy = _policyTx(connection, actorId, row['connection_id'], row['profile_id'], row['selected_path'], snapshot.resourceKind)
        except store.CloudConnectionError as exc:
            if exc.code == 'source_disabled':
                raise store.CloudConnectionError('context_stale') from None
            raise
        stored = (row['connection_security_revision'], row['profile_security_revision'], row['connection_state_revision'],
                  row['profile_state_revision'], row['connection_grant_revision'], row['directory_grant_revision'])
        if (stored != tuple(policy['revisions'][key] for key in _REVISION_KEYS) or policy['policyHash'] != row['policy_hash']
                or not policy['available'] or policy['identityHash'] != row['recovery_identity_hash']):
            raise store.CloudConnectionError('context_stale')
    return snapshot


def resolveCloudContext(settings, contextId, *, accessToken, auditContext, mode='history'):
    if mode not in ('history', 'send'):
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings) as connection:
        kind = _loadContextTx(connection, contextId).resourceKind
        actorId = _requireContextActorTx(connection, auditContext, kind)
        authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
        return _resolveTx(connection, contextId, actorId, authSessionId, mode)


def findCloudIntent(settings, requestId, *, accessToken, auditContext):
    store.cloudId(requestId)
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext)
        authSessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
        row = connection.execute('SELECT context_id FROM cloud_request_registry WHERE request_id=? AND actor_id=?',
                                 (requestId, actorId)).fetchone()
        if row is None:
            raise store.CloudConnectionError('resource_not_found')
        kind = _loadContextTx(connection, row['context_id']).resourceKind
        _requireContextActorTx(connection, auditContext, kind)
        return _view(_resolveTx(connection, row['context_id'], actorId, authSessionId, 'history'), replayed=True)
