"""M3 rename operation tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.preview_service import (
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
)
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation


class RenameOperationDatabaseTest(unittest.TestCase):
    """Rename operation database schema tests."""

    def test_database_creates_rename_operation_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = AppSettings(
                data_dir=root,
                database_path=root / "mediaai.sqlite3",
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )
            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertIn("rename_operations", table_names)
            self.assertIn("rename_operation_items", table_names)


class RenameOperationDryRunTest(unittest.TestCase):
    """Rename operation dry-run behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.source = self.media_dir / "Movie.2024.1080p.mkv"
        self.source.write_text("movie", encoding="utf-8")
        self.existing = self.media_dir / "Movie.2024.mkv"
        self.existing.write_text("existing", encoding="utf-8")
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
        )
        ensure_database(self.settings)
        self._insert_media_file()

    def tearDown(self):
        shutdown_logging()
        self.temp_dir.cleanup()

    def _insert_media_file(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                "VALUES (1, 'test', ?, 1, 'now', 'now')",
                (str(self.media_dir),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (1, 1, 1, ?, ?, '.mkv', 5, 'now', 'now')",
                (str(self.source), self.source.name),
            )
            connection.commit()

    def _insert_additional_media_file(self, media_file_id: int, file_path: Path):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (?, 1, 1, ?, ?, ?, ?, 'now', 'now')",
                (
                    media_file_id,
                    str(file_path),
                    file_path.name,
                    file_path.suffix.lower(),
                    file_path.stat().st_size,
                ),
            )
            connection.commit()

    def test_dry_run_detects_existing_target_without_renaming(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]

        operation = create_rename_dry_run(self.settings, [preview.id])

        self.assertEqual("dry_run", operation.status)
        self.assertEqual(1, operation.conflict_count)
        self.assertEqual("conflict", operation.items[0].status)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.existing.exists())

    def test_execute_renames_ready_items_only(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Safe")
        operation = create_rename_dry_run(self.settings, [preview.id])

        executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual(1, executed.renamed_count)
        self.assertEqual(0, executed.ready_count)
        self.assertEqual("completed", executed.status)
        self.assertFalse(self.source.exists())
        self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            row = connection.execute(
                "SELECT file_path, file_name FROM media_files WHERE id = 1"
            ).fetchone()
        self.assertEqual(str(self.media_dir / "Movie.Safe.mkv"), row[0])
        self.assertEqual("Movie.Safe.mkv", row[1])
        updated_preview = list_rename_previews(self.settings)[0]
        self.assertEqual("Movie.Safe.mkv", updated_preview.file_name)
        self.assertEqual("renamed", updated_preview.status)

    def test_execute_completed_operation_keeps_previous_result(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Once")
        operation = create_rename_dry_run(self.settings, [preview.id])

        first_execution = execute_rename_operation(self.settings, operation.id)
        second_execution = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("completed", first_execution.status)
        self.assertEqual("completed", second_execution.status)
        self.assertEqual(1, second_execution.renamed_count)
        self.assertEqual(0, second_execution.failed_count)

    def test_dry_run_marks_duplicate_targets_as_conflict(self):
        second_source = self.media_dir / "Movie.2025.2160p.mkv"
        second_source.write_text("movie-2", encoding="utf-8")
        self._insert_additional_media_file(2, second_source)
        generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)

        update_rename_preview(self.settings, previews[0].id, "Shared.Name")
        update_rename_preview(self.settings, previews[1].id, "Shared.Name")

        operation = create_rename_dry_run(self.settings, [preview.id for preview in previews])

        self.assertEqual(0, operation.ready_count)
        self.assertEqual(2, operation.conflict_count)
        self.assertTrue(all(item.status == "conflict" for item in operation.items))

    def test_dry_run_marks_empty_target_name_as_conflict(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET suggested_name = '', edited_name = '' WHERE id = ?",
                (preview.id,),
            )
            connection.commit()

        operation = create_rename_dry_run(self.settings, [preview.id])

        self.assertEqual(0, operation.ready_count)
        self.assertEqual(1, operation.conflict_count)
        self.assertEqual("conflict", operation.items[0].status)

    def test_execute_marks_missing_source_as_failed(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Missing")
        operation = create_rename_dry_run(self.settings, [preview.id])
        self.source.unlink()

        executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", executed.status)
        self.assertEqual(0, executed.ready_count)
        self.assertEqual(0, executed.renamed_count)
        self.assertEqual(1, executed.failed_count)
        self.assertEqual("failed", executed.items[0].status)

    def test_rename_operation_api_dry_run_and_execute(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Api")
        client = TestClient(create_app(self.settings))

        dry_run_response = client.post(
            "/api/rename-operations/dry-run",
            json={"rename_preview_ids": [preview.id]},
        )
        self.assertEqual(200, dry_run_response.status_code)
        operation_id = dry_run_response.json()["id"]

        get_response = client.get(f"/api/rename-operations/{operation_id}")
        self.assertEqual(200, get_response.status_code)
        self.assertEqual(1, get_response.json()["ready_count"])

        execute_response = client.post(f"/api/rename-operations/{operation_id}/execute")
        self.assertEqual(200, execute_response.status_code)
        self.assertEqual(1, execute_response.json()["renamed_count"])
        self.assertTrue((self.media_dir / "Movie.Api.mkv").exists())


if __name__ == "__main__":
    unittest.main()
