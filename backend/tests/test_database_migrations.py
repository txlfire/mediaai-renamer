"""Database migration tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database


class DatabaseMigrationTest(unittest.TestCase):
    """SQLite schema migration behavior."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

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
            self.assertIn("scan_mode", scan_job_columns)
            self.assertIn("new_count", scan_job_columns)
            self.assertIn("changed_count", scan_job_columns)
            self.assertIn("skipped_count", scan_job_columns)
            self.assertIn("missing_count", scan_job_columns)
            self.assertIn("indexed_count", scan_job_columns)

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


if __name__ == "__main__":
    unittest.main()
