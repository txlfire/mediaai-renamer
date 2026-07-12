"""M9 task governance tests."""

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
from app.service.rename_rollback_service import create_rename_rollback_plan, dry_run_rename_rollback_plan
from app.service.task_governance_service import list_task_governance_items, set_task_archived


class TaskGovernanceTest(unittest.TestCase):
    """任务治理聚合行为。"""

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
        self._insert_base_rows()

    def tearDown(self):
        shutdown_logging()
        self.temp_dir.cleanup()

    def _insert_base_rows(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                "VALUES (1, '电影库', ?, 1, '2026-07-09T00:00:00+00:00', '2026-07-09T00:00:00+00:00')",
                (str(self.media_dir),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, scan_mode, "
                "scanned_count, video_count, warning_count, created_at, started_at, ended_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'full', 1, 1, 0, "
                "'2026-07-09T00:00:00+00:00', '2026-07-09T00:00:00+00:00', '2026-07-09T00:01:00+00:00')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (1, 1, 1, ?, ?, '.mkv', 5, "
                "'2026-07-09T00:00:00+00:00', '2026-07-09T00:00:00+00:00')",
                (str(self.source), self.source.name),
            )
            connection.commit()

    def _create_operation_and_rollback(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Safe")
        operation = create_rename_dry_run(self.settings, [preview.id])
        executed = execute_rename_operation(self.settings, operation.id)
        plan = create_rename_rollback_plan(self.settings, executed.id)
        checked = dry_run_rename_rollback_plan(self.settings, plan.id)
        return executed, checked

    def test_list_task_governance_items_aggregates_three_task_types(self):
        operation, plan = self._create_operation_and_rollback()

        items = list_task_governance_items(self.settings)
        task_ids = {item.id for item in items}

        self.assertIn("scan_job:1", task_ids)
        self.assertIn(f"rename_operation:{operation.id}", task_ids)
        self.assertIn(f"rollback_plan:{plan.id}", task_ids)
        rename_item = next(item for item in items if item.task_type == "rename_operation")
        self.assertEqual(1, rename_item.media_source_id)
        self.assertEqual("电影库", rename_item.media_source_name)
        self.assertEqual("rename_operation", rename_item.log_task_type)
        rollback_item = next(item for item in items if item.task_type == "rollback_plan")
        self.assertEqual(operation.id, rollback_item.related_id)
        self.assertEqual("rollback_plan", rollback_item.log_task_type)

    def test_list_task_governance_items_supports_filters(self):
        operation, _plan = self._create_operation_and_rollback()

        rename_items = list_task_governance_items(
            self.settings,
            task_type="rename_operation",
            status="completed",
            media_source_id=1,
        )

        self.assertEqual(1, len(rename_items))
        self.assertEqual(operation.id, rename_items[0].task_id)
        self.assertEqual("completed", rename_items[0].status)
        with self.assertRaises(ValueError):
            list_task_governance_items(self.settings, task_type="unknown")

    def test_archive_hides_task_without_deleting_source_rows(self):
        archived = set_task_archived(
            self.settings,
            task_type="scan_job",
            task_id=1,
            archived=True,
            archived_by="tester",
            reason="已处理",
        )

        self.assertTrue(archived.archived)
        self.assertEqual("tester", archived.archived_by)
        self.assertEqual("已处理", archived.archive_reason)
        visible_items = list_task_governance_items(self.settings)
        self.assertNotIn("scan_job:1", {item.id for item in visible_items})
        archived_items = list_task_governance_items(self.settings, include_archived=True)
        self.assertIn("scan_job:1", {item.id for item in archived_items})
        restored = set_task_archived(
            self.settings,
            task_type="scan_job",
            task_id=1,
            archived=False,
        )
        self.assertFalse(restored.archived)
        visible_items = list_task_governance_items(self.settings)
        self.assertIn("scan_job:1", {item.id for item in visible_items})

        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            scan_count = connection.execute("SELECT COUNT(*) FROM scan_jobs WHERE id = 1").fetchone()[0]
            file_count = connection.execute("SELECT COUNT(*) FROM media_files WHERE scan_job_id = 1").fetchone()[0]

        self.assertEqual(1, scan_count)
        self.assertEqual(1, file_count)

    def test_tasks_api_returns_items(self):
        self._create_operation_and_rollback()
        app = create_app(self.settings)
        client = TestClient(app)

        response = client.get("/api/tasks?task_type=rollback_plan&limit=10")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual(1, len(data["items"]))
        self.assertEqual("rollback_plan", data["items"][0]["task_type"])
        self.assertEqual("rollback_plan", data["items"][0]["log_task_type"])

    def test_tasks_api_archives_and_restores_task(self):
        app = create_app(self.settings)
        client = TestClient(app)

        archive_response = client.patch(
            "/api/tasks/scan_job/1/archive",
            json={"archived": True, "reason": "已处理"},
        )
        self.assertEqual(200, archive_response.status_code)
        self.assertTrue(archive_response.json()["archived"])

        hidden_response = client.get("/api/tasks?task_type=scan_job")
        self.assertEqual([], hidden_response.json()["items"])
        visible_response = client.get("/api/tasks?task_type=scan_job&include_archived=true")
        self.assertEqual(1, len(visible_response.json()["items"]))

        restore_response = client.patch(
            "/api/tasks/scan_job/1/archive",
            json={"archived": False},
        )
        self.assertEqual(200, restore_response.status_code)
        self.assertFalse(restore_response.json()["archived"])


if __name__ == "__main__":
    unittest.main()
