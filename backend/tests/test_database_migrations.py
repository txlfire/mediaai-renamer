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

            self.assertEqual(str(CURRENT_SCHEMA_VERSION), schema_version)


if __name__ == "__main__":
    unittest.main()
