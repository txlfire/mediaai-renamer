"""数据库基础设施模块。

负责初始化 SQLite 数据库和基础表结构。业务层不得直接复制数据库初始化逻辑。
"""

from pathlib import Path
from contextlib import closing
import sqlite3

from app.core.config import AppSettings
from app.core.logger import get_logger

logger = get_logger(__name__)

CURRENT_SCHEMA_VERSION = 14


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


def _run_migrations(connection: sqlite3.Connection) -> None:
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
        _run_migrations(connection)
        connection.commit()
    logger.info("数据库初始化完成: %s", settings.database_path)
    return settings.database_path
