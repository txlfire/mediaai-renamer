"""M9 rename rollback tests."""

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
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation
from app.service.rename_rollback_service import (
    create_rename_rollback_plan,
    dry_run_rename_rollback_plan,
    execute_rename_rollback_plan,
)


class RenameRollbackTest(unittest.TestCase):
    """重命名回滚服务。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.source = self.media_dir / "Movie.2024.1080p.mkv"
        self.source.write_text("movie", encoding="utf-8")
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

    def _execute_sample_rename(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Safe")
        operation = create_rename_dry_run(self.settings, [preview.id])
        return execute_rename_operation(self.settings, operation.id)

    def test_rollback_plan_dry_run_and_execute_restores_file(self):
        operation = self._execute_sample_rename()
        target = self.media_dir / "Movie.Safe.mkv"
        self.assertFalse(self.source.exists())
        self.assertTrue(target.exists())

        plan = create_rename_rollback_plan(self.settings, operation.id, created_by="admin")
        checked = dry_run_rename_rollback_plan(self.settings, plan.id)
        executed = execute_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual(1, plan.item_count)
        self.assertEqual("checked", checked.status)
        self.assertEqual(1, checked.executable_count)
        self.assertEqual("executed", executed.status)
        self.assertEqual("rolled_back", executed.items[0].status)
        self.assertTrue(self.source.exists())
        self.assertFalse(target.exists())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            media_row = connection.execute("SELECT file_path, file_name FROM media_files WHERE id = 1").fetchone()
            preview_row = connection.execute("SELECT status, message FROM rename_previews WHERE id = 1").fetchone()
            log_count = connection.execute(
                "SELECT COUNT(*) AS total FROM operation_logs WHERE task_type = 'rollback_plan' AND task_id = ?",
                (plan.id,),
            ).fetchone()["total"]

        self.assertEqual(str(self.source), media_row["file_path"])
        self.assertEqual(self.source.name, media_row["file_name"])
        self.assertEqual("rolled_back", preview_row["status"])
        self.assertEqual("已回滚", preview_row["message"])
        self.assertGreaterEqual(log_count, 3)

    def test_rollback_dry_run_detects_existing_target(self):
        operation = self._execute_sample_rename()
        self.source.write_text("external", encoding="utf-8")

        plan = create_rename_rollback_plan(self.settings, operation.id)
        checked = dry_run_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual("checked", checked.status)
        self.assertEqual(0, checked.executable_count)
        self.assertEqual(1, checked.conflict_count)
        self.assertEqual("conflict", checked.items[0].status)
        self.assertEqual("回滚目标已存在", checked.items[0].message)
        with self.assertRaises(ValueError):
            execute_rename_rollback_plan(self.settings, plan.id)

    def test_api_creates_dry_runs_and_executes_rollback_plan(self):
        operation = self._execute_sample_rename()
        app = create_app(self.settings)
        client = TestClient(app)

        create_response = client.post(f"/api/rename-operations/{operation.id}/rollback-plan")
        self.assertEqual(201, create_response.status_code)
        plan_id = create_response.json()["id"]
        dry_run_response = client.post(f"/api/rename-rollback-plans/{plan_id}/dry-run")
        execute_response = client.post(f"/api/rename-rollback-plans/{plan_id}/execute")

        self.assertEqual(200, dry_run_response.status_code)
        self.assertEqual(200, execute_response.status_code)
        self.assertEqual("executed", execute_response.json()["status"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            audit_total = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = 'rename.rollback'"
            ).fetchone()[0]
        self.assertGreaterEqual(audit_total, 3)


if __name__ == "__main__":
    unittest.main()
