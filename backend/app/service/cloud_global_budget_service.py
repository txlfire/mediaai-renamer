"""多连接全实例预算管理；本地独立事务，不解析单例、不重启或清除未决。"""

from dataclasses import replace

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service.cloud_drive_budget_service import cloudBudgetStatusTx, _pool, _wallNow, _audit
from app.service.scan_recovery_service import isProcessAlive


def _settingsTx(settings, connection):
    policy = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    if policy is None:
        raise store.CloudConnectionError('storage_unavailable')
    return replace(settings, cloudDrive=CloudDriveSettings(minIntervalSeconds=policy['min_interval'],
                   failureCooldownSeconds=policy['failure_cooldown']))


def globalCloudBudgetStatus(settings, *, auditContext):
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        return cloudBudgetStatusTx(_settingsTx(settings, connection), connection, includeLeaseIdentity=True) | {'networkAccess': False}


def pauseGlobalCloudBudget(settings, *, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        resolved = _settingsTx(settings, connection)
        now = _wallNow()
        pool = _pool(resolved, connection, now)
        revision = store.nextRevision(pool['revision'])
        connection.execute("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='manual_pause',revision=?,updated_at=? WHERE id=1",
                           (revision, now))
        _audit(settings, connection, 'pause', context=auditContext, detail={'reason': 'manual_pause', 'networkAccess': False})
        return cloudBudgetStatusTx(resolved, connection) | {'networkAccess': False}


def resumeGlobalCloudBudget(settings, *, expectedRevision, confirmRemoteIdle, auditContext):
    store.revisionValue(expectedRevision)
    if confirmRemoteIdle is not True:
        raise store.CloudConnectionError('confirmation_required')
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        # 未领取队列也须先明确取消；不得仅恢复池就复活原受理。
        store.requireIdle(connection)
        resolved = _settingsTx(settings, connection)
        now = _wallNow()
        pool = _pool(resolved, connection, now)
        if pool['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if now < pool['cooldown_until']:
            raise store.CloudConnectionError('global_protection_held')
        if pool['state'] == 'paused':
            revision = store.nextRevision(pool['revision'])
            connection.execute("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL,revision=?,updated_at=? WHERE id=1",
                               (revision, now))
            _audit(settings, connection, 'resume', context=auditContext,
                   detail={'remoteIdleConfirmed': True, 'replayed': False, 'networkAccess': False})
        return cloudBudgetStatusTx(resolved, connection) | {'networkAccess': False}


def releaseGlobalCloudDirectoryPause(settings, *, expectedRevision, confirmClientsStopped, confirmRemoteIdle, auditContext):
    from app.service.cloud_drive_budget_service import releaseDirectoryManualPauseTx
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        resolved = _settingsTx(settings, connection)
        pool = _pool(resolved, connection, _wallNow())
        store.nextRevision(pool['revision'])
        # 只读/诊断未决须先走各自管理核对，不能借目录分支跳过。
        if (connection.execute("SELECT 1 FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running') LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM cloud_connection_diagnostics WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('accepted','running','review') LIMIT 1").fetchone()):
            raise store.CloudConnectionError('global_protection_held')
        releaseDirectoryManualPauseTx(resolved, connection, expectedRevision=expectedRevision,
            confirmClientsStopped=confirmClientsStopped, confirmRemoteIdle=confirmRemoteIdle, auditContext=auditContext)
        return cloudBudgetStatusTx(resolved, connection, includeLeaseIdentity=True) | {'networkAccess': False}


def reviewGlobalCloudReadLease(settings, *, leaseId, expectedRevision, confirmClientsStopped,
                              confirmRemoteIdle, auditContext):
    """只核对已退出的只读发送者；不触及C2/C3写意图、不重放原调用。"""
    store.cloudId(leaseId)
    store.revisionValue(expectedRevision)
    if confirmClientsStopped is not True or confirmRemoteIdle is not True:
        raise store.CloudConnectionError('confirmation_required')
    from app.service.cloud_drive_budget_service import _validateLease, cloudDriveHostKey, _pauseTx
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        lease = connection.execute('SELECT * FROM cloud_drive_rpc_lease WHERE id=?', (leaseId,)).fetchone()
        if lease is None:
            raise store.CloudConnectionError('resource_not_found')
        _validateLease(lease)
        observed = dict(lease)
    try:
        if observed['owner_host'] != cloudDriveHostKey() or isProcessAlive(observed['owner_pid']):
            raise store.CloudConnectionError('worker_exit_unconfirmed')
    except (OSError, ValueError, TypeError):
        raise store.CloudConnectionError('worker_exit_unconfirmed') from None
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        current = connection.execute('SELECT * FROM cloud_drive_rpc_lease WHERE id=?', (leaseId,)).fetchone()
        if current is None or dict(current) != observed:
            raise store.CloudConnectionError('revision_conflict')
        from app.service.cloud_task_context_service import _loadContextTx, _checkResourceTx
        session = connection.execute('SELECT * FROM cloud_drive_read_sessions WHERE id=?', (observed['session_id'],)).fetchone()
        if session is None or session['context_id'] is None:
            raise store.CloudConnectionError('legacy_unbound')
        snapshot = _loadContextTx(connection, session['context_id'])
        if snapshot.resourceKind not in {'inventory', 'diagnostic_tls', 'diagnostic_token_runtime'}:
            raise store.CloudConnectionError('invalid_scope')
        resource = _checkResourceTx(connection, snapshot)
        if (resource['session_id'] != session['id'] or resource['worker_token'] is not None or resource['worker_pid'] is not None
                or resource['status'] in {'queued', 'accepted', 'running'}):
            raise store.CloudConnectionError('worker_exit_unconfirmed')
        resolved, now = _settingsTx(settings, connection), _wallNow()
        pool = _pool(resolved, connection, now)
        if pool['revision'] != expectedRevision:
            raise store.CloudConnectionError('revision_conflict')
        if now < pool['cooldown_until']:
            raise store.CloudConnectionError('global_protection_held')
        store.nextRevision(pool['revision'])
        connection.execute('DELETE FROM cloud_drive_rpc_lease WHERE id=?', (leaseId,))
        connection.execute("UPDATE cloud_drive_read_sessions SET status='paused',updated_at=? WHERE id=?", (now, session['id']))
        # 原结果仍未知：只结束本地保护核对，不改为成功、不恢复原请求资格。
        if snapshot.resourceKind in {'diagnostic_tls', 'diagnostic_token_runtime'}:
            connection.execute("UPDATE cloud_connection_diagnostics SET status='interrupted',reason_code='request_incomplete',"
                'revision=revision+1,updated_at=? WHERE id=?', (now, resource['id']))
        _pauseTx(resolved, connection, pool, 'local_review_complete', now, context=auditContext)
        _audit(settings, connection, 'review_read_lease', context=auditContext, targetId=leaseId,
            detail={'contextId': snapshot.values['id'], 'requestId': snapshot.requestId, 'rpcMethod': observed['rpc_method'],
                'originalPhase': observed['phase'], 'processExited': True, 'clientsStoppedConfirmed': True,
                'remoteIdleConfirmed': True, 'originalResultConfirmed': False, 'replayed': False, 'networkAccess': False})
        return cloudBudgetStatusTx(resolved, connection, includeLeaseIdentity=True) | {'networkAccess': False}
