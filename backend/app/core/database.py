"""数据库基础设施模块。

负责初始化 SQLite 数据库和基础表结构。业务层不得直接复制数据库初始化逻辑。
"""

from pathlib import Path
from contextlib import closing
import sqlite3

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.service.media_source_secret import (
    CURRENT_CREDENTIAL_VERSION,
    LEGACY_CREDENTIAL_VERSION,
    credential_version_for_secret,
    migrate_secret,
    decrypt_secret,
)

logger = get_logger(__name__)

CURRENT_SCHEMA_VERSION = 38


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    if column_name not in _column_names(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        "INSERT INTO app_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _ensure_system_settings_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS system_settings "
        "(key TEXT PRIMARY KEY, "
        "category TEXT NOT NULL, "
        "value TEXT NOT NULL, "
        "description TEXT, "
        "sensitive INTEGER NOT NULL DEFAULT 0, "
        "operator TEXT NOT NULL DEFAULT 'system', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )


def _ensure_page_test_results_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS page_test_results "
        "(page_key TEXT PRIMARY KEY, "
        "config_snapshot TEXT NOT NULL, "
        "config_hash TEXT NOT NULL, "
        "v4_result TEXT NOT NULL, "
        "v3_result TEXT NOT NULL, "
        "effective_channel TEXT NOT NULL, "
        "tested_at TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )


def _ensure_imdb_test_result_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS imdb_test_result "
        "(id INTEGER PRIMARY KEY, "
        "connection_status TEXT NOT NULL, "
        "response_time INTEGER, "
        "error_message TEXT, "
        "config_snapshot TEXT NOT NULL, "
        "test_time TEXT NOT NULL, "
        "is_valid INTEGER NOT NULL DEFAULT 1)"
    )


def _ensure_external_submission_blocks_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS external_submission_blocks "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_module TEXT NOT NULL, "
        "source_record_id INTEGER NOT NULL, "
        "file_name TEXT NOT NULL, "
        "file_path TEXT NOT NULL, "
        "match_title TEXT, "
        "target_service TEXT NOT NULL, "
        "block_rule_type TEXT NOT NULL, "
        "block_rule_name TEXT NOT NULL, "
        "matched_value_masked TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'blocked', "
        "user_decision TEXT, "
        "override_reason TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "decided_at TEXT, "
        "operator TEXT)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_submission_blocks_status "
        "ON external_submission_blocks(status, target_service, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_submission_blocks_source "
        "ON external_submission_blocks(source_module, source_record_id, target_service)"
    )


def _ensure_scan_file_index_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scan_file_index "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "media_source_id INTEGER NOT NULL, "
        "file_path TEXT NOT NULL, "
        "normalized_path TEXT NOT NULL, "
        "file_name TEXT NOT NULL, "
        "extension TEXT NOT NULL, "
        "file_size INTEGER NOT NULL, "
        "modified_at TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, "
        "last_scan_job_id INTEGER, "
        "last_seen_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "rename_preview_id INTEGER, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY(media_source_id) REFERENCES media_sources(id), "
        "FOREIGN KEY(last_scan_job_id) REFERENCES scan_jobs(id), "
        "FOREIGN KEY(rename_preview_id) REFERENCES rename_previews(id), "
        "UNIQUE(media_source_id, normalized_path))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_file_index_source_status "
        "ON scan_file_index(media_source_id, status, updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_file_index_fingerprint "
        "ON scan_file_index(media_source_id, fingerprint)"
    )


def _ensure_users_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL UNIQUE, "
        "display_name TEXT NOT NULL, "
        "password_hash TEXT NOT NULL, "
        "permissions_json TEXT NOT NULL, "
        "must_change_password INTEGER NOT NULL DEFAULT 0, "
        "password_changed_at TEXT, "
        "enabled INTEGER NOT NULL DEFAULT 1, "
        "last_login_at TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_enabled "
        "ON users(enabled)"
    )
    columns = _column_names(connection, "users")
    if "permissions_json" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "must_change_password" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
    if "password_changed_at" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT")


def _ensure_user_sessions_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS user_sessions "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "token_hash TEXT NOT NULL UNIQUE, "
        "expires_at TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "revoked_at TEXT, "
        "FOREIGN KEY(user_id) REFERENCES users(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_expires "
        "ON user_sessions(user_id, expires_at, revoked_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry_cleanup "
        "ON user_sessions(julianday(expires_at), id)"
    )


def _ensure_audit_events_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS audit_events "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, "
        "actor_id INTEGER, "
        "actor_name TEXT, "
        "target_type TEXT NOT NULL, "
        "target_id TEXT, "
        "action TEXT NOT NULL, "
        "result TEXT NOT NULL, "
        "summary TEXT NOT NULL, "
        "detail_json TEXT, "
        "ip_address TEXT, "
        "user_agent TEXT, "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY(actor_id) REFERENCES users(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_query "
        "ON audit_events(event_type, result, actor_name, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_target "
        "ON audit_events(target_type, target_id, created_at)"
    )


def _ensure_operation_logs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS operation_logs "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_type TEXT NOT NULL, "
        "task_id INTEGER NOT NULL, "
        "level TEXT NOT NULL, "
        "stage TEXT NOT NULL, "
        "message TEXT NOT NULL, "
        "progress_current INTEGER, "
        "progress_total INTEGER, "
        "detail_json TEXT, "
        "approx_bytes INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_logs_task "
        "ON operation_logs(task_type, task_id, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_logs_created "
        "ON operation_logs(created_at)"
    )


def _ensure_task_archives_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS task_archives "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_type TEXT NOT NULL, "
        "task_id INTEGER NOT NULL, "
        "archived_at TEXT NOT NULL, "
        "archived_by TEXT, "
        "reason TEXT, "
        "UNIQUE(task_type, task_id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_archives_task "
        "ON task_archives(task_type, task_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_archives_archived_at "
        "ON task_archives(archived_at)"
    )


def _ensure_remote_operation_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS remote_operation_locks "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "media_source_id INTEGER NOT NULL, "
        "lock_key TEXT NOT NULL UNIQUE, "
        "owner TEXT, "
        "task_type TEXT, "
        "task_id INTEGER, "
        "lease_token TEXT NOT NULL, "
        "heartbeat_at TEXT, "
        "expires_at TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY(media_source_id) REFERENCES media_sources(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_operation_locks_source "
        "ON remote_operation_locks(media_source_id, status, expires_at)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS remote_operation_items "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "media_source_id INTEGER NOT NULL, "
        "operation_type TEXT NOT NULL, "
        "idempotency_key TEXT NOT NULL UNIQUE, "
        "source_path TEXT NOT NULL, "
        "target_path TEXT, "
        "source_version TEXT, "
        "target_version TEXT, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "error_message TEXT, "
        "recovery_json TEXT, "
        "recovery_generation INTEGER NOT NULL DEFAULT 0, "
        "recovery_token TEXT, "
        "recovery_phase TEXT NOT NULL DEFAULT 'idle', "
        "recovery_heartbeat_at TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY(media_source_id) REFERENCES media_sources(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_operation_items_source "
        "ON remote_operation_items(media_source_id, operation_type, status)"
    )


def _ensure_metadata_provider_configs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata_provider_configs "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "provider TEXT NOT NULL UNIQUE, "
        "enabled INTEGER NOT NULL DEFAULT 0, "
        "priority INTEGER NOT NULL DEFAULT 100, "
        "base_url TEXT, "
        "api_key_encrypted TEXT, "
        "timeout_seconds INTEGER NOT NULL DEFAULT 10, "
        "max_retries INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_metadata_provider_configs_priority "
        "ON metadata_provider_configs(enabled, priority, provider)"
    )


def _seed_metadata_provider_configs(connection: sqlite3.Connection) -> None:
    now = "1970-01-01T00:00:00+00:00"
    defaults = [
        ("tmdb", 1, 1, "https://api.themoviedb.org/3", 15, 1),
        ("imdb", 0, 2, "https://www.imdb.com", 10, 1),
        ("bangumi", 0, 30, "https://api.bgm.tv", 10, 1),
        ("tvdb", 0, 40, "https://api4.thetvdb.com/v4", 10, 1),
        ("douban_proxy", 0, 50, "", 10, 1),
    ]
    for provider, enabled, priority, base_url, timeout_seconds, max_retries in defaults:
        connection.execute(
            "INSERT INTO metadata_provider_configs "
            "(provider, enabled, priority, base_url, timeout_seconds, max_retries, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO NOTHING",
            (provider, enabled, priority, base_url, timeout_seconds, max_retries, now, now),
        )


def _ensure_rename_rollback_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rename_rollback_plans "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "operation_id INTEGER NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'draft', "
        "item_count INTEGER NOT NULL DEFAULT 0, "
        "executable_count INTEGER NOT NULL DEFAULT 0, "
        "conflict_count INTEGER NOT NULL DEFAULT 0, "
        "created_by TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY(operation_id) REFERENCES rename_operations(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rename_rollback_plans_operation "
        "ON rename_rollback_plans(operation_id, status, created_at)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rename_rollback_items "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "plan_id INTEGER NOT NULL, "
        "operation_item_id INTEGER NOT NULL, "
        "current_path TEXT NOT NULL, "
        "rollback_path TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "message TEXT, "
        "executed_at TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY(plan_id) REFERENCES rename_rollback_plans(id), "
        "FOREIGN KEY(operation_item_id) REFERENCES rename_operation_items(id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rename_rollback_items_plan "
        "ON rename_rollback_items(plan_id, status)"
    )


def _ensure_m9_governance_tables(connection: sqlite3.Connection) -> None:
    _ensure_users_table(connection)
    _ensure_user_sessions_table(connection)
    _ensure_audit_events_table(connection)
    _ensure_rename_rollback_tables(connection)


def _ensure_scan_job_incremental_columns(connection: sqlite3.Connection) -> None:
    if "scan_jobs" not in _table_names(connection):
        return
    _ensure_column(connection, "scan_jobs", "scan_mode", "TEXT NOT NULL DEFAULT 'full'")
    _ensure_column(connection, "scan_jobs", "new_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "scan_jobs", "changed_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "scan_jobs", "skipped_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "scan_jobs", "missing_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "scan_jobs", "indexed_count", "INTEGER NOT NULL DEFAULT 0")


def _ensure_rename_preview_metadata_columns(connection: sqlite3.Connection) -> None:
    if "rename_previews" not in _table_names(connection):
        return
    _ensure_column(connection, "rename_previews", "metadata_source", "TEXT")
    _ensure_column(connection, "rename_previews", "metadata_match_status", "TEXT")
    _ensure_column(connection, "rename_previews", "metadata_match_score", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "rename_previews", "metadata_message", "TEXT")
    _ensure_column(connection, "rename_previews", "metadata_candidates_json", "TEXT")


def _ensure_rename_preview_title_source_columns(connection: sqlite3.Connection) -> None:
    if "rename_previews" not in _table_names(connection):
        return
    _ensure_column(connection, "rename_previews", "title_source", "TEXT NOT NULL DEFAULT 'file_name'")
    _ensure_column(connection, "rename_previews", "parent_folder_title", "TEXT")
    _ensure_column(connection, "rename_previews", "recognition_mode", "TEXT NOT NULL DEFAULT 'parent_folder_fallback'")
    _ensure_column(connection, "rename_previews", "title_conflict_message", "TEXT")


def _ensure_rename_preview_naming_template_columns(connection: sqlite3.Connection) -> None:
    if "rename_previews" not in _table_names(connection):
        return
    _ensure_column(connection, "rename_previews", "naming_template_type", "TEXT")
    _ensure_column(connection, "rename_previews", "naming_template_version", "INTEGER")
    _ensure_column(connection, "rename_previews", "naming_template_updated_at", "TEXT")


def _ensure_pending_files_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS pending_files "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "media_source_id INTEGER NOT NULL, "
        "scan_job_id INTEGER NOT NULL, "
        "file_path TEXT NOT NULL, "
        "file_name TEXT NOT NULL, "
        "extension TEXT NOT NULL, "
        "file_size INTEGER NOT NULL, "
        "reason TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY(media_source_id) REFERENCES media_sources(id), "
        "FOREIGN KEY(scan_job_id) REFERENCES scan_jobs(id))"
    )


def _ensure_media_source_shared_path_columns(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "media_sources", "path_type", "TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(connection, "media_sources", "protocol", "TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(connection, "media_sources", "host", "TEXT")
    _ensure_column(connection, "media_sources", "share_name", "TEXT")
    _ensure_column(connection, "media_sources", "domain", "TEXT")
    _ensure_column(connection, "media_sources", "username", "TEXT")
    _ensure_column(connection, "media_sources", "encrypted_secret", "TEXT")
    _ensure_column(connection, "media_sources", "port", "INTEGER")
    _ensure_column(connection, "media_sources", "remark", "TEXT")
    _ensure_column(connection, "media_sources", "nfs_host", "TEXT")
    _ensure_column(connection, "media_sources", "nfs_export", "TEXT")
    _ensure_column(connection, "media_sources", "nfs_version", "TEXT")
    _ensure_column(connection, "media_sources", "nfs_options", "TEXT")
    _ensure_column(connection, "media_sources", "local_mount_path", "TEXT")


def _ensure_media_source_remote_protocol_columns(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "media_sources", "protocol_endpoint", "TEXT")
    _ensure_column(connection, "media_sources", "auth_type", "TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(connection, "media_sources", "credential_version", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "media_sources", "remote_root", "TEXT")
    _ensure_column(connection, "media_sources", "capability_snapshot_json", "TEXT")
    _ensure_column(connection, "media_sources", "trusted_certificate_fingerprint", "TEXT")


def _migrate_media_source_secrets(settings: AppSettings, connection: sqlite3.Connection) -> None:
    if "media_sources" not in _table_names(connection):
        return
    columns = _column_names(connection, "media_sources")
    if "encrypted_secret" not in columns or "credential_version" not in columns:
        return

    rows = connection.execute(
        "SELECT id, encrypted_secret FROM media_sources WHERE encrypted_secret IS NOT NULL "
        "AND encrypted_secret <> ''"
    ).fetchall()
    for source_id, encrypted_secret in rows:
        try:
            migrated_secret = migrate_secret(settings, str(encrypted_secret))
            credential_version = credential_version_for_secret(migrated_secret)
        except ValueError as exc:
            raise ValueError(f"媒体源凭据迁移失败 source_id={source_id}，请保留数据库并检查原路径和密钥") from exc
        connection.execute(
            "UPDATE media_sources SET encrypted_secret = ?, credential_version = ? WHERE id = ?",
            (migrated_secret, credential_version, source_id),
        )
    connection.execute(
        "UPDATE media_sources SET credential_version = ? "
        "WHERE encrypted_secret IS NULL OR encrypted_secret = ''",
        (LEGACY_CREDENTIAL_VERSION,),
    )


def _migrateProtectedCredentials(settings: AppSettings, connection: sqlite3.Connection) -> None:
    # 一次事务覆盖所有持久化凭据；任一失败不提交部分密文。
    from app.service.media_source_secret import encrypt_secret
    from app.service.settings_service import SETTING_DEFINITIONS
    _migrate_media_source_secrets(settings, connection)
    for provider, encrypted in connection.execute(
        "SELECT provider, api_key_encrypted FROM metadata_provider_configs WHERE api_key_encrypted IS NOT NULL AND api_key_encrypted <> ''"
    ).fetchall():
        migrated = migrate_secret(settings, encrypted)
        decrypt_secret(settings, migrated)
        connection.execute("UPDATE metadata_provider_configs SET api_key_encrypted = ? WHERE provider = ?", (migrated, provider))
    for key, value in connection.execute("SELECT key, value FROM system_settings").fetchall():
        definition = SETTING_DEFINITIONS.get(key)
        if definition and (definition.sensitive or key == "ai.provider_profiles"):
            if value.startswith("v3:"):
                decrypt_secret(settings, value)
            else:
                connection.execute("UPDATE system_settings SET value = ? WHERE key = ?", (encrypt_secret(settings, value), key))
    for (encrypted,) in connection.execute("SELECT encrypted_secret FROM media_sources WHERE encrypted_secret LIKE 'v3:%'").fetchall():
        decrypt_secret(settings, encrypted)


def _run_migrations(settings: AppSettings, connection: sqlite3.Connection) -> None:
    version = _schema_version(connection)

    if version < 4:
        _ensure_system_settings_table(connection)
        _ensure_rename_preview_metadata_columns(connection)
        _set_schema_version(connection, 4)

    if version < 5:
        _ensure_pending_files_table(connection)
        _set_schema_version(connection, 5)

    if version < 6:
        _ensure_media_source_shared_path_columns(connection)
        connection.execute(
            "UPDATE media_sources SET path_type = 'local' "
            "WHERE path_type IS NULL OR path_type = ''"
        )
        connection.execute(
            "UPDATE media_sources SET protocol = 'local' "
            "WHERE protocol IS NULL OR protocol = ''"
        )
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 7:
        _ensure_page_test_results_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 8:
        _ensure_imdb_test_result_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 9:
        _ensure_external_submission_blocks_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 10:
        _ensure_rename_preview_metadata_columns(connection)
        connection.execute(
            "UPDATE rename_previews SET status = 'generated' "
            "WHERE status IN ('tmdb_matched', 'tmdb_selected')"
        )
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 11:
        _ensure_scan_job_incremental_columns(connection)
        _ensure_scan_file_index_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 12:
        _ensure_rename_preview_title_source_columns(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 13:
        _ensure_rename_preview_naming_template_columns(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 14:
        _ensure_m9_governance_tables(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 15:
        _ensure_users_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 16:
        _ensure_operation_logs_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 17:
        _ensure_task_archives_table(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 18:
        _ensure_metadata_provider_configs_table(connection)
        _seed_metadata_provider_configs(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 19:
        _ensure_media_source_remote_protocol_columns(connection)
        connection.execute(
            "UPDATE media_sources SET auth_type = 'none' "
            "WHERE auth_type IS NULL OR auth_type = ''"
        )
        connection.execute(
            "UPDATE media_sources SET credential_version = 1 "
            "WHERE credential_version IS NULL OR credential_version < 1"
        )
        _ensure_remote_operation_tables(connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    if version < 20:
        _ensure_media_source_remote_protocol_columns(connection)
        _migrate_media_source_secrets(settings, connection)
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _migrateProtectedCredentials(settings, connection)
    if version < 21:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "rename_operation_items", "snapshot_json", "TEXT")
    if version < 22:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    connection.execute(
        "CREATE TABLE IF NOT EXISTS local_file_actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, media_source_id INTEGER NOT NULL, "
        "action_type TEXT NOT NULL, record_id INTEGER NOT NULL, "
        "source_path TEXT NOT NULL, target_path TEXT NOT NULL, "
        "source_key TEXT NOT NULL, target_key TEXT NOT NULL, "
        "identity_json TEXT NOT NULL, status TEXT NOT NULL, message TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_file_actions_source_status "
        "ON local_file_actions(media_source_id, status)"
    )
    if version < 23:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "local_file_actions", "target_identity_json", "TEXT")
    if version < 24:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "local_file_actions", "source_entry_name", "TEXT")
    _ensure_column(connection, "local_file_actions", "target_entry_name", "TEXT")
    _ensure_column(
        connection,
        "local_file_actions",
        "action_phase",
        "TEXT NOT NULL DEFAULT 'intent_recorded'",
    )
    if version < 37:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "rename_rollback_items", "snapshot_json", "TEXT")
    if version < 25:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "media_sources", "deleted_at", "TEXT")
    _ensure_column(connection, "media_sources", "retired_root", "TEXT")
    _ensure_column(connection, "scan_jobs", "source_invalidated_at", "TEXT")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS media_source_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, media_source_id INTEGER NOT NULL, "
        "event_type TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_source_history_source ON media_source_history(media_source_id, id)"
    )
    if version < 26:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    connection.execute(
        "CREATE TABLE IF NOT EXISTS file_action_audit_intents ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT NOT NULL, task_id INTEGER NOT NULL, "
        "context_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
        "last_error_type TEXT, outcome_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_file_action_audit_intents_status ON file_action_audit_intents(status, id)"
    )
    _ensure_column(connection, "file_action_audit_intents", "outcome_json", "TEXT")
    if version < 27:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(
        connection,
        "file_action_audit_intents",
        "request_phase",
        "TEXT NOT NULL DEFAULT 'accepted'",
    )
    _ensure_column(connection, "file_action_audit_intents", "closed_at", "TEXT")
    _ensure_column(connection, "file_action_audit_intents", "closed_by", "TEXT")
    _ensure_column(connection, "file_action_audit_intents", "close_reason", "TEXT")
    _ensure_column(connection, "file_action_audit_intents", "close_evidence", "TEXT")
    if version < 38:
        connection.execute(
            "UPDATE file_action_audit_intents SET request_phase = "
            "CASE WHEN status='completed' THEN 'completed' ELSE 'legacy_unknown' END"
        )
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "scan_jobs", "policy_json", "TEXT")
    _ensure_column(connection, "scan_jobs", "actor_id", "INTEGER")
    _ensure_column(connection, "scan_jobs", "actor_name", "TEXT")
    if version < 28:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    _ensure_column(connection, "scan_jobs", "execution_token", "TEXT")
    _ensure_column(connection, "scan_jobs", "execution_pid", "INTEGER")
    _ensure_column(connection, "scan_jobs", "execution_expires_at", "TEXT")
    _ensure_column(connection, "scan_jobs", "audit_context_json", "TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_scan_jobs_queue ON scan_jobs(status, id)")
    if version < 29:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    connection.execute("CREATE TABLE IF NOT EXISTS external_submission_authorizations ("
        "id TEXT PRIMARY KEY, block_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, snapshot_json TEXT NOT NULL, context_json TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'prepared', outcome_json TEXT, created_at TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, consumed_at TEXT, completed_at TEXT)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_external_authorizations_block "
                       "ON external_submission_authorizations(block_id,created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_external_authorizations_pending "
                       "ON external_submission_authorizations(status,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_media_files_job_modified "
                       "ON media_files(scan_job_id,modified_at,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_media_files_source_job "
                       "ON media_files(media_source_id,scan_job_id,id)")
    if version < 30:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    for column, definition in (
        ("execution_token", "TEXT"), ("execution_pid", "INTEGER"),
        ("execution_expires_at", "TEXT"), ("actor_id", "INTEGER"),
        ("actor_name", "TEXT"), ("audit_context_json", "TEXT"),
        ("audit_intent_id", "INTEGER"), ("error_message", "TEXT"),
    ):
        _ensure_column(connection, "rename_operations", column, definition)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rename_operations_queue ON rename_operations(status,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rename_items_operation ON rename_operation_items(operation_id,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rename_items_preview ON rename_operation_items(rename_preview_id,operation_id)")
    if version < 31:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    for column, definition in (
        ("execution_token", "TEXT"), ("execution_pid", "INTEGER"),
        ("execution_expires_at", "TEXT"), ("actor_id", "INTEGER"),
        ("actor_name", "TEXT"), ("audit_context_json", "TEXT"),
        ("audit_intent_id", "INTEGER"), ("error_message", "TEXT"),
        ("rolled_back_count", "INTEGER NOT NULL DEFAULT 0"), ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _ensure_column(connection, "rename_rollback_plans", column, definition)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rollback_plans_queue ON rename_rollback_plans(status,updated_at,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rollback_items_operation ON rename_rollback_items(operation_item_id,plan_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rename_queue_order ON rename_operations(status,updated_at,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rename_execution_slot ON rename_operations(id) WHERE execution_token IS NOT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rollback_execution_slot ON rename_rollback_plans(id) WHERE execution_token IS NOT NULL")
    if version < 32:
        # 历史计数只来源于已持久化明细，不推断旧任务的操作者或执行授权。
        connection.execute("UPDATE rename_rollback_plans SET rolled_back_count=(SELECT COUNT(*) FROM rename_rollback_items "
            "WHERE plan_id=rename_rollback_plans.id AND status='rolled_back'),failed_count=(SELECT COUNT(*) "
            "FROM rename_rollback_items WHERE plan_id=rename_rollback_plans.id AND status='failed')")
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    connection.execute("CREATE TABLE IF NOT EXISTS external_request_sites (site_key TEXT PRIMARY KEY, "
        "last_started_at REAL NOT NULL, spacing_seconds REAL NOT NULL, updated_at REAL NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_external_sites_updated ON external_request_sites(updated_at)")
    connection.execute("CREATE TABLE IF NOT EXISTS external_request_leases (token TEXT PRIMARY KEY, site_key TEXT NOT NULL, "
        "pid INTEGER NOT NULL, global_limit INTEGER NOT NULL, site_limit INTEGER NOT NULL, started_at REAL NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_external_leases_site ON external_request_leases(site_key)")
    if version < 33:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    connection.execute("CREATE TABLE IF NOT EXISTS pending_move_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "request_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, target_directory TEXT NOT NULL, "
        "target_identity_json TEXT, status TEXT NOT NULL DEFAULT 'queued', total_count INTEGER NOT NULL, "
        "moved_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0, actor_id INTEGER, "
        "actor_name TEXT, audit_context_json TEXT, audit_intent_id INTEGER, execution_token TEXT, "
        "execution_expires_at TEXT, execution_pid INTEGER, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS pending_move_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL REFERENCES pending_move_tasks(id), pending_file_id INTEGER NOT NULL, "
        "media_source_id INTEGER NOT NULL, source_path TEXT NOT NULL, target_path TEXT NOT NULL, "
        "snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready', message TEXT, "
        "UNIQUE(task_id,pending_file_id))")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_pending_move_queue ON pending_move_tasks(status,updated_at,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_pending_move_tokens ON pending_move_tasks(execution_token) WHERE execution_token IS NOT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_pending_move_item_source ON pending_move_items(media_source_id,task_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_pending_move_item_file ON pending_move_items(pending_file_id,task_id)")
    if version < 34:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    for column, definition in (
        ("recovery_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("recovery_token", "TEXT"),
        ("recovery_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("recovery_heartbeat_at", "TEXT"),
    ):
        _ensure_column(connection, "remote_operation_items", column, definition)
    if version < 35:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)

    for table in ("rename_operations", "rename_rollback_plans", "pending_move_tasks"):
        _ensure_column(connection, table, "execution_instance_id", "TEXT")
        _ensure_column(connection, table, "execution_started_at", "TEXT")
    if version < 36:
        _set_schema_version(connection, CURRENT_SCHEMA_VERSION)


def ensure_database(settings: AppSettings) -> Path:
    """确保 SQLite 数据库和基础元数据表存在。

    Args:
        settings: 应用运行配置。

    Returns:
        SQLite 数据库文件路径。
    """

    logger.info("开始检查数据库目录和基础表")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        _ensure_system_settings_table(connection)
        _ensure_page_test_results_table(connection)
        _ensure_imdb_test_result_table(connection)
        _ensure_external_submission_blocks_table(connection)
        _ensure_scan_file_index_table(connection)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS media_sources "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, "
            "path TEXT NOT NULL UNIQUE, "
            "path_type TEXT NOT NULL DEFAULT 'local', "
            "protocol TEXT NOT NULL DEFAULT 'local', "
            "host TEXT, "
            "share_name TEXT, "
            "domain TEXT, "
            "username TEXT, "
            "encrypted_secret TEXT, "
            "port INTEGER, "
            "remark TEXT, "
            "nfs_host TEXT, "
            "nfs_export TEXT, "
            "nfs_version TEXT, "
            "nfs_options TEXT, "
            "local_mount_path TEXT, "
            "protocol_endpoint TEXT, "
            "auth_type TEXT NOT NULL DEFAULT 'none', "
            "credential_version INTEGER NOT NULL DEFAULT 1, "
            "remote_root TEXT, "
            "capability_snapshot_json TEXT, "
            "trusted_certificate_fingerprint TEXT, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scan_jobs "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_source_id INTEGER NOT NULL, "
            "status TEXT NOT NULL, "
            "batch_size INTEGER NOT NULL, "
            "batch_interval_seconds REAL NOT NULL, "
            "scan_mode TEXT NOT NULL DEFAULT 'full', "
            "scanned_count INTEGER NOT NULL DEFAULT 0, "
            "video_count INTEGER NOT NULL DEFAULT 0, "
            "warning_count INTEGER NOT NULL DEFAULT 0, "
            "new_count INTEGER NOT NULL DEFAULT 0, "
            "changed_count INTEGER NOT NULL DEFAULT 0, "
            "skipped_count INTEGER NOT NULL DEFAULT 0, "
            "missing_count INTEGER NOT NULL DEFAULT 0, "
            "indexed_count INTEGER NOT NULL DEFAULT 0, "
            "error_message TEXT, "
            "started_at TEXT, "
            "ended_at TEXT, "
            "created_at TEXT NOT NULL, "
            "FOREIGN KEY(media_source_id) REFERENCES media_sources(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS media_files "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_source_id INTEGER NOT NULL, "
            "scan_job_id INTEGER NOT NULL, "
            "file_path TEXT NOT NULL, "
            "file_name TEXT NOT NULL, "
            "extension TEXT NOT NULL, "
            "file_size INTEGER NOT NULL, "
            "modified_at TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "FOREIGN KEY(media_source_id) REFERENCES media_sources(id), "
            "FOREIGN KEY(scan_job_id) REFERENCES scan_jobs(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_previews "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_file_id INTEGER NOT NULL UNIQUE, "
            "media_type TEXT NOT NULL, "
            "parsed_title TEXT NOT NULL, "
            "parsed_year INTEGER, "
            "season INTEGER, "
            "episode INTEGER, "
            "original_extension TEXT NOT NULL, "
            "suggested_name TEXT NOT NULL, "
            "edited_name TEXT, "
            "metadata_source TEXT, "
            "metadata_match_status TEXT, "
            "metadata_match_score INTEGER NOT NULL DEFAULT 0, "
            "metadata_message TEXT, "
            "metadata_candidates_json TEXT, "
            "title_source TEXT NOT NULL DEFAULT 'file_name', "
            "parent_folder_title TEXT, "
            "recognition_mode TEXT NOT NULL DEFAULT 'parent_folder_fallback', "
            "title_conflict_message TEXT, "
            "naming_template_type TEXT, "
            "naming_template_version INTEGER, "
            "naming_template_updated_at TEXT, "
            "status TEXT NOT NULL, "
            "message TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "FOREIGN KEY(media_file_id) REFERENCES media_files(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_operations "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "status TEXT NOT NULL, "
            "mode TEXT NOT NULL, "
            "total_count INTEGER NOT NULL DEFAULT 0, "
            "ready_count INTEGER NOT NULL DEFAULT 0, "
            "conflict_count INTEGER NOT NULL DEFAULT 0, "
            "renamed_count INTEGER NOT NULL DEFAULT 0, "
            "failed_count INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_operation_items "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "operation_id INTEGER NOT NULL, "
            "rename_preview_id INTEGER NOT NULL, "
            "source_path TEXT NOT NULL, "
            "target_path TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "message TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "FOREIGN KEY(operation_id) REFERENCES rename_operations(id), "
            "FOREIGN KEY(rename_preview_id) REFERENCES rename_previews(id))"
        )
        _ensure_pending_files_table(connection)
        _ensure_scan_job_incremental_columns(connection)
        _ensure_rename_preview_title_source_columns(connection)
        _ensure_rename_preview_naming_template_columns(connection)
        _ensure_m9_governance_tables(connection)
        _ensure_operation_logs_table(connection)
        _ensure_task_archives_table(connection)
        _ensure_metadata_provider_configs_table(connection)
        _seed_metadata_provider_configs(connection)
        _ensure_remote_operation_tables(connection)
        _run_migrations(settings, connection)
        connection.commit()
    logger.info("数据库初始化完成: %s", settings.database_path)
    return settings.database_path
