"""具名CD2连接及目录配置；保存与生命周期变更全部为本地事务。"""

from hashlib import sha256
import time
from uuid import uuid4

from app.core.config import CloudDriveSettings
from app.service.cloud_drive_configuration_service import _parse
from app.service import cloud_connection_store as store
from app.service.cloud_connection_store import CloudConnectionError
from app.utils.cloud_drive_paths import canonicalCd2Target, approvedCloudRoot, isWithinCloudRoot


def _connectionValues(endpoint, caCertificatePem):
    try:
        config = _parse({'endpoint': endpoint, 'caCertificatePem': caCertificatePem})
        target = canonicalCd2Target(config.endpoint)
    except ValueError:
        raise CloudConnectionError('invalid_fields') from None
    pem = config.caCertificatePem
    return 'https://' + target, target, pem, store.intentHash({'mode': 'public-ca' if pem else 'system-ca', 'pem': pem})


def _profileValues(approvedRoot, mappingKey, quarantineFolderName, **flags):
    try:
        root = approvedCloudRoot(approvedRoot)
        config = CloudDriveSettings(approvedRoot=root, endpoint='https://validation.invalid',
            directoryMappingRevision=mappingKey, quarantineFolderName=quarantineFolderName, **flags)
        if not config.directoryMappingRevision:
            raise ValueError
    except (ValueError, TypeError):
        raise CloudConnectionError('invalid_fields') from None
    return config


def connectionView(connection, connectionId, *, includeTrust=True):
    row = store.rowById(connection, 'cloud_connections', connectionId)
    version = connection.execute('SELECT * FROM cloud_connection_versions WHERE connection_id=? AND revision=?',
                                 (row['id'], row['security_revision'])).fetchone()
    value = {'id': row['id'], 'name': row['name'], 'state': row['state'],
        'metadataRevision': row['metadata_revision'], 'stateRevision': row['state_revision'],
        'securityRevision': row['security_revision'], 'endpoint': version['endpoint'],
        'caSha256': sha256(version['ca_pem'].encode('ascii')).hexdigest() if version['ca_pem'] else None,
        'networkAccess': False, 'cloudExecutionAvailable': False, 'createdAt': row['created_at'], 'updatedAt': row['updated_at']}
    if includeTrust:
        value['caCertificatePem'] = version['ca_pem']
    return value


def profileView(connection, profileId):
    row = store.rowById(connection, 'cloud_directory_profiles', profileId)
    version = connection.execute('SELECT * FROM cloud_directory_profile_versions WHERE profile_id=? AND revision=?',
                                 (profileId, row['security_revision'])).fetchone()
    return {'id': row['id'], 'connectionId': row['connection_id'], 'name': row['name'], 'state': row['state'],
        'metadataRevision': row['metadata_revision'], 'stateRevision': row['state_revision'],
        'securityRevision': row['security_revision'], 'approvedRoot': version['approved_root'],
        'mappingKey': version['mapping_key'], 'quarantineFolderName': version['quarantine_name'],
        'webExecutionEnabled': bool(version['inventory_enabled']), 'directoryRenameEnabled': bool(version['rename_enabled']),
        'quarantineEnabled': bool(version['quarantine_enabled']), 'networkAccess': False, 'cloudExecutionAvailable': False}


def _uniqueName(connection, table, name, *, connectionId=None, exceptId=''):
    if table == 'cloud_connections':
        found = connection.execute('SELECT 1 FROM cloud_connections WHERE name_key=? AND id<>?', (name, exceptId)).fetchone()
    else:
        found = connection.execute('SELECT 1 FROM cloud_directory_profiles WHERE connection_id=? AND name_key=? AND id<>?',
                                   (connectionId, name, exceptId)).fetchone()
    if found:
        raise CloudConnectionError('name_duplicate')


def _uniqueTarget(connection, target, exceptId=''):
    if connection.execute("SELECT 1 FROM cloud_connections c JOIN cloud_connection_versions v "
        "ON v.connection_id=c.id AND v.revision=c.security_revision WHERE c.state<>'archived' AND v.target_key=? AND c.id<>?",
        (target, exceptId)).fetchone():
        raise CloudConnectionError('endpoint_duplicate')


def _rootAvailable(connection, connectionId, root, exceptId=''):
    rows = connection.execute("SELECT v.approved_root FROM cloud_directory_profiles p JOIN cloud_directory_profile_versions v "
        "ON v.profile_id=p.id AND v.revision=p.security_revision WHERE p.connection_id=? AND p.state<>'archived' AND p.id<>?",
        (connectionId, exceptId)).fetchall()
    if any(isWithinCloudRoot(root, row[0]) or isWithinCloudRoot(row[0], root) for row in rows):
        raise CloudConnectionError('root_overlap')


def _insertConnection(connection, name, values):
    if (connection.execute('SELECT COUNT(*) FROM cloud_connections').fetchone()[0] >= store.MAX_CONNECTIONS
            or connection.execute('SELECT COUNT(*) FROM cloud_connection_versions').fetchone()[0] >= 4096):
        raise CloudConnectionError('capacity_reached')
    _uniqueName(connection, 'cloud_connections', name)
    endpoint, target, pem, trustHash = values
    _uniqueTarget(connection, target)
    store.checkConfigurationCapacity(connection, len(endpoint.encode()) + len(pem.encode()) + 2 * len(name.encode()) + 512)
    now, connectionId = time.time(), str(uuid4())
    connection.execute('INSERT INTO cloud_connections VALUES(?,?,?,\'disabled\',0,0,1,?,?)', (connectionId, name, name, now, now))
    connection.execute("INSERT INTO cloud_connection_versions VALUES(?,1,?,?,?,?,'strict',?)",
                       (connectionId, endpoint, target, pem, trustHash, now))
    return connectionId


def _insertProfile(connection, connectionId, name, config):
    parent = store.rowById(connection, 'cloud_connections', connectionId)
    if parent['state'] == 'archived':
        raise CloudConnectionError('source_disabled')
    if (connection.execute('SELECT COUNT(*) FROM cloud_directory_profiles').fetchone()[0] >= store.MAX_PROFILES
            or connection.execute('SELECT COUNT(*) FROM cloud_directory_profile_versions').fetchone()[0] >= 8192
            or connection.execute('SELECT COUNT(*) FROM cloud_directory_profiles WHERE connection_id=?', (connectionId,)).fetchone()[0] >= store.MAX_PROFILES_PER_CONNECTION):
        raise CloudConnectionError('capacity_reached')
    _uniqueName(connection, 'cloud_directory_profiles', name, connectionId=connectionId)
    _rootAvailable(connection, connectionId, config.approvedRoot)
    store.checkConfigurationCapacity(connection, len(config.approvedRoot.encode()) + 2 * len(name.encode()) + 512)
    profileId, namespaceId, now = str(uuid4()), str(uuid4()), time.time()
    connection.execute("INSERT INTO cloud_source_namespaces VALUES(?,'unverified',NULL,0,0,1,?)", (namespaceId, now))
    connection.execute("INSERT INTO cloud_directory_profiles VALUES(?,?,?,?,'disabled',0,0,1,?,?)",
                       (profileId, connectionId, name, name, now, now))
    connection.execute('INSERT INTO cloud_directory_profile_versions VALUES(?,1,?,?,?,0,0,0,?,?)',
                       (profileId, config.approvedRoot, config.directoryMappingRevision, namespaceId, config.quarantineFolderName, now))
    return profileId


def createCloudConnection(settings, *, requestId, name, endpoint, caCertificatePem, auditContext):
    name, values = store.cloudName(name), _connectionValues(endpoint, caCertificatePem)
    fingerprint = store.intentHash(['connection', name, values])
    with store.connectionDb(settings, write=True) as connection:
        actorId = store.requireActor(connection, auditContext, 'settings:write')
        oldId = store.configurationRequest(connection, requestId, actorId, fingerprint)
        if oldId:
            return {**connectionView(connection, oldId), 'replayed': True}
        connectionId = _insertConnection(connection, name, values)
        store.recordConfigurationRequest(connection, requestId, actorId, 'connection', connectionId, fingerprint)
        store.auditChange(settings, connection, auditContext, connectionId, 'create_connection')
        return {**connectionView(connection, connectionId), 'replayed': False}


def getCloudConnection(settings, connectionId, *, auditContext):
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        return connectionView(connection, connectionId)


def listCloudConnections(settings, *, auditContext, page=1, pageSize=20, search='', state=None):
    search, offset = store.pageValues(page, pageSize, search)
    if state not in (None, 'enabled', 'disabled', 'archived'):
        raise CloudConnectionError('invalid_fields')
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        where, params = 'instr(name,?)>0', [search]
        if state is not None:
            where += ' AND state=?'
            params.append(state)
        total = connection.execute('SELECT COUNT(*) FROM cloud_connections WHERE ' + where, params).fetchone()[0]
        rows = connection.execute('SELECT id FROM cloud_connections WHERE ' + where + ' ORDER BY created_at,id LIMIT ? OFFSET ?',
                                  [*params, pageSize, offset]).fetchall()
        return {'items': [connectionView(connection, row[0], includeTrust=False) for row in rows],
                'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False}


def _metadata(settings, connectionId, profileId, name, expected, auditContext):
    name = store.cloudName(name)
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        table = 'cloud_directory_profiles' if profileId else 'cloud_connections'
        row = store.rowById(connection, table, profileId or connectionId)
        if profileId and row['connection_id'] != connectionId:
            raise CloudConnectionError('resource_not_found')
        store.checkRevision(row, 'metadata_revision', expected)
        if row['name'] != name:
            store.nextRevision(expected)
            _uniqueName(connection, table, name, connectionId=connectionId, exceptId=row['id'])
            store.checkConfigurationCapacity(connection, max(0, 2 * (len(name.encode()) - len(row['name'].encode()))))
            cursor = connection.execute(f'UPDATE {table} SET name=?,name_key=?,metadata_revision=metadata_revision+1,updated_at=? '
                'WHERE id=? AND metadata_revision=?', (name, name, time.time(), row['id'], expected))
            if cursor.rowcount != 1:
                raise CloudConnectionError('revision_conflict')
            store.auditChange(settings, connection, auditContext, connectionId, 'rename_configuration', {'profileId': profileId})
        return profileView(connection, profileId) if profileId else connectionView(connection, connectionId)


def updateCloudConnectionMetadata(settings, connectionId, *, name, expectedMetadataRevision, auditContext):
    return _metadata(settings, connectionId, None, name, expectedMetadataRevision, auditContext)


def updateCloudDirectoryMetadata(settings, connectionId, profileId, *, name, expectedMetadataRevision, auditContext):
    return _metadata(settings, connectionId, profileId, name, expectedMetadataRevision, auditContext)


def publishCloudConnectionVersion(settings, connectionId, *, endpoint, caCertificatePem, expectedSecurityRevision, auditContext):
    values = _connectionValues(endpoint, caCertificatePem)
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        row = store.rowById(connection, 'cloud_connections', connectionId)
        store.checkRevision(row, 'security_revision', expectedSecurityRevision)
        old = connection.execute('SELECT endpoint,target_key,ca_pem,trust_hash FROM cloud_connection_versions WHERE connection_id=? AND revision=?',
                                 (connectionId, expectedSecurityRevision)).fetchone()
        if values[1] != old['target_key']:
            # 新端点可能解析为不同实例，不能继承原连接的目录批准或用户授权。
            raise CloudConnectionError('connection_endpoint_immutable')
        if tuple(old) == values:
            return connectionView(connection, connectionId)
        store.requireIdle(connection)
        if row['state'] != 'archived':
            _uniqueTarget(connection, values[1], connectionId)
        if connection.execute('SELECT COUNT(*) FROM cloud_connection_versions').fetchone()[0] >= 4096:
            raise CloudConnectionError('capacity_reached')
        store.checkConfigurationCapacity(connection, sum(len(value.encode()) for value in values) + 64)
        nextRevision, now = store.nextRevision(expectedSecurityRevision), time.time()
        connection.execute("INSERT INTO cloud_connection_versions VALUES(?,?,?,?,?,?,'strict',?)", (connectionId, nextRevision, *values, now))
        cursor = connection.execute('UPDATE cloud_connections SET security_revision=?,updated_at=? WHERE id=? AND security_revision=?',
                                    (nextRevision, now, connectionId, expectedSecurityRevision))
        if cursor.rowcount != 1:
            raise CloudConnectionError('revision_conflict')
        store.auditChange(settings, connection, auditContext, connectionId, 'connection_security', {'securityRevision': nextRevision})
        return connectionView(connection, connectionId)


def createCloudDirectoryProfile(settings, connectionId, *, requestId, name, approvedRoot, mappingKey, quarantineFolderName, auditContext):
    name = store.cloudName(name)
    config = _profileValues(approvedRoot, mappingKey, quarantineFolderName)
    fingerprint = store.intentHash(['profile', connectionId, name, config.approvedRoot, mappingKey, quarantineFolderName])
    with store.connectionDb(settings, write=True) as connection:
        actorId = store.requireActor(connection, auditContext, 'settings:write')
        oldId = store.configurationRequest(connection, requestId, actorId, fingerprint)
        if oldId:
            return {**profileView(connection, oldId), 'replayed': True}
        profileId = _insertProfile(connection, connectionId, name, config)
        store.recordConfigurationRequest(connection, requestId, actorId, 'profile', profileId, fingerprint)
        store.auditChange(settings, connection, auditContext, connectionId, 'create_profile', {'profileId': profileId})
        return {**profileView(connection, profileId), 'replayed': False}


def listCloudDirectoryProfiles(settings, connectionId, *, auditContext, page=1, pageSize=20, search=''):
    search, offset = store.pageValues(page, pageSize, search)
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        store.rowById(connection, 'cloud_connections', connectionId)
        params = (connectionId, search)
        total = connection.execute('SELECT COUNT(*) FROM cloud_directory_profiles WHERE connection_id=? AND instr(name,?)>0', params).fetchone()[0]
        rows = connection.execute('SELECT id FROM cloud_directory_profiles WHERE connection_id=? AND instr(name,?)>0 '
            'ORDER BY created_at,id LIMIT ? OFFSET ?', (*params, pageSize, offset)).fetchall()
        return {'items': [profileView(connection, row[0]) for row in rows], 'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False}


def publishCloudDirectoryPolicy(settings, connectionId, profileId, *, webExecutionEnabled, directoryRenameEnabled,
                                quarantineEnabled, quarantineFolderName, expectedSecurityRevision, auditContext):
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        row = store.rowById(connection, 'cloud_directory_profiles', profileId)
        if row['connection_id'] != connectionId:
            raise CloudConnectionError('resource_not_found')
        store.checkRevision(row, 'security_revision', expectedSecurityRevision)
        old = connection.execute('SELECT * FROM cloud_directory_profile_versions WHERE profile_id=? AND revision=?',
                                 (profileId, expectedSecurityRevision)).fetchone()
        _profileValues(old['approved_root'], old['mapping_key'], quarantineFolderName, webExecutionEnabled=webExecutionEnabled,
                       directoryRenameEnabled=directoryRenameEnabled, quarantineEnabled=quarantineEnabled)
        flags = (int(webExecutionEnabled), int(directoryRenameEnabled), int(quarantineEnabled))
        if (*flags, quarantineFolderName) == (old['inventory_enabled'], old['rename_enabled'], old['quarantine_enabled'], old['quarantine_name']):
            return profileView(connection, profileId)
        if any(new > old[key] for new, key in zip(flags, ('inventory_enabled', 'rename_enabled', 'quarantine_enabled'))) or quarantineFolderName != old['quarantine_name']:
            store.requireIdle(connection)
        if connection.execute('SELECT COUNT(*) FROM cloud_directory_profile_versions').fetchone()[0] >= 8192:
            raise CloudConnectionError('capacity_reached')
        store.checkConfigurationCapacity(connection, len(old['approved_root'].encode()) + len(quarantineFolderName.encode()) + 256)
        nextRevision, now = store.nextRevision(expectedSecurityRevision), time.time()
        connection.execute('INSERT INTO cloud_directory_profile_versions VALUES(?,?,?,?,?,?,?,?,?,?)',
            (profileId, nextRevision, old['approved_root'], old['mapping_key'], old['source_namespace_id'], *flags, quarantineFolderName, now))
        cursor = connection.execute('UPDATE cloud_directory_profiles SET security_revision=?,updated_at=? WHERE id=? AND security_revision=?',
                                    (nextRevision, now, profileId, expectedSecurityRevision))
        if cursor.rowcount != 1:
            raise CloudConnectionError('revision_conflict')
        store.auditChange(settings, connection, auditContext, connectionId, 'profile_policy', {'profileId': profileId, 'securityRevision': nextRevision})
        return profileView(connection, profileId)


def setCloudLifecycle(settings, connectionId, *, state, expectedStateRevision, auditContext, profileId=None):
    if state not in ('enabled', 'disabled', 'archived'):
        raise CloudConnectionError('invalid_fields')
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        parent = store.rowById(connection, 'cloud_connections', connectionId)
        table = 'cloud_directory_profiles' if profileId else 'cloud_connections'
        row = store.rowById(connection, table, profileId or connectionId)
        if profileId and row['connection_id'] != connectionId:
            raise CloudConnectionError('resource_not_found')
        store.checkRevision(row, 'state_revision', expectedStateRevision)
        if row['state'] != state:
            store.nextRevision(expectedStateRevision)
            if state in ('enabled', 'archived') or row['state'] == 'archived':
                store.requireIdle(connection)
            if state != 'archived':
                if profileId:
                    if parent['state'] == 'archived':
                        raise CloudConnectionError('source_disabled')
                    _rootAvailable(connection, connectionId, profileView(connection, profileId)['approvedRoot'], profileId)
                else:
                    target = connection.execute('SELECT target_key FROM cloud_connection_versions WHERE connection_id=? AND revision=?',
                        (connectionId, row['security_revision'])).fetchone()[0]
                    _uniqueTarget(connection, target, connectionId)
            cursor = connection.execute(f'UPDATE {table} SET state=?,state_revision=state_revision+1,updated_at=? WHERE id=? AND state_revision=?',
                                        (state, time.time(), row['id'], expectedStateRevision))
            if cursor.rowcount != 1:
                raise CloudConnectionError('revision_conflict')
            store.auditChange(settings, connection, auditContext, connectionId, 'lifecycle', {'profileId': profileId, 'state': state})
        return profileView(connection, profileId) if profileId else connectionView(connection, connectionId)
