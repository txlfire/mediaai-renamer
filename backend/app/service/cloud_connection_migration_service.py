"""MC2显式离线扩展迁移；不挂接应用启动、不调用CD2、不认领旧任务。"""

import time

from app.core.cloud_connection_schema import createCloudConnectionSchema, STORAGE_KEY, STORAGE_VERSION
from app.service import cloud_connection_store as store
from app.service.cloud_connection_service import _insertConnection, _insertProfile, _connectionValues, _profileValues
from app.service.cloud_drive_configuration_service import _read, _blocked

ROOT_TABLES = (
    ('cloud_drive_inventory_jobs', 'id'), ('cloud_directory_plans', 'id'),
    ('cloud_directory_executions', 'plan_id'), ('cloud_directory_recoveries', 'id'),
    ('cloud_quarantine_plans', 'id'), ('cloud_quarantine_runs', 'id'), ('cloud_ad_reviews', 'id'),
)


def _extendLegacyBindings(connection):
    connection.execute('ALTER TABLE cloud_drive_read_sessions ADD COLUMN context_id TEXT REFERENCES cloud_task_contexts(id)')
    for table, primaryKey in ROOT_TABLES:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN context_id TEXT REFERENCES cloud_task_contexts(id)')
        connection.execute(f"ALTER TABLE {table} ADD COLUMN binding_state TEXT NOT NULL DEFAULT 'legacy_unbound' "
            "CHECK((binding_state='bound' AND context_id IS NOT NULL) OR (binding_state='legacy_unbound' AND context_id IS NULL))")
        connection.execute(f'CREATE INDEX idx_mc2_{table}_context ON {table}(context_id,created_at,{primaryKey})')
        connection.execute(f"CREATE TRIGGER mc2_{table}_insert BEFORE INSERT ON {table} "
            "WHEN NEW.binding_state<>'bound' OR NEW.context_id IS NULL "
            "BEGIN SELECT RAISE(ABORT,'multi_connection_context_required'); END")
        connection.execute(f'CREATE TRIGGER mc2_{table}_binding BEFORE UPDATE OF context_id,binding_state ON {table} '
            "WHEN NEW.context_id IS NOT OLD.context_id OR NEW.binding_state<>OLD.binding_state "
            "BEGIN SELECT RAISE(ABORT,'immutable_source_binding'); END")
    connection.execute('CREATE INDEX idx_mc2_read_session_context ON cloud_drive_read_sessions(context_id,created_at,id)')
    connection.execute("CREATE TRIGGER mc2_read_session_insert BEFORE INSERT ON cloud_drive_read_sessions "
        "WHEN NEW.context_id IS NULL BEGIN SELECT RAISE(ABORT,'multi_connection_context_required'); END")


def _result(connection, *, alreadyMigrated):
    ids = dict(connection.execute("SELECT key,value FROM app_meta WHERE key IN ('cloud_multi_legacy_connection_id','cloud_multi_legacy_profile_id')"))
    return {'alreadyMigrated': alreadyMigrated, 'storageVersion': STORAGE_VERSION,
        'legacyConnectionId': ids.get('cloud_multi_legacy_connection_id'),
        'legacyDirectoryProfileId': ids.get('cloud_multi_legacy_profile_id'),
        'cloudExecutionAvailable': False, 'networkAccess': False}


def _upgradeStorage(connection, settings, auditContext, previousVersion):
    version = connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
    if version is None or version[0] != '55':
        raise store.CloudConnectionError('migration_schema_unsupported')
    if connection.execute('PRAGMA foreign_key_check').fetchone():
        raise store.CloudConnectionError('migration_integrity_failed')
    if _blocked(connection) or connection.execute("SELECT 1 FROM cloud_connection_diagnostics WHERE worker_token IS NOT NULL "
            "OR worker_pid IS NOT NULL OR status IN ('accepted','running','review') LIMIT 1").fetchone():
        raise store.CloudConnectionError('global_protection_held')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(cloud_connection_diagnostics)')}
    ownerColumns = {'owner_pid', 'owner_host', 'owner_instance'}
    if (previousVersion == '1' and columns & ownerColumns or previousVersion == '2' and not ownerColumns <= columns
            or connection.execute("SELECT 1 FROM sqlite_master WHERE name='idx_cloud_request_context'").fetchone()):
        raise store.CloudConnectionError('migration_integrity_failed')
    if previousVersion == '1':
        for definition in ('owner_pid INTEGER CHECK(owner_pid IS NULL OR owner_pid BETWEEN 1 AND 2147483647)',
                           'owner_host TEXT CHECK(owner_host IS NULL OR length(owner_host)=64)', 'owner_instance TEXT'):
            connection.execute('ALTER TABLE cloud_connection_diagnostics ADD COLUMN ' + definition)
    connection.execute('CREATE INDEX idx_cloud_request_context ON cloud_request_registry(context_id)')
    connection.execute('UPDATE app_meta SET value=? WHERE key=?', (STORAGE_VERSION, STORAGE_KEY))
    store.auditChange(settings, connection, auditContext, 'migration', 'offline_context_lookup_upgrade',
        {'fromStorageVersion': previousVersion, 'storageVersion': STORAGE_VERSION, 'historicalOwnerGuessed': False})
    if connection.execute('PRAGMA foreign_key_check').fetchone():
        raise store.CloudConnectionError('migration_integrity_failed')
    return _result(connection, alreadyMigrated=False)


def migrateCloudConnectionStorage(settings, *, confirmOffline, auditContext):
    if confirmOffline is not True:
        raise store.CloudConnectionError('offline_confirmation_required')
    with store.connectionDb(settings, write=True, migration=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        marker = connection.execute('SELECT value FROM app_meta WHERE key=?', (STORAGE_KEY,)).fetchone()
        if marker is not None:
            if marker[0] in {'1', '2'}:
                return _upgradeStorage(connection, settings, auditContext, marker[0])
            if marker[0] != STORAGE_VERSION:
                raise store.CloudConnectionError('storage_version_unsupported')
            return _result(connection, alreadyMigrated=True)
        version = connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != '55':
            raise store.CloudConnectionError('migration_schema_unsupported')
        if _blocked(connection):
            raise store.CloudConnectionError('global_protection_held')
        if connection.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchone():
            raise store.CloudConnectionError('migration_integrity_failed')
        total = 0
        for table, key in (*ROOT_TABLES, ('cloud_drive_read_sessions', 'id')):
            total += connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if total > 50000:
                raise store.CloudConnectionError('migration_capacity_reached')
            after = None
            # 有界遍历只核对主键，不加载快照或媒体正文。
            while True:
                query = f'SELECT {key} FROM {table}'
                rows = connection.execute(query + (f' WHERE {key}>?' if after is not None else '') + f' ORDER BY {key} LIMIT 500',
                                          (after,) if after is not None else ()).fetchall()
                if not rows:
                    break
                after = rows[-1][0]
        try:
            config, _ = _read(connection)
        except ValueError:
            raise store.CloudConnectionError('migration_configuration_invalid') from None
        pool = connection.execute('SELECT min_interval,failure_cooldown FROM cloud_drive_budget_pool WHERE id=1').fetchone()
        interval, cooldown = (pool[0], pool[1]) if pool else (config.minIntervalSeconds, config.failureCooldownSeconds)
        if not 2 <= interval <= 300 or not 60 <= cooldown <= 86400:
            raise store.CloudConnectionError('migration_policy_invalid')
        createCloudConnectionSchema(connection)
        connection.execute('INSERT INTO cloud_global_policy VALUES(1,0,?,?,?)', (interval, cooldown, time.time()))
        if config.endpoint and config.approvedRoot and config.directoryMappingRevision:
            connectionId = _insertConnection(connection, '旧配置（待确认）', _connectionValues(config.endpoint, config.caCertificatePem))
            profileId = _insertProfile(connection, connectionId, '旧媒体目录（待确认）',
                _profileValues(config.approvedRoot, config.directoryMappingRevision, config.quarantineFolderName))
            for key, value in (('cloud_multi_legacy_connection_id', connectionId), ('cloud_multi_legacy_profile_id', profileId)):
                connection.execute('INSERT INTO app_meta(key,value) VALUES(?,?)', (key, value))
        _extendLegacyBindings(connection)
        connection.execute('INSERT INTO app_meta(key,value) VALUES(?,?)', (STORAGE_KEY, STORAGE_VERSION))
        store.auditChange(settings, connection, auditContext, 'migration', 'offline_storage_migration',
                          {'legacyRows': total, 'executionEnabled': False, 'historicalBinding': 'legacy_unbound'})
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise store.CloudConnectionError('migration_integrity_failed')
        return _result(connection, alreadyMigrated=False)
