"""受控任务的最小保护摘要和停止；不读取目录内容，不释放未决RPC。"""

import time

from app.service import cloud_connection_store as store
from app.service.audit_service import record_audit_event
from app.service.cloud_task_context_service import resolveCloudAuthSessionTx
from app.service.scan_recovery_service import isProcessAlive

_TABLES = {'inventory': 'cloud_drive_inventory_jobs', 'diagnostic_tls': 'cloud_connection_diagnostics',
           'diagnostic_token_runtime': 'cloud_connection_diagnostics'}
_NATIVE = {
    'directory_execute': ('cloud_directory_executions', 'plan_id', 'cloud_directory_rpc_intents', 'plan_id', 'reviewed_by_recovery'),
    'directory_observe': ('cloud_directory_recoveries', 'id', 'cloud_directory_recovery_rpcs', 'recovery_id', None),
    'quarantine_move': ('cloud_quarantine_runs', 'id', 'cloud_quarantine_rpcs', 'run_id', 'reviewed_by_run'),
    'quarantine_observe': ('cloud_quarantine_runs', 'id', 'cloud_quarantine_rpcs', 'run_id', 'reviewed_by_run'),
}
_NATIVE_REASONS = frozenset({'rpc_unknown', 'worker_interrupted', 'worker_start_failed', 'awaiting_path_sync',
    'positions_observed', 'position_ambiguous', 'positions_reconciled', 'cancel_requested', 'deadline_exceeded',
    'permission_denied', 'context_stale', 'authentication_required', 'resource_not_found',
    'execution_policy_changed', 'recovery_not_active'})


def _nativeUnresolvedSql(kind):
    # 标识符只来自内部白名单；按原主键查未决，不加载RPC路径、身份或响应正文。
    _, key, table, foreignKey, reviewed = _NATIVE[kind]
    if kind == 'directory_observe':
        from app.service.cloud_directory_recovery_evidence import UNRECONCILED_DIRECTORY_READ_RPC
        return f'EXISTS(SELECT 1 FROM {table} n WHERE n.{foreignKey}=t.{key} AND {UNRECONCILED_DIRECTORY_READ_RPC})'
    condition = f' AND n.{reviewed} IS NULL' if reviewed else ''
    return (f'EXISTS(SELECT 1 FROM {table} n WHERE n.{foreignKey}=t.{key} '
            f"AND n.phase IN ('sending','unknown'){condition})")


def _nativeRecordTx(connection, registry):
    kind = registry['resource_kind']
    table, key, _, _, _ = _NATIVE[kind]
    columns = (f't.{key} AS id,t.request_id,t.context_id,t.actor_id,t.status,t.worker_pid,t.revision,'
               't.owner_pid,t.owner_host,t.cancel_requested,t.slot_held,t.reason,t.binding_state')
    if kind.startswith('quarantine_'):
        columns += ',t.kind AS native_kind'
    row = connection.execute(f'SELECT {columns},{_nativeUnresolvedSql(kind)} AS unresolved FROM {table} t WHERE t.{key}=?',
                             (registry['resource_id'],)).fetchone()
    if (row is None or row['request_id'] != registry['request_id'] or row['context_id'] != registry['context_id']
            or row['actor_id'] != registry['actor_id'] or row['binding_state'] != 'bound'
            or registry['context_actor'] != registry['actor_id'] or registry['context_action'] != kind
            or kind.startswith('quarantine_') and row['native_kind'] != ('move' if kind == 'quarantine_move' else 'observe')):
        raise store.CloudConnectionError('storage_unavailable')
    lease = {'phase': 'unknown' if row['unresolved'] else 'reserved'} if row['unresolved'] or row['slot_held'] else None
    return registry, row, lease


def _actorTx(connection, accessToken, auditContext, manage):
    actorId = store.requireActor(connection, auditContext, 'settings:write' if manage else None)
    resolveCloudAuthSessionTx(connection, actorId, accessToken)
    return actorId


def _recordTx(connection, requestId, actorId, manage):
    store.cloudId(requestId)
    registry = connection.execute('SELECT r.request_id,r.context_id,r.actor_id,r.resource_kind,r.resource_id,'
        'c.actor_id AS context_actor,c.action AS context_action FROM cloud_request_registry r '
        'JOIN cloud_task_contexts c ON c.id=r.context_id WHERE r.request_id=?', (requestId,)).fetchone()
    if registry is None or not manage and registry['actor_id'] != actorId:
        raise store.CloudConnectionError('resource_not_found')
    if registry['resource_kind'] in _NATIVE:
        return _nativeRecordTx(connection, registry)
    if registry['resource_kind'] not in _TABLES:
        raise store.CloudConnectionError('invalid_scope')
    table = _TABLES[registry['resource_kind']]
    # 明确白名单投影，不将原目录、端点或名称加载进保护摘要。
    columns = ('id,request_id,context_id,session_id,status,worker_token,worker_pid,revision,owner_pid,owner_host,owner_instance' +
               ',actor_id,cancel_requested' if registry['resource_kind'] == 'inventory' else
               'id,request_id,context_id,session_id,status,worker_token,worker_pid,revision,owner_pid,owner_host,owner_instance')
    row = connection.execute(f'SELECT {columns} FROM {table} WHERE id=?', (registry['resource_id'],)).fetchone()
    if (row is None or row['request_id'] != requestId or row['context_id'] != registry['context_id']
            or registry['context_actor'] != registry['actor_id']
            or registry['resource_kind'] == 'inventory' and row['actor_id'] != registry['actor_id']):
        raise store.CloudConnectionError('storage_unavailable')
    lease = connection.execute('SELECT phase FROM cloud_drive_rpc_lease WHERE session_id=?', (row['session_id'],)).fetchone()
    return registry, row, lease


def _view(registry, row, lease):
    if registry['resource_kind'] in _NATIVE:
        reason = row['reason'] if row['reason'] in _NATIVE_REASONS else 'review_required' if row['reason'] is not None else None
        stopped = bool(row['cancel_requested'])
        return {'requestId': registry['request_id'], 'resourceKind': registry['resource_kind'], 'status': row['status'],
            'revision': row['revision'], 'workerSlotHeld': row['worker_pid'] is not None,
            'requestSlotHeld': lease is not None, 'unresolved': bool(row['unresolved']), 'stopRequested': stopped,
            'continuationBlocked': stopped or row['status'] not in {'accepted', 'running'}, 'reasonCode': reason,
            'networkAccess': False, 'retryAutomatically': False}
    held = row['worker_token'] is not None or row['worker_pid'] is not None
    return {'requestId': registry['request_id'], 'resourceKind': registry['resource_kind'], 'status': row['status'],
            'revision': row['revision'], 'workerSlotHeld': held, 'requestSlotHeld': lease is not None,
            'unresolved': lease is not None and lease['phase'] in {'sending', 'unknown'},
            'stopRequested': bool(row['cancel_requested']) if registry['resource_kind'] == 'inventory' else row['status'] == 'cancelled',
            'continuationBlocked': row['status'] not in {'queued', 'accepted', 'running'},
            'networkAccess': False, 'retryAutomatically': False}


def cloudReadProtection(settings, requestId, *, accessToken, auditContext, manage=False):
    with store.connectionDb(settings) as connection:
        actorId = _actorTx(connection, accessToken, auditContext, manage)
        return _view(*_recordTx(connection, requestId, actorId, manage))


def stopCloudReadIntent(settings, requestId, *, accessToken, auditContext, manage=False):
    with store.connectionDb(settings, write=True) as connection:
        actorId = _actorTx(connection, accessToken, auditContext, manage)
        registry, row, lease = _recordTx(connection, requestId, actorId, manage)
        now = time.time()
        changed = False
        if registry['resource_kind'] in _NATIVE:
            if not row['cancel_requested'] and (row['status'] not in {'completed', 'cancelled', 'committed'} or lease is not None):
                table, key, _, _, _ = _NATIVE[registry['resource_kind']]
                connection.execute(f'UPDATE {table} SET cancel_requested=1,revision=revision+1,updated_at=? WHERE {key}=?',
                                   (now, row['id']))
                changed = True
        elif registry['resource_kind'] == 'inventory' and row['status'] not in {'completed', 'cancelled'}:
            held = row['worker_token'] is not None or row['worker_pid'] is not None
            status = 'cancelled' if not held and lease is None else row['status']
            if not row['cancel_requested'] or status != row['status']:
                connection.execute('UPDATE cloud_drive_inventory_jobs SET cancel_requested=1,status=?,revision=revision+1,updated_at=? WHERE id=?',
                                   (status, now, row['id']))
                changed = True
        elif registry['resource_kind'] != 'inventory' and row['status'] in {'accepted', 'running'}:
            # 仅阻止后续；原worker仍可记录已发送响应，sending/unknown不删除。
            connection.execute("UPDATE cloud_connection_diagnostics SET status='cancelled',reason_code='cancel_requested',"
                               'revision=revision+1,updated_at=? WHERE id=?', (now, row['id']))
            changed = True
        if changed:
            record_audit_event(settings, event_type='cloud_drive.protection', action='stop_cloud_read_intent',
                result='success', summary='记录停止后续意图；未释放未决保护或重发请求',
                target_type='cloud_task_context', target_id=registry['context_id'],
                actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent,
                detail={'networkAccess': False, 'requestId': requestId, 'kind': registry['resource_kind'],
                        'managedStop': manage, 'originalActorId': registry['actor_id'],
                        'unresolvedPreserved': lease is not None}, connection=connection)
        return _view(*_recordTx(connection, requestId, actorId, manage))


def listCloudReadProtections(settings, *, accessToken, auditContext, page=1, pageSize=20):
    if type(page) is not int or not 1 <= page <= 1000 or type(pageSize) is not int or not 1 <= pageSize <= 100:
        raise store.CloudConnectionError('invalid_fields')
    with store.connectionDb(settings) as connection:
        _actorTx(connection, accessToken, auditContext, True)
        parts = []
        for kind, table in _TABLES.items():
            states = "('queued','running')" if kind == 'inventory' else "('accepted','running','review')"
            parts.append('SELECT r.request_id,r.resource_kind,r.actor_id,r.created_at,t.status,t.worker_pid,t.owner_pid,t.owner_host,t.revision,'
                't.worker_token IS NOT NULL AS worker_held,EXISTS(SELECT 1 FROM cloud_drive_rpc_lease l WHERE l.session_id=t.session_id) AS request_held '
                f'FROM cloud_request_registry r JOIN {table} t ON t.id=r.resource_id AND t.context_id=r.context_id '
                f"WHERE r.resource_kind='{kind}' AND (t.status IN {states} OR t.worker_token IS NOT NULL OR t.worker_pid IS NOT NULL "
                'OR EXISTS(SELECT 1 FROM cloud_drive_rpc_lease l WHERE l.session_id=t.session_id))')
        for kind, (table, key, _, _, _) in _NATIVE.items():
            unresolved = _nativeUnresolvedSql(kind)
            workerHeld = '(t.worker_pid IS NOT NULL)'
            nativeKind = " AND t.kind='move'" if kind == 'quarantine_move' else " AND t.kind='observe'" if kind == 'quarantine_observe' else ''
            parts.append('SELECT r.request_id,r.resource_kind,r.actor_id,r.created_at,t.status,t.worker_pid,t.owner_pid,t.owner_host,t.revision,'
                f'{workerHeld} AS worker_held,(t.slot_held=1 OR {unresolved}) AS request_held '
                f'FROM cloud_request_registry r JOIN {table} t ON t.{key}=r.resource_id AND t.context_id=r.context_id AND t.actor_id=r.actor_id '
                'JOIN cloud_task_contexts c ON c.id=r.context_id AND c.actor_id=r.actor_id AND c.action=r.resource_kind '
                f"WHERE r.resource_kind='{kind}' AND t.binding_state='bound'{nativeKind} "
                f'AND (t.slot_held=1 OR t.worker_pid IS NOT NULL OR {unresolved})')
        union = ' UNION ALL '.join(parts)
        total = connection.execute('SELECT COUNT(*) FROM (' + union + ')').fetchone()[0]
        rows = connection.execute('SELECT * FROM (' + union + ') ORDER BY created_at DESC,request_id '
            'LIMIT ? OFFSET ?', (pageSize, (page - 1) * pageSize)).fetchall()
        return {'items': [{'requestId': row['request_id'], 'resourceKind': row['resource_kind'], 'actorId': row['actor_id'],
            'status': row['status'], 'workerPid': row['worker_pid'], 'workerSlotHeld': bool(row['worker_held']),
            'ownerPid': row['owner_pid'], 'ownerHost': row['owner_host'], 'revision': row['revision'],
            'requestSlotHeld': bool(row['request_held'])} for row in rows],
            'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False}


def reviewCloudReadWorker(settings, requestId, *, expectedRevision, confirmClientsStopped, confirmRemoteIdle,
                         accessToken, auditContext):
    """核定原父子进程退出后归还本机槽；原RPC槽和安全事实均保留。"""
    store.revisionValue(expectedRevision)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True:
        raise store.CloudConnectionError('confirmation_required')
    with store.connectionDb(settings) as connection:
        actorId = _actorTx(connection, accessToken, auditContext, True)
        registry, row, lease = _recordTx(connection, requestId, actorId, True)
        if registry['resource_kind'] in _NATIVE:
            raise store.CloudConnectionError('invalid_scope')
        if row['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if row['worker_token'] is None and row['worker_pid'] is None:
            return _view(registry, row, lease)
        _requireConsistentChildEvidenceTx(connection, registry, row, lease)
        observed = dict(row)
    from app.service.cloud_drive_budget_service import cloudDriveHostKey
    try:
        store.cloudId(observed['owner_instance'])
        pids = [observed['owner_pid']] + ([observed['worker_pid']] if observed['worker_pid'] is not None else [])
        if (observed['owner_host'] != cloudDriveHostKey() or any(type(pid) is not int or not 1 <= pid <= 2**31 - 1
                or isProcessAlive(pid) for pid in pids) or observed['worker_pid'] is None and lease is not None):
            raise store.CloudConnectionError('worker_exit_unconfirmed')
    except (ValueError, TypeError, OSError):
        raise store.CloudConnectionError('worker_exit_unconfirmed') from None
    with store.connectionDb(settings, write=True) as connection:
        actorId = _actorTx(connection, accessToken, auditContext, True)
        registry, current, lease = _recordTx(connection, requestId, actorId, True)
        if dict(current) != observed:
            raise store.CloudConnectionError('revision_conflict')
        _requireConsistentChildEvidenceTx(connection, registry, current, lease)
        now = time.time()
        kind, table = registry['resource_kind'], _TABLES[registry['resource_kind']]
        status = current['status'] if lease is None and current['status'] in {'completed', 'cancelled'} else \
            'review' if lease is not None and kind != 'inventory' else 'interrupted'
        connection.execute(f'UPDATE {table} SET worker_token=NULL,worker_pid=NULL,status=?,revision=revision+1,updated_at=? WHERE id=?',
                           (status, now, current['id']))
        if kind == 'inventory':
            connection.execute("UPDATE cloud_drive_inventory_directories SET status='partial' WHERE job_id=? AND status='reading'", (current['id'],))
        store.auditChange(settings, connection, auditContext, registry['context_id'], 'review_cloud_read_worker',
            {'requestId': requestId, 'kind': kind, 'ownerPid': observed['owner_pid'], 'ownerHost': observed['owner_host'],
             'workerPid': observed['worker_pid'], 'processesExited': True, 'remoteIdleConfirmed': True,
             'requestSlotPreserved': lease is not None, 'replayed': False})
        return _view(*_recordTx(connection, requestId, actorId, True))


def _requireConsistentChildEvidenceTx(connection, registry, row, lease):
    if row['worker_pid'] is not None:
        return
    reading = registry['resource_kind'] == 'inventory' and connection.execute(
        "SELECT 1 FROM cloud_drive_inventory_directories WHERE job_id=? AND status='reading' LIMIT 1", (row['id'],)).fetchone()
    if lease is not None or reading:
        raise store.CloudConnectionError('worker_exit_unconfirmed')
