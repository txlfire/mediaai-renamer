"""小型路径修订及保守版本栅栏；不改历史路径、不遍历子树、不自动重新扫描。"""

import time

_TASK_KEYS = {"rename_operation": "max_rename_operation_id", "rollback_plan": "max_rollback_plan_id", "pending_move": "max_pending_move_id"}


def cloudPathEpoch(connection):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_directory_path_epoch'").fetchone()
    if not exists:
        return {"revision": 0, "max_scan_job_id": 0, **{key: 0 for key in _TASK_KEYS.values()}}
    row = connection.execute("SELECT * FROM cloud_directory_path_epoch WHERE id=1").fetchone()
    if row is None:
        raise ValueError("目录路径版本缺失，停止使用旧记录")
    return dict(row) if hasattr(row, "keys") else dict(zip([column[1] for column in connection.execute("PRAGMA table_info(cloud_directory_path_epoch)")], row))


def requireFreshScan(connection, scanId):
    epoch = cloudPathEpoch(connection)
    if epoch["revision"] > 0 and (scanId is None or int(scanId) <= epoch["max_scan_job_id"]):
        raise ValueError("目录路径已修订，请仅对将要操作的范围重新扫描；不会自动扫描整库")


def requireFreshFileTask(connection, kind, taskId):
    if taskId > 0 and taskId <= cloudPathEpoch(connection)[_TASK_KEYS[kind]]:
        raise ValueError("该文件计划早于目录路径修订，请重新扫描所选范围并生成新计划；不会重放旧动作")


def advanceCloudPathEpoch(connection, now):
    from app.core.cloud_connection_schema import cloudMultiStoragePresent, cloudMultiStorageReady
    from app.service.cloud_connection_store import nextRevision, CloudConnectionError
    oldEpoch = cloudPathEpoch(connection)
    epoch = nextRevision(oldEpoch['revision'])
    multi = cloudMultiStoragePresent(connection)
    if multi:
        if not cloudMultiStorageReady(connection):
            raise CloudConnectionError('storage_version_unsupported')
        maximum = connection.execute('SELECT COALESCE(MAX(path_epoch),0) FROM cloud_source_namespaces').fetchone()[0]
        nextRevision(maximum)
    tables = (("scan_jobs", "max_scan_job_id"), ("rename_operations", "max_rename_operation_id"),
              ("rename_rollback_plans", "max_rollback_plan_id"), ("pending_move_tasks", "max_pending_move_id"))
    maxima = [max(oldEpoch[key], connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]) for table, key in tables]
    connection.execute("UPDATE cloud_directory_path_epoch SET revision=?,max_scan_job_id=?,max_rename_operation_id=?,"
        "max_rollback_plan_id=?,max_pending_move_id=?,updated_at=? WHERE id=1", (epoch, *maxima, now))
    if multi:
        # 尚不能证明跨连接来源独立，所有小型命名空间一起失效，不枚举媒体路径。
        connection.execute('UPDATE cloud_source_namespaces SET path_epoch=path_epoch+1')
    connection.execute("UPDATE cloud_drive_read_sessions SET status='closed',updated_at=? WHERE status IN ('active','paused')", (now,))
    connection.execute("UPDATE cloud_drive_inventory_jobs SET revision=revision+1,updated_at=?", (now,))
    return epoch


def commitDirectoryPathRevision(settings, planId, *, actorId):
    from app.service.cloud_directory_admission_service import _row
    from app.service.cloud_inventory_service import inventoryDb
    with inventoryDb(settings, write=True) as connection:
        from app.service.cloud_bound_native_runtime_service import BoundNativeCloudSettings, requireNativeSourceTx
        row = _row(connection, planId)
        if isinstance(settings, BoundNativeCloudSettings):
            requireNativeSourceTx(settings, connection,
                mode='history' if row['status'] == 'completed' and not row['slot_held'] else 'send')
        return commitDirectoryPathRevisionTx(settings, connection, planId, actorId=actorId)


def commitDirectoryPathRevisionTx(settings, connection, planId, *, actorId, auditContext=None):
    """仅提交持久化核验事实；调用者负责同事务的来源和当前会话授权。"""
    from app.service.cloud_directory_admission_service import _row, _audit
    from app.service.cloud_directory_plan_service import CloudDirectoryError, _permission, directoryPolicyHash
    row = _row(connection, planId)
    if row['actor_id'] != actorId:
        raise CloudDirectoryError('not_found')
    _permission(connection, actorId)
    _permission(connection, actorId, 'cloud-directory:execute')
    if not settings.cloudDrive.directoryRenameEnabled or row['policy_hash'] != directoryPolicyHash(settings):
        raise CloudDirectoryError('execution_policy_changed')
    # 恢复已接管后，只能由恢复证据提交；终态幂等也不能绕过。
    if connection.execute('SELECT 1 FROM cloud_directory_recoveries WHERE plan_id=? LIMIT 1', (planId,)).fetchone():
        raise CloudDirectoryError('path_commit_requires_review')
    if row['status'] == 'completed' and row['slot_held'] == 0:
        return cloudPathEpoch(connection)['revision']
    if (row['reason'] != 'awaiting_path_sync' or row['worker_pid'] is not None or row['slot_held'] != 1
            or connection.execute("SELECT 1 FROM cloud_directory_rpc_intents WHERE plan_id=? AND phase!='responded' LIMIT 1", (planId,)).fetchone()):
        raise CloudDirectoryError('path_commit_requires_review')
    items = connection.execute('SELECT p.*,o.state FROM cloud_directory_plan_items p JOIN cloud_directory_item_outcomes o '
        'ON o.plan_id=p.plan_id AND o.ordinal=p.ordinal WHERE p.plan_id=? ORDER BY p.ordinal', (planId,)).fetchall()
    count = connection.execute('SELECT COUNT(*) FROM cloud_directory_plan_items WHERE plan_id=?', (planId,)).fetchone()[0]
    if not 1 <= count <= 5 or len(items) != count or any(item['state'] != 'verified' for item in items):
        raise CloudDirectoryError('path_commit_requires_review')
    now = time.time()
    epoch = advanceCloudPathEpoch(connection, now)
    connection.executemany('INSERT INTO cloud_directory_path_revisions(plan_id,ordinal,epoch,source_path,target_path,remote_id,committed_at) VALUES(?,?,?,?,?,?,?)',
        [(planId, item['ordinal'], epoch, item['source_path'], item['target_path'], item['remote_id'], now) for item in items])
    connection.execute("UPDATE cloud_directory_item_outcomes SET state='committed' WHERE plan_id=?", (planId,))
    connection.execute("UPDATE cloud_directory_executions SET status='completed',reason=NULL,slot_held=0,revision=revision+1,updated_at=? WHERE plan_id=?", (now, planId))
    _audit(settings, connection, row, 'path_revision_committed', context=auditContext, result='success', detail={'epoch': epoch,
        'itemCount': count, 'globalOldPlanFence': True, 'automaticRescan': False, 'historicalPathsRewritten': False})
    return epoch
