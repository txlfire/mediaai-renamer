"""多连接事务与非秘密校验；不创建数据库、不发送网络请求。"""

from contextlib import closing, contextmanager
from hashlib import sha256
import json
import sqlite3
import time
import unicodedata
from uuid import UUID

from app.core.cloud_connection_schema import cloudMultiStorageReady, cloudMultiStoragePresent
from app.core.logger import get_logger
from app.service.audit_service import record_audit_event

MAX_CONNECTIONS = 64
MAX_PROFILES = 1024
MAX_PROFILES_PER_CONNECTION = 128
MAX_CONFIGURATION_BYTES = 32 * 1024 * 1024


class CloudConnectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def cloudId(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise CloudConnectionError('invalid_fields') from None
    return value


def revisionValue(value):
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise CloudConnectionError('invalid_revision')
    return value


def nextRevision(value):
    revisionValue(value)
    if value >= 2**63 - 2:
        raise CloudConnectionError('capacity_reached')
    return value + 1


def cloudName(value):
    if not isinstance(value, str):
        raise CloudConnectionError('invalid_fields')
    value = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(value) <= 80 or any(unicodedata.category(c).startswith('C') for c in value):
        raise CloudConnectionError('invalid_fields')
    return value


def intentHash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def connectionDb(settings, *, write=False, migration=False):
    try:
        mode = 'rw' if write else 'ro'
        with closing(sqlite3.connect(settings.database_path.resolve(strict=True).as_uri() + '?mode=' + mode,
                                    uri=True, timeout=0.2)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if not migration and not cloudMultiStorageReady(connection):
                raise CloudConnectionError('storage_version_unsupported' if cloudMultiStoragePresent(connection) else 'storage_not_migrated')
            try:
                yield connection
                if write:
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise
    except (sqlite3.Error, OSError) as exc:
        get_logger(__name__).warning('CD2多连接事务失败 mode=%s error_type=%s sqlite_code=%s',
            'write' if write else 'read', type(exc).__name__, getattr(exc, 'sqlite_errorcode', None))
        raise CloudConnectionError('storage_unavailable') from None


def requireActor(connection, auditContext, permission=None):
    actorId = auditContext.actor.id if auditContext is not None and auditContext.actor is not None else None
    row = connection.execute('SELECT enabled,permissions_json FROM users WHERE id=?', (actorId,)).fetchone()
    try:
        permissions = json.loads(row['permissions_json']) if row is not None else None
    except (ValueError, TypeError):
        permissions = None
    if (row is None or row['enabled'] != 1 or not isinstance(permissions, list)
            or permission is not None and permission not in permissions):
        raise CloudConnectionError('action_permission_denied')
    return actorId


def cloudMultiMode(settings):
    """按持久化存储标记分流，未知扩展版本不能回退到旧单例。"""
    with connectionDb(settings, migration=True) as connection:
        if cloudMultiStoragePresent(connection) and not cloudMultiStorageReady(connection):
            raise CloudConnectionError('storage_version_unsupported')
        return cloudMultiStorageReady(connection)


def auditChange(settings, connection, auditContext, targetId, action, detail=None):
    record_audit_event(settings, event_type='cloud_connections.configuration', action=action, result='success',
        summary='维护CD2连接、目录或授权；未发送云请求', target_type='cloud_connection', target_id=targetId,
        actor=auditContext.actor, ip_address=auditContext.ipAddress, user_agent=auditContext.userAgent,
        detail={'networkAccess': False, **(detail or {})}, connection=connection)


def rowById(connection, table, recordId):
    if table not in {'cloud_connections', 'cloud_directory_profiles'}:
        raise ValueError('未注册配置表')
    cloudId(recordId)
    row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (recordId,)).fetchone()
    if row is None:
        raise CloudConnectionError('resource_not_found')
    return row


def checkRevision(row, column, expected):
    revisionValue(expected)
    if row[column] != expected:
        raise CloudConnectionError('revision_conflict')


def requireIdle(connection):
    from app.service.cloud_drive_configuration_service import _blocked
    if _blocked(connection) or connection.execute(
        "SELECT 1 FROM cloud_connection_diagnostics WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL "
        "OR status IN ('accepted','running','review') LIMIT 1").fetchone():
        raise CloudConnectionError('global_protection_held')


def checkConfigurationCapacity(connection, extraBytes=0):
    total = connection.execute('SELECT COALESCE(SUM(length(CAST(endpoint AS BLOB))+length(CAST(ca_pem AS BLOB))+length(target_key)+length(trust_hash)+64),0) '
        'FROM cloud_connection_versions').fetchone()[0]
    total += connection.execute('SELECT COALESCE(SUM(length(CAST(approved_root AS BLOB))+length(mapping_key)+'
        'length(CAST(quarantine_name AS BLOB))+128),0) FROM cloud_directory_profile_versions').fetchone()[0]
    total += connection.execute('SELECT COALESCE(SUM(length(CAST(name AS BLOB))+length(CAST(name_key AS BLOB))+128),0) FROM cloud_connections').fetchone()[0]
    total += connection.execute('SELECT COALESCE(SUM(length(CAST(name AS BLOB))+length(CAST(name_key AS BLOB))+128),0) FROM cloud_directory_profiles').fetchone()[0]
    if total + extraBytes > MAX_CONFIGURATION_BYTES:
        raise CloudConnectionError('capacity_reached')


def configurationRequest(connection, requestId, actorId, fingerprint):
    cloudId(requestId)
    row = connection.execute('SELECT * FROM cloud_configuration_requests WHERE request_id=?', (requestId,)).fetchone()
    if row is None:
        return None
    if row['actor_id'] != actorId:
        raise CloudConnectionError('resource_not_found')
    if row['intent_hash'] != fingerprint:
        raise CloudConnectionError('request_context_conflict')
    return row['resource_id']


def recordConfigurationRequest(connection, requestId, actorId, kind, recordId, fingerprint):
    connection.execute('INSERT INTO cloud_configuration_requests VALUES(?,?,?,?,?,?)',
                       (requestId, actorId, kind, recordId, fingerprint, time.time()))


def pageValues(page, pageSize, search):
    if type(page) is not int or not 1 <= page <= 1000 or type(pageSize) is not int or not 1 <= pageSize <= 100:
        raise CloudConnectionError('invalid_fields')
    if not isinstance(search, str) or len(search) > 80:
        raise CloudConnectionError('invalid_fields')
    # instr按字面搜索，不将%或_解释为通配符。
    return search.strip(), (page - 1) * pageSize
