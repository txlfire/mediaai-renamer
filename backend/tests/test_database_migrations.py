"""Database migration tests."""

import sqlite3
import tempfile
import unittest
import base64
import hashlib
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service.media_source_secret import decrypt_secret


class DatabaseMigrationTest(unittest.TestCase):
    """SQLite schema migration behavior."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_schema_34_remote_operations_gain_recovery_fencing_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '34')")
                connection.execute(
                    "CREATE TABLE remote_operation_items ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, media_source_id INTEGER NOT NULL, "
                    "operation_type TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, "
                    "source_path TEXT NOT NULL, target_path TEXT, source_version TEXT, target_version TEXT, "
                    "status TEXT NOT NULL DEFAULT 'pending', error_message TEXT, recovery_json TEXT, "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO remote_operation_items "
                    "(media_source_id, operation_type, idempotency_key, source_path, status, created_at, updated_at) "
                    "VALUES (1, 'rename', 'legacy', '/source', 'failed', 'now', 'now')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(remote_operation_items)")
                }
                migrated = connection.execute(
                    "SELECT recovery_generation, recovery_token, recovery_phase, recovery_heartbeat_at "
                    "FROM remote_operation_items WHERE idempotency_key = 'legacy'"
                ).fetchone()
                schemaVersion = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertTrue({
                "recovery_generation", "recovery_token", "recovery_phase", "recovery_heartbeat_at"
            }.issubset(columns))
            self.assertEqual((0, None, "idle", None), migrated)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schemaVersion)

    def test_schema_35_file_tasks_gain_execution_owner_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                for table in ("rename_operations", "rename_rollback_plans", "pending_move_tasks"):
                    connection.execute(f"ALTER TABLE {table} DROP COLUMN execution_instance_id")
                    connection.execute(f"ALTER TABLE {table} DROP COLUMN execution_started_at")
                connection.execute("UPDATE app_meta SET value='35' WHERE key='schema_version'")
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                for table in ("rename_operations", "rename_rollback_plans", "pending_move_tasks"):
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                    self.assertTrue({"execution_instance_id", "execution_started_at"}.issubset(columns))
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute(
                    "SELECT value FROM app_meta WHERE key='schema_version'"
                ).fetchone()[0])

    def test_v27_scan_context_upgrade_preserves_history_without_guessing_actor(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                for column in ("policy_json", "actor_id", "actor_name"):
                    connection.execute(f"ALTER TABLE scan_jobs DROP COLUMN {column}")
                connection.execute("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES(1,'completed',2,0,'old')")
                connection.execute("UPDATE app_meta SET value='27' WHERE key='schema_version'")
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute("SELECT status,policy_json,actor_id,actor_name FROM scan_jobs").fetchone()
                self.assertEqual(("completed", None, None, None), row)
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0])

    def test_v28_queue_columns_are_nullable_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                # 模拟v28时一并移除后续版本新增、依赖execution_token的索引。
                connection.execute("DROP INDEX IF EXISTS idx_directory_active_scan")
                for column in ("execution_token", "execution_expires_at", "audit_context_json", "execution_pid"):
                    connection.execute(f"ALTER TABLE scan_jobs DROP COLUMN {column}")
                connection.execute("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES(1,'completed',1,0,'old')")
                connection.execute("UPDATE app_meta SET value='28' WHERE key='schema_version'")
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(("completed", None, None, None), connection.execute(
                    "SELECT status,execution_token,execution_expires_at,audit_context_json FROM scan_jobs").fetchone())

    def legacy_encrypt_secret(self, settings: AppSettings, secret: str) -> str:
        raw = secret.encode("utf-8")
        key = hashlib.sha256(str(settings.database_path).encode("utf-8")).digest()
        encrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(raw))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")

    def test_v26_adds_audit_intents_idempotently_without_modifying_history(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("DROP TABLE file_action_audit_intents")
                connection.execute("UPDATE app_meta SET value = '26' WHERE key = 'schema_version'")
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(file_action_audit_intents)")}
                self.assertIn("outcome_json", columns)
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM file_action_audit_intents").fetchone()[0])
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()[0])

    def test_v21_rename_items_gain_nullable_snapshot_without_backfilling_old_plans(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("ALTER TABLE rename_operation_items DROP COLUMN snapshot_json")
                connection.execute("UPDATE app_meta SET value = '21' WHERE key = 'schema_version'")
                connection.execute(
                    "INSERT INTO rename_operation_items (operation_id, rename_preview_id, source_path, "
                    "target_path, status, created_at, updated_at) VALUES (1, 1, 'old', 'new', 'ready', 'now', 'now')"
                )
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute("SELECT source_path, snapshot_json FROM rename_operation_items").fetchone()
                version = connection.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(("old", None), row)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), version)

    def test_v22_database_adds_empty_local_action_journal_idempotently(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("DROP TABLE local_file_actions")
                connection.execute("UPDATE app_meta SET value = '22' WHERE key = 'schema_version'")
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM local_file_actions").fetchone()[0])
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'",
                ).fetchone()[0])

    def test_v23_journal_gains_nullable_target_identity_without_losing_intent(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("ALTER TABLE local_file_actions DROP COLUMN target_identity_json")
                connection.execute("UPDATE app_meta SET value = '23' WHERE key = 'schema_version'")
                connection.execute(
                    "INSERT INTO local_file_actions (media_source_id, action_type, record_id, source_path, target_path, "
                    "source_key, target_key, identity_json, status, created_at, updated_at) "
                    "VALUES (1, 'rename', 1, 'source', 'target', 'source', 'target', '[1,2,3,4]', 'prepared', 'now', 'now')"
                )
                connection.commit()
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(("prepared", "[1,2,3,4]", None), connection.execute(
                    "SELECT status, identity_json, target_identity_json FROM local_file_actions",
                ).fetchone())

    def test_v24_rollback_snapshot_is_nullable_and_old_plan_is_preserved(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("ALTER TABLE rename_rollback_items DROP COLUMN snapshot_json")
                connection.execute("UPDATE app_meta SET value = '24' WHERE key = 'schema_version'")
                connection.execute(
                    "INSERT INTO rename_rollback_items (plan_id, operation_item_id, current_path, rollback_path, "
                    "status, created_at, updated_at) VALUES (1, 1, 'new', 'old', 'ready', 'now', 'now')"
                )
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(("new", "ready", None), connection.execute(
                    "SELECT current_path, status, snapshot_json FROM rename_rollback_items",
                ).fetchone())
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'",
                ).fetchone()[0])

    def test_v25_source_lifecycle_columns_do_not_invent_retirement_history(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("ALTER TABLE media_sources DROP COLUMN deleted_at")
                connection.execute("ALTER TABLE media_sources DROP COLUMN retired_root")
                connection.execute("ALTER TABLE scan_jobs DROP COLUMN source_invalidated_at")
                connection.execute("DROP TABLE media_source_history")
                connection.execute("UPDATE app_meta SET value = '25' WHERE key = 'schema_version'")
                connection.execute("INSERT INTO media_sources (name, path, enabled, created_at, updated_at) VALUES ('old', 'old-root', 1, 'now', 'now')")
                connection.commit()
            ensure_database(settings)
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(("old-root", 1, None, None), connection.execute(
                    "SELECT path, enabled, deleted_at, retired_root FROM media_sources",
                ).fetchone())
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM media_source_history").fetchone()[0])

    def test_existing_m3_database_is_migrated_to_current_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "CREATE TABLE rename_previews "
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
                    "status TEXT NOT NULL, "
                    "message TEXT, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL)"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                preview_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_previews)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("system_settings", tables)
            self.assertIn("page_test_results", tables)
            self.assertIn("imdb_test_result", tables)
            self.assertIn("external_submission_blocks", tables)
            self.assertIn("metadata_source", preview_columns)
            self.assertIn("metadata_match_status", preview_columns)
            self.assertIn("metadata_match_score", preview_columns)
            self.assertIn("title_source", preview_columns)
            self.assertIn("parent_folder_title", preview_columns)
            self.assertIn("recognition_mode", preview_columns)
            self.assertIn("title_conflict_message", preview_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_new_database_records_current_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                scan_job_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(scan_jobs)")
                }

            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)
            self.assertIn("page_test_results", tables)
            self.assertIn("imdb_test_result", tables)
            self.assertIn("external_submission_blocks", tables)
            self.assertIn("scan_file_index", tables)
            self.assertIn("users", tables)
            self.assertIn("user_sessions", tables)
            self.assertIn("audit_events", tables)
            self.assertIn("rename_rollback_plans", tables)
            self.assertIn("rename_rollback_items", tables)
            self.assertIn("operation_logs", tables)
            self.assertIn("task_archives", tables)
            self.assertIn("metadata_provider_configs", tables)
            self.assertIn("scan_mode", scan_job_columns)
            self.assertIn("new_count", scan_job_columns)
            self.assertIn("changed_count", scan_job_columns)
            self.assertIn("skipped_count", scan_job_columns)
            self.assertIn("missing_count", scan_job_columns)
            self.assertIn("indexed_count", scan_job_columns)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                user_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)")
                }
            self.assertIn("must_change_password", user_columns)
            self.assertIn("password_changed_at", user_columns)

    def test_existing_m13_database_is_migrated_to_m9_governance_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '13')")
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                user_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)")
                }
                session_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(user_sessions)")
                }
                audit_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(audit_events)")
                }
                rollback_plan_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_rollback_plans)")
                }
                rollback_item_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_rollback_items)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("users", tables)
            self.assertIn("username", user_columns)
            self.assertIn("password_hash", user_columns)
            self.assertIn("permissions_json", user_columns)
            self.assertIn("must_change_password", user_columns)
            self.assertIn("password_changed_at", user_columns)
            self.assertNotIn("role", user_columns)
            self.assertIn("enabled", user_columns)
            self.assertIn("user_sessions", tables)
            self.assertIn("token_hash", session_columns)
            self.assertIn("expires_at", session_columns)
            self.assertIn("revoked_at", session_columns)
            self.assertIn("audit_events", tables)
            self.assertIn("event_type", audit_columns)
            self.assertIn("actor_name", audit_columns)
            self.assertIn("detail_json", audit_columns)
            self.assertIn("rename_rollback_plans", tables)
            self.assertIn("operation_id", rollback_plan_columns)
            self.assertIn("executable_count", rollback_plan_columns)
            self.assertIn("rename_rollback_items", tables)
            self.assertIn("current_path", rollback_item_columns)
            self.assertIn("rollback_path", rollback_item_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m14_database_is_migrated_to_admin_password_prompt_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '14')")
                connection.execute(
                    "CREATE TABLE users "
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
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                user_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("must_change_password", user_columns)
            self.assertIn("password_changed_at", user_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m15_database_is_migrated_to_operation_logs_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '15')")
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(operation_logs)")
                }
                indexes = {
                    row[1] for row in connection.execute("PRAGMA index_list(operation_logs)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("task_type", columns)
            self.assertIn("task_id", columns)
            self.assertIn("level", columns)
            self.assertIn("stage", columns)
            self.assertIn("approx_bytes", columns)
            self.assertIn("idx_operation_logs_task", indexes)
            self.assertIn("idx_operation_logs_created", indexes)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m16_database_is_migrated_to_task_archives_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '16')")
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(task_archives)")
                }
                indexes = {
                    row[1] for row in connection.execute("PRAGMA index_list(task_archives)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("task_type", columns)
            self.assertIn("task_id", columns)
            self.assertIn("archived_at", columns)
            self.assertIn("archived_by", columns)
            self.assertIn("reason", columns)
            self.assertIn("idx_task_archives_task", indexes)
            self.assertIn("idx_task_archives_archived_at", indexes)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m17_database_is_migrated_to_metadata_provider_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '17')")
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(metadata_provider_configs)")
                }
                providers = {
                    row[0]
                    for row in connection.execute("SELECT provider FROM metadata_provider_configs")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("provider", columns)
            self.assertIn("enabled", columns)
            self.assertIn("priority", columns)
            self.assertIn("base_url", columns)
            self.assertIn("api_key_encrypted", columns)
            self.assertEqual({"tmdb", "imdb", "bangumi", "tvdb", "douban_proxy"}, providers)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m11_database_is_migrated_to_parent_title_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '11')")
                connection.execute(
                    "CREATE TABLE rename_previews "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, media_file_id INTEGER NOT NULL UNIQUE, "
                    "media_type TEXT NOT NULL, parsed_title TEXT NOT NULL, parsed_year INTEGER, "
                    "season INTEGER, episode INTEGER, original_extension TEXT NOT NULL, "
                    "suggested_name TEXT NOT NULL, edited_name TEXT, metadata_source TEXT, "
                    "metadata_match_status TEXT, metadata_match_score INTEGER NOT NULL DEFAULT 0, "
                    "metadata_message TEXT, metadata_candidates_json TEXT, status TEXT NOT NULL, "
                    "message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                preview_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_previews)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("title_source", preview_columns)
            self.assertIn("parent_folder_title", preview_columns)
            self.assertIn("recognition_mode", preview_columns)
            self.assertIn("title_conflict_message", preview_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m12_database_is_migrated_to_naming_template_snapshot_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '12')")
                connection.execute(
                    "CREATE TABLE rename_previews "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, media_file_id INTEGER NOT NULL UNIQUE, "
                    "media_type TEXT NOT NULL, parsed_title TEXT NOT NULL, parsed_year INTEGER, "
                    "season INTEGER, episode INTEGER, original_extension TEXT NOT NULL, "
                    "suggested_name TEXT NOT NULL, edited_name TEXT, metadata_source TEXT, "
                    "metadata_match_status TEXT, metadata_match_score INTEGER NOT NULL DEFAULT 0, "
                    "metadata_message TEXT, metadata_candidates_json TEXT, "
                    "title_source TEXT NOT NULL DEFAULT 'file_name', parent_folder_title TEXT, "
                    "recognition_mode TEXT NOT NULL DEFAULT 'parent_folder_fallback', "
                    "title_conflict_message TEXT, status TEXT NOT NULL, message TEXT, "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                preview_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_previews)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("naming_template_type", preview_columns)
            self.assertIn("naming_template_version", preview_columns)
            self.assertIn("naming_template_updated_at", preview_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_media_sources_are_migrated_to_m5_shared_path_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '5')"
                )
                connection.execute(
                    "CREATE TABLE media_sources "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name TEXT NOT NULL, "
                    "path TEXT NOT NULL UNIQUE, "
                    "enabled INTEGER NOT NULL DEFAULT 1, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO media_sources "
                    "(name, path, enabled, created_at, updated_at) "
                    "VALUES ('电影', '/data/media', 1, '2026-06-27T00:00:00+00:00', "
                    "'2026-06-27T00:00:00+00:00')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(media_sources)")
                }
                row = connection.execute(
                    "SELECT path_type, protocol, username, encrypted_secret "
                    "FROM media_sources WHERE name = '电影'"
                ).fetchone()
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("path_type", columns)
            self.assertIn("protocol", columns)
            self.assertIn("username", columns)
            self.assertIn("encrypted_secret", columns)
            self.assertEqual(("local", "local", None, None), row)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_database_is_migrated_to_m11_remote_protocol_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '18')"
                )
                connection.execute(
                    "CREATE TABLE media_sources "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name TEXT NOT NULL, "
                    "path TEXT NOT NULL UNIQUE, "
                    "path_type TEXT NOT NULL DEFAULT 'local', "
                    "protocol TEXT NOT NULL DEFAULT 'local', "
                    "encrypted_secret TEXT, "
                    "enabled INTEGER NOT NULL DEFAULT 1, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO media_sources "
                    "(name, path, path_type, protocol, enabled, created_at, updated_at) "
                    "VALUES ('电影', '/data/media', 'local', 'local', 1, "
                    "'2026-07-13T00:00:00+00:00', '2026-07-13T00:00:00+00:00')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                media_source_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(media_sources)")
                }
                table_names = {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                lock_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(remote_operation_locks)")
                }
                item_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(remote_operation_items)")
                }
                row = connection.execute(
                    "SELECT auth_type, credential_version, remote_root, capability_snapshot_json "
                    "FROM media_sources WHERE name = '电影'"
                ).fetchone()
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("protocol_endpoint", media_source_columns)
            self.assertIn("auth_type", media_source_columns)
            self.assertIn("credential_version", media_source_columns)
            self.assertIn("remote_root", media_source_columns)
            self.assertIn("capability_snapshot_json", media_source_columns)
            self.assertIn("remote_operation_locks", table_names)
            self.assertIn("remote_operation_items", table_names)
            self.assertIn("lease_token", lock_columns)
            self.assertIn("idempotency_key", item_columns)
            self.assertEqual(("none", 1, None, None), row)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m11_remote_schema_migrates_legacy_media_source_secret_to_v2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)
            legacy_secret = self.legacy_encrypt_secret(settings, "old-smb-password")

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '19')"
                )
                connection.execute(
                    "CREATE TABLE media_sources "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name TEXT NOT NULL, "
                    "path TEXT NOT NULL UNIQUE, "
                    "path_type TEXT NOT NULL DEFAULT 'local', "
                    "protocol TEXT NOT NULL DEFAULT 'local', "
                    "username TEXT, "
                    "encrypted_secret TEXT, "
                    "auth_type TEXT NOT NULL DEFAULT 'none', "
                    "credential_version INTEGER NOT NULL DEFAULT 1, "
                    "enabled INTEGER NOT NULL DEFAULT 1, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO media_sources "
                    "(name, path, path_type, protocol, username, encrypted_secret, auth_type, "
                    "credential_version, enabled, created_at, updated_at) "
                    "VALUES ('共享', '\\\\nas\\media', 'unc', 'smb', 'media-user', ?, "
                    "'password', 1, 1, '2026-07-14T00:00:00+00:00', "
                    "'2026-07-14T00:00:00+00:00')",
                    (legacy_secret,),
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute(
                    "SELECT encrypted_secret, credential_version FROM media_sources WHERE name = '共享'"
                ).fetchone()
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertTrue(str(row[0]).startswith("v3:"))
            self.assertNotEqual(legacy_secret, row[0])
            self.assertEqual(3, row[1])
            self.assertEqual("old-smb-password", decrypt_secret(settings, row[0]))
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m6_database_is_migrated_to_page_test_results_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '6')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(page_test_results)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("page_key", columns)
            self.assertIn("config_snapshot", columns)
            self.assertIn("v4_result", columns)
            self.assertIn("v3_result", columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m7_database_is_migrated_to_imdb_test_result_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '7')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(imdb_test_result)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("connection_status", columns)
            self.assertIn("response_time", columns)
            self.assertIn("error_message", columns)
            self.assertIn("config_snapshot", columns)
            self.assertIn("test_time", columns)
            self.assertIn("is_valid", columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m8_database_is_migrated_to_external_submission_blocks_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            root.mkdir(parents=True, exist_ok=True)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('schema_version', '8')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(external_submission_blocks)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("source_module", columns)
            self.assertIn("source_record_id", columns)
            self.assertIn("file_name", columns)
            self.assertIn("file_path", columns)
            self.assertIn("match_title", columns)
            self.assertIn("target_service", columns)
            self.assertIn("block_rule_type", columns)
            self.assertIn("matched_value_masked", columns)
            self.assertIn("status", columns)
            self.assertIn("override_reason", columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m9_database_is_migrated_to_metadata_candidate_cache_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '9')")
                connection.execute(
                    "CREATE TABLE rename_previews "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, media_file_id INTEGER NOT NULL UNIQUE, "
                    "media_type TEXT NOT NULL, parsed_title TEXT NOT NULL, parsed_year INTEGER, "
                    "season INTEGER, episode INTEGER, original_extension TEXT NOT NULL, "
                    "suggested_name TEXT NOT NULL, edited_name TEXT, metadata_source TEXT, "
                    "metadata_match_status TEXT, metadata_match_score INTEGER NOT NULL DEFAULT 0, "
                    "metadata_message TEXT, status TEXT NOT NULL, message TEXT, "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO rename_previews "
                    "(id, media_file_id, media_type, parsed_title, original_extension, suggested_name, "
                    "metadata_match_score, status, created_at, updated_at) "
                    "VALUES (1, 1, 'movie', 'Movie', '.mkv', 'Movie.mkv', 90, 'tmdb_selected', 'now', 'now')"
                )
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                preview_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(rename_previews)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                status = connection.execute("SELECT status FROM rename_previews WHERE id = 1").fetchone()[0]

            self.assertIn("metadata_candidates_json", preview_columns)
            self.assertEqual("generated", status)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_existing_m10_database_is_migrated_to_incremental_scan_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "mediaai.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '10')")
                connection.execute(
                    "CREATE TABLE scan_jobs "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "media_source_id INTEGER NOT NULL, status TEXT NOT NULL, "
                    "batch_size INTEGER NOT NULL, batch_interval_seconds REAL NOT NULL, "
                    "scanned_count INTEGER NOT NULL DEFAULT 0, "
                    "video_count INTEGER NOT NULL DEFAULT 0, "
                    "warning_count INTEGER NOT NULL DEFAULT 0, "
                    "error_message TEXT, started_at TEXT, ended_at TEXT, "
                    "created_at TEXT NOT NULL)"
                )
                connection.commit()
            settings = AppSettings(
                data_dir=root,
                database_path=database_path,
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )

            ensure_database(settings)

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                scan_job_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(scan_jobs)")
                }
                index_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(scan_file_index)")
                }
                schema_version = connection.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0]

            self.assertIn("scan_file_index", tables)
            self.assertIn("scan_mode", scan_job_columns)
            self.assertIn("new_count", scan_job_columns)
            self.assertIn("changed_count", scan_job_columns)
            self.assertIn("skipped_count", scan_job_columns)
            self.assertIn("missing_count", scan_job_columns)
            self.assertIn("indexed_count", scan_job_columns)
            self.assertIn("normalized_path", index_columns)
            self.assertIn("fingerprint", index_columns)
            self.assertIn("last_scan_job_id", index_columns)
            self.assertIn("rename_preview_id", index_columns)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)

    def test_schema_36_local_actions_gain_entry_and_phase_evidence_without_guessing_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '36')")
                connection.execute(
                    "CREATE TABLE local_file_actions ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, media_source_id INTEGER NOT NULL, "
                    "action_type TEXT NOT NULL, record_id INTEGER NOT NULL, "
                    "source_path TEXT NOT NULL, target_path TEXT NOT NULL, "
                    "source_key TEXT NOT NULL, target_key TEXT NOT NULL, "
                    "identity_json TEXT NOT NULL, target_identity_json TEXT, "
                    "status TEXT NOT NULL, message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO local_file_actions "
                    "(media_source_id, action_type, record_id, source_path, target_path, source_key, target_key, "
                    "identity_json, status, created_at, updated_at) "
                    "VALUES (1, 'rename', 1, 'C:/Media/Movie.mkv', 'C:/Media/movie.mkv', "
                    "'c:/media/movie.mkv', 'c:/media/movie.mkv', '[1,2,3,4]', 'prepared', 'now', 'now')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(local_file_actions)")}
                row = connection.execute(
                    "SELECT source_entry_name, target_entry_name, action_phase FROM local_file_actions WHERE id=1"
                ).fetchone()
                schemaVersion = connection.execute(
                    "SELECT value FROM app_meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertTrue({"source_entry_name", "target_entry_name", "action_phase"}.issubset(columns))
            self.assertEqual((None, None, "intent_recorded"), row)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schemaVersion)

    def test_schema_37_audit_intents_gain_review_fields_without_inventing_outcomes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '37')")
                connection.execute(
                    "CREATE TABLE file_action_audit_intents ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,task_type TEXT NOT NULL,task_id INTEGER NOT NULL,"
                    "context_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',last_error_type TEXT,"
                    "outcome_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO file_action_audit_intents "
                    "(task_type,task_id,context_json,status,created_at,updated_at) "
                    "VALUES ('rename_operation',1,'{}','pending','now','now')"
                )
                connection.commit()

            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(file_action_audit_intents)")}
                row = connection.execute(
                    "SELECT status,request_phase,outcome_json,closed_at,closed_by,close_reason,close_evidence "
                    "FROM file_action_audit_intents WHERE id=1"
                ).fetchone()
                schemaVersion = connection.execute(
                    "SELECT value FROM app_meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertTrue({"request_phase", "closed_at", "closed_by", "close_reason", "close_evidence"}.issubset(columns))
            self.assertEqual(("pending", "legacy_unknown", None, None, None, None, None), row)
            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schemaVersion)


if __name__ == "__main__":
    unittest.main()
