"""仅对显式离线数据库副本演练多连接迁移，不导入应用或替换运行库。"""

import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.config import AppSettings, LoggingSettings
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import AuthUser
from app.service.cloud_connection_migration_service import migrateCloudConnectionStorage
from app.service.cloud_connection_store import CloudConnectionError


class RehearsalError(ValueError):
    pass


def fileHash(path):
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def requireColdFiles(source):
    # 只接受停服后生成的独立主文件，不能漏掉仍含提交数据的WAL或回滚日志。
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise RehearsalError('source_has_live_sidecar')


def copyExclusive(source, target):
    with source.open('rb') as reader, target.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
    target.chmod(0o600)


def inspectDatabase(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchone():
            raise RehearsalError('copy_integrity_failed')
        schema = connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        if schema != ('55',):
            raise RehearsalError('source_schema_unsupported')
        return schema[0]


def migrationActor(path, actorId):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        row = connection.execute('SELECT username,enabled,permissions_json FROM users WHERE id=?', (actorId,)).fetchone()
    if row is None or row[1] != 1:
        raise RehearsalError('operator_not_allowed')
    try:
        permissions = json.loads(row[2])
    except (TypeError, ValueError):
        raise RehearsalError('operator_not_allowed') from None
    if not isinstance(permissions, list) or any(not isinstance(value, str) for value in permissions) or 'settings:write' not in permissions:
        raise RehearsalError('operator_not_allowed')
    return AuthUser(actorId, row[0], '', tuple(permissions), True, False, None, None, '', '')


def rehearse(sourceDb, outputDirectory, actorId, *, confirmOfflineCopy=False):
    if confirmOfflineCopy is not True or type(actorId) is not int or actorId < 1:
        raise RehearsalError('offline_copy_confirmation_required')
    source = Path(sourceDb).resolve(strict=True)
    if not source.is_file() or source.stat().st_size == 0:
        raise RehearsalError('source_file_required')
    output = Path(outputDirectory)
    if output.exists() or output.is_symlink():
        raise RehearsalError('new_output_directory_required')
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise RehearsalError('output_parent_required')
    output = parent / output.name
    requireColdFiles(source)
    originalHash = fileHash(source)
    inspectDatabase(source)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        if connection.execute("SELECT 1 FROM app_meta WHERE key='cloud_multi_storage_version'").fetchone():
            raise RehearsalError('already_multi_connection_source')
    actor = migrationActor(source, actorId)
    if shutil.disk_usage(parent).free < source.stat().st_size * 4 + 16 * 1024 * 1024:
        raise RehearsalError('insufficient_copy_space')
    output.mkdir(mode=0o700)
    baseline, candidate, restored = (output / name for name in ('baseline.sqlite3', 'candidate.sqlite3', 'restored.sqlite3'))
    copyExclusive(source, baseline)
    requireColdFiles(source)
    if fileHash(source) != originalHash or fileHash(baseline) != originalHash:
        raise RehearsalError('source_changed_during_copy')
    copyExclusive(baseline, candidate)
    settings = AppSettings(data_dir=output, database_path=candidate, logging=LoggingSettings(log_dir=output / 'logs', console_output=False))
    context = AuditWriteContext(actor, userAgent='offline-copy-rehearsal')
    first = migrateCloudConnectionStorage(settings, confirmOffline=True, auditContext=context)
    second = migrateCloudConnectionStorage(settings, confirmOffline=True, auditContext=context)
    if second['alreadyMigrated'] is not True or first['networkAccess'] is not False or second['cloudExecutionAvailable'] is not False:
        raise RehearsalError('migration_result_invalid')
    with closing(sqlite3.connect(candidate)) as connection:
        checkpoint = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint and checkpoint[0] != 0:
            raise RehearsalError('candidate_checkpoint_failed')
    requireColdFiles(candidate)
    inspectDatabase(candidate)
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        if connection.execute("SELECT 1 FROM cloud_connections WHERE state<>'disabled' LIMIT 1").fetchone() or connection.execute(
                "SELECT 1 FROM cloud_directory_profiles WHERE state<>'disabled' LIMIT 1").fetchone() or connection.execute(
                'SELECT 1 FROM cloud_directory_profile_versions WHERE inventory_enabled<>0 OR rename_enabled<>0 OR quarantine_enabled<>0 LIMIT 1').fetchone():
            raise RehearsalError('candidate_actions_not_disabled')
        if connection.execute('SELECT COUNT(*) FROM cloud_connection_user_grants').fetchone()[0] or connection.execute('SELECT COUNT(*) FROM cloud_directory_user_grants').fetchone()[0]:
            raise RehearsalError('candidate_has_user_grants')
    copyExclusive(baseline, restored)
    inspectDatabase(restored)
    requireColdFiles(source)
    if fileHash(source) != originalHash or fileHash(baseline) != originalHash or fileHash(restored) != originalHash:
        raise RehearsalError('baseline_or_source_changed')
    report = {'schemaVersion': '55', 'storageVersion': first['storageVersion'], 'alreadyMigratedAtStart': first['alreadyMigrated'],
        'idempotent': True, 'integrityChecked': True, 'sourceUnchanged': True, 'restoredCopyMatchesBaseline': True,
        'oldBinaryRollbackTested': False, 'filesystemAclVerified': False, 'networkAccess': False, 'runtimeReplaced': False, 'baselineSha256': originalHash,
        'candidateSha256': fileHash(candidate), 'cloudExecutionAvailable': False}
    with (output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    (output / 'report.json').chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--actor-id', type=int, required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(rehearse(args.source_db, args.output_dir, args.actor_id, confirmOfflineCopy=args.confirm_offline_copy)))
        return 0
    except CloudConnectionError as error:
        print(json.dumps({'ok': False, 'reasonCode': error.code, 'runtimeReplaced': False}), file=sys.stderr)
    except RehearsalError as error:
        print(json.dumps({'ok': False, 'reasonCode': str(error), 'runtimeReplaced': False}), file=sys.stderr)
    except (OSError, sqlite3.Error, ValueError):
        print(json.dumps({'ok': False, 'reasonCode': 'copy_rehearsal_failed', 'runtimeReplaced': False}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
