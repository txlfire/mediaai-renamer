"""直接用户/目录白名单、个人偏好及全局政策；不隐含媒体使用授权。"""

import json
import time

from app.core.config import CloudDriveSettings
from app.service import cloud_connection_store as store
from app.service.cloud_connection_service import profileView
from app.service.cloud_connection_store import CloudConnectionError


def _userId(connection, userId):
    if type(userId) is not int or not 1 <= userId < 2**63:
        raise CloudConnectionError('invalid_fields')
    if connection.execute('SELECT 1 FROM users WHERE id=?', (userId,)).fetchone() is None:
        raise CloudConnectionError('resource_not_found')


def _hasUsePermission(connection, actorId):
    permissions = json.loads(connection.execute('SELECT permissions_json FROM users WHERE id=?', (actorId,)).fetchone()[0])
    return any(permission in permissions for permission in ('scan:run', 'cloud-directory:plan', 'cloud-directory:execute',
                                                           'cloud-quarantine:plan', 'cloud-quarantine:execute'))


def _requireUseActor(connection, auditContext):
    actorId = store.requireActor(connection, auditContext)
    if not _hasUsePermission(connection, actorId):
        raise CloudConnectionError('action_permission_denied')
    return actorId


def _grantView(connection, connectionId, userId):
    row = connection.execute('SELECT * FROM cloud_connection_user_grants WHERE connection_id=? AND user_id=?',
                             (connectionId, userId)).fetchone()
    ids = [item[0] for item in connection.execute('SELECT profile_id FROM cloud_directory_user_grants '
        'WHERE connection_id=? AND user_id=? AND allowed=1 ORDER BY profile_id', (connectionId, userId))]
    return {'connectionId': connectionId, 'userId': userId, 'allowed': bool(row['allowed']) if row else False,
            'grantRevision': row['revision'] if row else 0, 'directoryProfileIds': ids if row and row['allowed'] else [], 'networkAccess': False}


def getCloudUserGrant(settings, connectionId, userId, *, auditContext):
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        store.rowById(connection, 'cloud_connections', connectionId)
        _userId(connection, userId)
        return _grantView(connection, connectionId, userId)


def replaceCloudUserGrant(settings, connectionId, userId, *, allowed, directoryProfileIds, expectedGrantRevision, auditContext):
    if (type(allowed) is not bool or not isinstance(directoryProfileIds, list) or len(directoryProfileIds) > 128
            or any(not isinstance(value, str) for value in directoryProfileIds)
            or len(directoryProfileIds) != len(set(directoryProfileIds)) or not allowed and directoryProfileIds):
        raise CloudConnectionError('invalid_fields')
    with store.connectionDb(settings, write=True) as connection:
        actorId = store.requireActor(connection, auditContext, 'settings:write')
        store.rowById(connection, 'cloud_connections', connectionId)
        _userId(connection, userId)
        current = _grantView(connection, connectionId, userId)
        store.revisionValue(expectedGrantRevision)
        if current['grantRevision'] != expectedGrantRevision:
            raise CloudConnectionError('revision_conflict')
        for profileId in directoryProfileIds:
            row = store.rowById(connection, 'cloud_directory_profiles', profileId)
            if row['connection_id'] != connectionId:
                raise CloudConnectionError('parent_mismatch')
            if row['state'] == 'archived':
                raise CloudConnectionError('source_disabled')
        requested = sorted(directoryProfileIds)
        if current['allowed'] == allowed and current['directoryProfileIds'] == requested:
            return current
        store.nextRevision(expectedGrantRevision)
        now = time.time()
        oldRows = {row['profile_id']: row for row in connection.execute('SELECT * FROM cloud_directory_user_grants WHERE connection_id=? AND user_id=?',
                    (connectionId, userId))}
        requiredRows = int(current['grantRevision'] == 0) + sum(profileId not in oldRows for profileId in requested)
        count = connection.execute('SELECT COUNT(*) FROM cloud_directory_user_grants').fetchone()[0]
        count += connection.execute('SELECT COUNT(*) FROM cloud_connection_user_grants').fetchone()[0]
        if count + requiredRows > 65536:
            raise CloudConnectionError('capacity_reached')
        connection.execute('INSERT INTO cloud_connection_user_grants VALUES(?,?,?,?,?,?) '
            'ON CONFLICT(connection_id,user_id) DO UPDATE SET allowed=excluded.allowed,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
            (connectionId, userId, int(allowed), expectedGrantRevision + 1, actorId, now))
        for profileId in set(oldRows) | set(requested):
            enabled = profileId in requested
            old = oldRows.get(profileId)
            if old is not None and bool(old['allowed']) == enabled:
                continue
            nextRevision = store.nextRevision(old['revision']) if old is not None else 1
            connection.execute('INSERT INTO cloud_directory_user_grants VALUES(?,?,?,?,?,?,?) '
                'ON CONFLICT(connection_id,user_id,profile_id) DO UPDATE SET allowed=excluded.allowed,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                (connectionId, userId, profileId, int(enabled), nextRevision, actorId, now))
        store.auditChange(settings, connection, auditContext, connectionId, 'user_grants',
                          {'userId': userId, 'grantRevision': expectedGrantRevision + 1, 'directoryCount': len(requested)})
        return _grantView(connection, connectionId, userId)


def _scope(connection, connectionId, profileId, actorId, *, allowDisabled=False):
    store.cloudId(connectionId)
    store.cloudId(profileId)
    row = connection.execute('SELECT c.state AS connection_state,c.state_revision AS connection_state_revision,'
        'c.security_revision AS connection_security_revision,p.state,p.state_revision,p.security_revision,'
        'g.revision AS grant_revision,d.revision AS directory_grant_revision '
        'FROM cloud_connections c JOIN cloud_directory_profiles p ON p.connection_id=c.id '
        'JOIN cloud_connection_user_grants g ON g.connection_id=c.id AND g.user_id=? AND g.allowed=1 '
        'JOIN cloud_directory_user_grants d ON d.connection_id=c.id AND d.profile_id=p.id AND d.user_id=g.user_id AND d.allowed=1 '
        'WHERE c.id=? AND p.id=?', (actorId, connectionId, profileId)).fetchone()
    if row is None:
        raise CloudConnectionError('resource_not_found')
    if row['connection_state'] == 'archived' or row['state'] == 'archived' or (not allowDisabled
            and (row['connection_state'] != 'enabled' or row['state'] != 'enabled')):
        raise CloudConnectionError('source_disabled')
    return {'connectionId': connectionId, 'directoryProfileId': profileId, 'grantRevision': row['grant_revision'],
        'directoryGrantRevision': row['directory_grant_revision'], 'connectionSecurityRevision': row['connection_security_revision'],
        'directorySecurityRevision': row['security_revision'], 'connectionStateRevision': row['connection_state_revision'],
        'directoryStateRevision': row['state_revision'], 'networkAccess': False}


def requireCloudScope(settings, connectionId, profileId, *, permission, auditContext, allowDisabled=False):
    with store.connectionDb(settings) as connection:
        actorId = store.requireActor(connection, auditContext, permission)
        return _scope(connection, connectionId, profileId, actorId, allowDisabled=allowDisabled)


def cloudConnectionChoices(settings, *, auditContext, connectionId=None, page=1, pageSize=20, search=''):
    search, offset = store.pageValues(page, pageSize, search)
    with store.connectionDb(settings) as connection:
        actorId = _requireUseActor(connection, auditContext)
        if connectionId is None:
            clause = "FROM cloud_connections c JOIN cloud_connection_user_grants g ON g.connection_id=c.id "
            clause += "WHERE g.user_id=? AND g.allowed=1 AND c.state<>'archived' AND instr(c.name,?)>0"
            params = (actorId, search)
            total = connection.execute('SELECT COUNT(*) ' + clause, params).fetchone()[0]
            rows = connection.execute('SELECT c.id,c.name,c.state,c.security_revision,c.state_revision,g.revision ' + clause +
                ' ORDER BY c.created_at,c.id LIMIT ? OFFSET ?', (*params, pageSize, offset)).fetchall()
            items = [{'id': row['id'], 'name': row['name'], 'state': row['state'], 'securityRevision': row['security_revision'],
                'stateRevision': row['state_revision'], 'grantRevision': row['revision']} for row in rows]
        else:
            store.cloudId(connectionId)
            allowed = connection.execute("SELECT 1 FROM cloud_connections c JOIN cloud_connection_user_grants g ON g.connection_id=c.id "
                "WHERE c.id=? AND g.user_id=? AND g.allowed=1 AND c.state<>'archived'", (connectionId, actorId)).fetchone()
            if not allowed:
                raise CloudConnectionError('resource_not_found')
            clause = 'FROM cloud_directory_profiles p JOIN cloud_directory_user_grants d ON d.profile_id=p.id AND d.connection_id=p.connection_id '
            clause += "WHERE p.connection_id=? AND d.user_id=? AND d.allowed=1 AND p.state<>'archived' AND instr(p.name,?)>0"
            params = (connectionId, actorId, search)
            total = connection.execute('SELECT COUNT(*) ' + clause, params).fetchone()[0]
            rows = connection.execute('SELECT p.id,d.revision ' + clause + ' ORDER BY p.created_at,p.id LIMIT ? OFFSET ?', (*params, pageSize, offset)).fetchall()
            items = [{**profileView(connection, row['id']), 'directoryGrantRevision': row['revision']} for row in rows]
        return {'items': items, 'total': total, 'page': page, 'pageSize': pageSize, 'networkAccess': False, 'cloudExecutionAvailable': False}


def _preferenceView(connection, actorId):
    row = connection.execute('SELECT * FROM cloud_user_preferences WHERE user_id=?', (actorId,)).fetchone()
    value = {'connectionId': row['connection_id'] if row else None, 'directoryProfileId': row['profile_id'] if row else None,
             'revision': row['revision'] if row else 0, 'available': False, 'networkAccess': False}
    if row is None or row['connection_id'] is None or not _hasUsePermission(connection, actorId):
        return value
    try:
        if row['profile_id'] is not None:
            _scope(connection, row['connection_id'], row['profile_id'], actorId)
        elif connection.execute("SELECT 1 FROM cloud_connections c JOIN cloud_connection_user_grants g ON g.connection_id=c.id "
            "WHERE c.id=? AND c.state='enabled' AND g.user_id=? AND g.allowed=1", (row['connection_id'], actorId)).fetchone() is None:
            return value
        value['available'] = True
    except CloudConnectionError:
        pass
    return value


def getCloudPreference(settings, *, auditContext):
    with store.connectionDb(settings) as connection:
        return _preferenceView(connection, store.requireActor(connection, auditContext))


def saveCloudPreference(settings, *, connectionId, directoryProfileId, expectedRevision, auditContext):
    store.revisionValue(expectedRevision)
    if directoryProfileId is not None and connectionId is None:
        raise CloudConnectionError('parent_mismatch')
    with store.connectionDb(settings, write=True) as connection:
        actorId = store.requireActor(connection, auditContext)
        if connectionId is not None:
            _requireUseActor(connection, auditContext)
        old = _preferenceView(connection, actorId)
        if old['revision'] != expectedRevision:
            raise CloudConnectionError('revision_conflict')
        if directoryProfileId is not None:
            _scope(connection, connectionId, directoryProfileId, actorId)
        elif connectionId is not None:
            store.cloudId(connectionId)
            if connection.execute("SELECT 1 FROM cloud_connections c JOIN cloud_connection_user_grants g ON g.connection_id=c.id "
                "WHERE c.id=? AND c.state='enabled' AND g.user_id=? AND g.allowed=1", (connectionId, actorId)).fetchone() is None:
                raise CloudConnectionError('resource_not_found')
        if (old['connectionId'], old['directoryProfileId']) != (connectionId, directoryProfileId):
            store.nextRevision(expectedRevision)
            connection.execute('INSERT INTO cloud_user_preferences VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE '
                'SET connection_id=excluded.connection_id,profile_id=excluded.profile_id,revision=excluded.revision,updated_at=excluded.updated_at',
                (actorId, connectionId, directoryProfileId, expectedRevision + 1, time.time()))
            store.auditChange(settings, connection, auditContext, str(actorId), 'preference')
        return _preferenceView(connection, actorId)


def _policyView(connection):
    row = connection.execute('SELECT * FROM cloud_global_policy WHERE id=1').fetchone()
    if row is None:
        raise CloudConnectionError('storage_unavailable')
    return {'revision': row['revision'], 'minIntervalSeconds': row['min_interval'],
            'failureCooldownSeconds': row['failure_cooldown'], 'networkAccess': False}


def getCloudGlobalPolicy(settings, *, auditContext):
    with store.connectionDb(settings) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        return _policyView(connection)


def updateCloudGlobalPolicy(settings, *, minIntervalSeconds, failureCooldownSeconds, expectedRevision, auditContext):
    store.revisionValue(expectedRevision)
    try:
        config = CloudDriveSettings(minIntervalSeconds=minIntervalSeconds, failureCooldownSeconds=failureCooldownSeconds)
    except (ValueError, TypeError):
        raise CloudConnectionError('invalid_fields') from None
    with store.connectionDb(settings, write=True) as connection:
        store.requireActor(connection, auditContext, 'settings:write')
        old = _policyView(connection)
        if old['revision'] != expectedRevision:
            raise CloudConnectionError('revision_conflict')
        if (minIntervalSeconds, failureCooldownSeconds) == (old['minIntervalSeconds'], old['failureCooldownSeconds']):
            return old
        store.nextRevision(expectedRevision)
        if minIntervalSeconds < old['minIntervalSeconds'] or failureCooldownSeconds < old['failureCooldownSeconds']:
            store.requireIdle(connection)
            pool = connection.execute('SELECT state,cooldown_until FROM cloud_drive_budget_pool WHERE id=1').fetchone()
            if pool is not None and (pool['state'] != 'ready' or pool['cooldown_until'] > time.time()):
                raise CloudConnectionError('global_protection_held')
        poolRevision = connection.execute('SELECT revision FROM cloud_drive_budget_pool WHERE id=1').fetchone()
        if poolRevision is not None:
            store.nextRevision(poolRevision[0])
        now = time.time()
        cursor = connection.execute('UPDATE cloud_global_policy SET revision=revision+1,min_interval=?,failure_cooldown=?,updated_at=? WHERE id=1 AND revision=?',
            (config.minIntervalSeconds, config.failureCooldownSeconds, now, expectedRevision))
        if cursor.rowcount != 1:
            raise CloudConnectionError('revision_conflict')
        connection.execute('UPDATE cloud_drive_budget_pool SET revision=revision+1,min_interval=?,failure_cooldown=?,updated_at=? WHERE id=1',
            (config.minIntervalSeconds, config.failureCooldownSeconds, now))
        store.auditChange(settings, connection, auditContext, 'global', 'global_policy', {'revision': expectedRevision + 1})
        return _policyView(connection)
