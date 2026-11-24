"""分层诊断本地状态及运行引用；只准受限两种RPC，事实入账不丢原结果。"""

from dataclasses import dataclass, field
from hashlib import sha256
import os
import time
from uuid import uuid4

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service.cloud_task_context_service import (
    DIAGNOSTIC_LIMITS, _loadContextTx, _checkResourceTx, _resolveTx, _view,
    prepareCloudIntent, resolveCloudAuthSessionTx, requireCloudAuthSessionIdTx,
)
from app.service.cloud_drive_budget_service import insertCloudReadSessionTx, CloudBudgetError, cloudDriveHostKey
from app.service.cloud_drive_error_codes import CD2_RPC_FAILURE_CODES

REASONS = CD2_RPC_FAILURE_CODES | {'context_stale', 'authentication_required', 'action_permission_denied',
    'resource_not_found', 'worker_ownership_lost', 'worker_start_failed', 'process_interrupted', 'cancel_requested',
    'actor_permission_denied', 'invalid_fields', 'pool_paused', 'session_expired', 'session_not_active',
    'tls_name_mismatch', 'tls_expired', 'tls_untrusted', 'tls_failed', 'token_root_mismatch',
    'unknown_token_permissions', 'overprivileged_or_missing_list_permission', 'invalid_response',
    'byte_limit', 'runtime_unsupported', 'permission_missing', 'storage_unavailable', 'worker_failed', 'wait_timeout', 'request_incomplete'}


def safeDiagnosticReason(code):
    return code if isinstance(code, str) and code in REASONS else 'worker_failed'


@dataclass(frozen=True)
class BoundDiagnosticSettings:
    base: object = field(repr=False)
    cloudDrive: CloudDriveSettings = field(repr=False)
    contextId: str
    resourceId: str
    workerToken: str | None = field(default=None, repr=False)
    supervisorPid: int | None = None

    def __getattr__(self, name):
        return getattr(self.base, name)


def _runtimeTx(settings, connection, snapshot, *, workerToken=None, supervisorPid=None):
    if snapshot.resourceKind not in {'diagnostic_tls', 'diagnostic_token_runtime'}:
        raise store.CloudConnectionError('resource_not_found')
    policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    version, directory = snapshot.connectionVersion, snapshot.directoryVersion
    config = CloudDriveSettings(approvedRoot=directory['approved_root'], endpoint=version['endpoint'],
        caCertificatePem=version['ca_pem'], minIntervalSeconds=policy['min_interval'],
        failureCooldownSeconds=policy['failure_cooldown'], directoryMappingRevision=directory['mapping_key'],
        quarantineFolderName=directory['quarantine_name'])
    return BoundDiagnosticSettings(settings, config, snapshot.values['id'], snapshot.resourceId, workerToken, supervisorPid)


def requireDiagnosticFactsTx(settings, connection):
    snapshot = _loadContextTx(connection, settings.contextId)
    if snapshot.resourceKind not in {'diagnostic_tls', 'diagnostic_token_runtime'} or snapshot.resourceId != settings.resourceId:
        raise store.CloudConnectionError('context_stale')
    row = _checkResourceTx(connection, snapshot)
    config, original, directory = settings.cloudDrive, snapshot.connectionVersion, snapshot.directoryVersion
    if (config.endpoint != original['endpoint'] or config.caCertificatePem != original['ca_pem']
            or config.approvedRoot != directory['approved_root'] or config.directoryMappingRevision != directory['mapping_key']
            or config.webExecutionEnabled or config.directoryRenameEnabled or config.quarantineEnabled):
        raise store.CloudConnectionError('context_stale')
    if settings.workerToken is None:
        if (row['status'] != 'accepted' or row['worker_token'] is not None or row['worker_pid'] is not None
                or row['owner_pid'] is not None or row['owner_host'] is not None or row['owner_instance'] is not None):
            raise store.CloudConnectionError('worker_ownership_lost')
    else:
        store.cloudId(row['owner_instance'])
        parent = settings.supervisorPid == row['owner_pid'] == os.getpid()
        child = row['worker_pid'] == os.getpid()
        if (row['worker_token'] != settings.workerToken or row['owner_host'] != cloudDriveHostKey()
                or not (parent or child)):
            raise store.CloudConnectionError('worker_ownership_lost')
    return snapshot, row


def requireDiagnosticSendTx(settings, connection, sessionId=None, *, network=False, method=None):
    snapshot, row = requireDiagnosticFactsTx(settings, connection)
    if sessionId is not None and row['session_id'] != sessionId:
        raise store.CloudConnectionError('context_stale')
    if row['status'] not in {'accepted', 'running'}:
        raise store.CloudConnectionError('context_stale')
    if network and (settings.workerToken is None or row['status'] != 'running' or row['worker_pid'] != os.getpid()):
        raise store.CloudConnectionError('worker_ownership_lost')
    if method is not None and (snapshot.resourceKind != 'diagnostic_token_runtime'
                              or method not in {'GetApiTokenInfo', 'GetRuntimeInfo'}):
        raise store.CloudConnectionError('invalid_fields')
    context = snapshot.values
    from app.service.cloud_drive_budget_service import requireCloudActor
    requireCloudActor(connection, context['actor_id'])
    requireCloudAuthSessionIdTx(connection, context['actor_id'], context['auth_session_id'])
    return _resolveTx(connection, context['id'], context['actor_id'], context['auth_session_id'], 'send')


def _factory(settings, auditContext):
    def create(connection, snapshot, resourceId, requestId):
        context = snapshot.values
        now = context['created_at']
        policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
        connection.execute('INSERT OR IGNORE INTO cloud_drive_budget_pool '
            "(id,state,min_interval,failure_cooldown,observed_at,updated_at) VALUES(1,'ready',?,?,?,?)",
            (policy['min_interval'], policy['failure_cooldown'], now, now))
        try:
            session = insertCloudReadSessionTx(settings, connection, requestId=requestId,
                requestHash=store.intentHash({'purpose': 'bound-diagnostic', 'contextId': context['id']}),
                endpointHash=sha256(snapshot.connectionVersion['target_key'].encode()).hexdigest(),
                rootPath=context['selected_path'], actorId=context['actor_id'], maxRpc=2, durationSeconds=30,
                now=now, auditContext=auditContext, contextId=context['id'])
        except CloudBudgetError as exc:
            raise store.CloudConnectionError('capacity_reached' if exc.code == 'session_capacity_reached' else 'storage_unavailable') from None
        connection.execute('INSERT INTO cloud_connection_diagnostics '
            '(id,request_id,context_id,session_id,status,token_state,created_at,expires_at,updated_at) '
            "VALUES(?,?,?,?,'accepted',?,?,?,?)", (resourceId, requestId, context['id'], session['id'],
                'not_provided' if snapshot.resourceKind == 'diagnostic_tls' else 'not_run', now, now + 30, now))
    return create


def prepareCloudDiagnostic(settings, *, stage, confirmNetwork, **values):
    if confirmNetwork is not True:
        raise store.CloudConnectionError('confirmation_required')
    return prepareCloudIntent(settings, action='diagnostic', stage=stage,
        resourceFactory=_factory(settings, values['auditContext']), **values)


def _ownedTx(connection, recordId, accessToken, auditContext, *, byRequest=False):
    store.cloudId(recordId)
    actorId = store.requireActor(connection, auditContext, 'scan:run')
    sessionId = resolveCloudAuthSessionTx(connection, actorId, accessToken)
    column = 'd.request_id' if byRequest else 'd.id'
    row = connection.execute('SELECT d.* FROM cloud_connection_diagnostics d JOIN cloud_task_contexts c ON c.id=d.context_id '
        f'WHERE {column}=? AND c.actor_id=?', (recordId, actorId)).fetchone()
    if row is None:
        raise store.CloudConnectionError('resource_not_found')
    snapshot = _resolveTx(connection, row['context_id'], actorId, sessionId, 'history')
    if snapshot.resourceKind not in {'diagnostic_tls', 'diagnostic_token_runtime'} or snapshot.resourceId != row['id']:
        raise store.CloudConnectionError('storage_unavailable')
    return row, snapshot


def _resultTx(connection, row, snapshot):
    session = connection.execute('SELECT * FROM cloud_drive_read_sessions WHERE id=?', (row['session_id'],)).fetchone()
    lease = connection.execute('SELECT phase FROM cloud_drive_rpc_lease WHERE session_id=?', (row['session_id'],)).fetchone()
    return _view(snapshot, resource=row) | {'stage': 'tls' if snapshot.resourceKind == 'diagnostic_tls' else 'token-runtime',
        'tlsState': row['tls_state'], 'tokenState': row['token_state'], 'runtimeState': row['runtime_state'],
        'coreBuild': row['core_build'], 'cloudApiBuild': row['cloud_api_build'], 'reasonCode': row['reason_code'],
        'limits': dict(DIAGNOSTIC_LIMITS), 'usedRpc': session['used_rpc'], 'receivedBytes': row['received_bytes'],
        'expiresAt': row['expires_at'], 'workerSlotHeld': row['worker_token'] is not None or row['worker_pid'] is not None,
        'unresolved': lease is not None and lease['phase'] in {'sending', 'unknown'},
        'networkAccess': False,
        'networkAttempted': True if row['tls_state'] != 'not_run' or session['used_rpc'] > 0 else None if
            row['status'] in {'running', 'interrupted', 'cancelled', 'review'} else False,
        'writeAvailable': False, 'executionAvailable': False,
        'queryUrl': '/api/cloud-drive/connection-diagnostics/' + row['id']}


def getCloudDiagnostic(settings, recordId, *, accessToken, auditContext, byRequest=False):
    with store.connectionDb(settings) as connection:
        row, snapshot = _ownedTx(connection, recordId, accessToken, auditContext, byRequest=byRequest)
        return _resultTx(connection, row, snapshot)


def cancelCloudDiagnostic(settings, recordId, *, accessToken, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        row, snapshot = _ownedTx(connection, recordId, accessToken, auditContext)
        if row['status'] in {'accepted', 'running'}:
            connection.execute("UPDATE cloud_connection_diagnostics SET status='cancelled',reason_code='cancel_requested',"
                'revision=revision+1,updated_at=? WHERE id=?', (time.time(), row['id']))
            store.auditChange(settings, connection, auditContext, row['id'], 'cancel_diagnostic', {'contextId': row['context_id']})
        row = connection.execute('SELECT * FROM cloud_connection_diagnostics WHERE id=?', (row['id'],)).fetchone()
        return _resultTx(connection, row, snapshot)


def claimCloudDiagnostic(settings, recordId):
    with store.connectionDb(settings, write=True) as connection:
        row = connection.execute('SELECT * FROM cloud_connection_diagnostics WHERE id=?', (recordId,)).fetchone()
        if row is None:
            raise store.CloudConnectionError('resource_not_found')
        snapshot = _loadContextTx(connection, row['context_id'])
        runtime = _runtimeTx(settings, connection, snapshot)
        requireDiagnosticSendTx(runtime, connection)
        from app.service.cloud_drive_budget_service import requireCloudSessionForWork
        requireCloudSessionForWork(runtime, connection, row['session_id'])
        now = time.time()
        if now < row['created_at'] or now >= row['expires_at']:
            raise store.CloudConnectionError('context_stale')
        token = str(uuid4())
        connection.execute('UPDATE cloud_connection_diagnostics SET worker_token=?,owner_pid=?,owner_host=?,owner_instance=?,'
            'revision=revision+1,updated_at=? WHERE id=?', (token, os.getpid(), cloudDriveHostKey(), str(uuid4()), now, recordId))
        return _runtimeTx(settings, connection, snapshot, workerToken=token, supervisorPid=os.getpid())


def persistDiagnosticPid(settings, pid):
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings.base, write=True) as connection:
        _, row = requireDiagnosticFactsTx(settings, connection)
        requireDiagnosticSendTx(settings, connection)
        if settings.supervisorPid != os.getpid() or row['worker_pid'] is not None or row['status'] != 'accepted':
            raise store.CloudConnectionError('worker_ownership_lost')
        connection.execute("UPDATE cloud_connection_diagnostics SET worker_pid=?,status='running',revision=revision+1,updated_at=? WHERE id=?",
                           (pid, time.time(), row['id']))


def loadDiagnosticWorker(settings, contextId, recordId, workerToken):
    store.cloudId(workerToken)
    with store.connectionDb(settings) as connection:
        snapshot = _loadContextTx(connection, contextId)
        if snapshot.resourceId != recordId:
            raise store.CloudConnectionError('resource_not_found')
        runtime = _runtimeTx(settings, connection, snapshot, workerToken=workerToken)
        _, row = requireDiagnosticFactsTx(runtime, connection)
        if row['worker_pid'] != os.getpid():
            raise store.CloudConnectionError('worker_ownership_lost')
        return runtime


def recordDiagnosticStage(settings, stage, state, result=None):
    options = {'tls': ('tls_state', {'passed', 'failed'}), 'token': ('token_state', {'passed', 'failed'}),
               'runtime': ('runtime_state', {'passed', 'failed', 'unsupported', 'permission_missing'})}
    if stage not in options or state not in options[stage][1]:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings.base, write=True) as connection:
        _, row = requireDiagnosticFactsTx(settings, connection)
        if row['worker_pid'] != os.getpid():
            raise store.CloudConnectionError('worker_ownership_lost')
        connection.execute(f'UPDATE cloud_connection_diagnostics SET {options[stage][0]}=?,revision=revision+1,updated_at=? WHERE id=?',
                           (state, time.time(), row['id']))
        if result is not None:
            # 版本内容只来自协议组件固定格式白名单，不接收任意远端文本。
            from app.service.cd2_diagnostic_client import _BUILD
            builds = (result.get('coreBuild'), result.get('cloudApiBuild'))
            if any(value is not None and (not isinstance(value, str) or _BUILD.fullmatch(value) is None) for value in builds):
                raise store.CloudConnectionError('invalid_fields')
            connection.execute('UPDATE cloud_connection_diagnostics SET core_build=?,cloud_api_build=? WHERE id=?', (*builds, row['id']))


def recordDiagnosticBytes(settings, size):
    if type(size) is not int or not 0 <= size <= 32768:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings.base, write=True) as connection:
        _, row = requireDiagnosticFactsTx(settings, connection)
        if row['worker_pid'] != os.getpid() or row['received_bytes'] + size > 65536:
            raise store.CloudConnectionError('invalid_fields')
        connection.execute('UPDATE cloud_connection_diagnostics SET received_bytes=received_bytes+?,updated_at=? WHERE id=?',
                           (size, time.time(), row['id']))


def finishDiagnosticObservation(settings, reason=None):
    with store.connectionDb(settings.base, write=True) as connection:
        snapshot, row = requireDiagnosticFactsTx(settings, connection)
        if row['worker_pid'] != os.getpid():
            raise store.CloudConnectionError('worker_ownership_lost')
        lease = connection.execute('SELECT phase FROM cloud_drive_rpc_lease WHERE session_id=?', (row['session_id'],)).fetchone()
        unknown = lease is not None and lease['phase'] in {'sending', 'unknown'}
        reason = safeDiagnosticReason(reason) if reason is not None else None
        connection.execute('UPDATE cloud_connection_diagnostics SET status=?,reason_code=?,revision=revision+1,updated_at=? WHERE id=?',
                           ('review' if unknown else 'cancelled' if row['status'] == 'cancelled' else 'completed', reason, time.time(), row['id']))
        if not unknown:
            connection.execute("UPDATE cloud_drive_read_sessions SET status=CASE WHEN status='active' THEN 'closed' ELSE status END,updated_at=? WHERE id=?",
                               (time.time(), row['session_id']))
        if reason is not None and reason not in {'runtime_unsupported', 'permission_missing', 'cancel_requested',
                'context_stale', 'authentication_required', 'resource_not_found', 'worker_ownership_lost',
                'actor_permission_denied', 'invalid_fields', 'pool_paused', 'session_expired', 'session_not_active', 'wait_timeout'}:
            from app.service.cloud_drive_budget_service import _pool, _pauseTx, _wallNow, _FAILURE_CODES
            now = _wallNow()
            pool = _pool(settings, connection, now)
            _pauseTx(settings, connection, pool, reason if reason in _FAILURE_CODES else 'unavailable', now)
        from app.service.audit_service import record_audit_event
        record_audit_event(settings, event_type='cloud_connections.diagnostic', action='diagnostic_observed',
            result='failed' if reason else 'success', summary='已记录诊断分层结果；不代表盘点或写入授权',
            actor_id=snapshot.values['actor_id'], actor_name='cloud_diagnostic_worker',
            target_type='cloud_diagnostic', target_id=row['id'], detail={'contextId': row['context_id'],
                'reasonCode': reason, 'unresolved': unknown, 'writeAvailable': False}, connection=connection)
