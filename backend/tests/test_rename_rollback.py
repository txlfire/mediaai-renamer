"""M9 rename rollback tests."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

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

    def test_webdav_rollback_dry_run_and_execute_uses_remote_move(self):
        self._insert_webdav_renamed_operation()
        captured_move = {}

        def fake_check_rename_ready(source_path, target_path, context=None):
            from app.service.shared_protocols.base import ConnectionTestResult

            self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", source_path)
            self.assertEqual("https://nas.example/dav/Movie.2024.1080p.mkv", target_path)
            self.assertEqual("webdav", context.path_type if context else None)
            return ConnectionTestResult(True, "WebDAV 回滚 dry-run 可执行", readable=True, writable=True)

        def fake_move_file(source_path, target_path, context=None):
            from app.service.shared_protocols.base import ConnectionTestResult

            captured_move["source_path"] = source_path
            captured_move["target_path"] = target_path
            captured_move["context_path_type"] = context.path_type if context else None
            return ConnectionTestResult(True, "WebDAV MOVE 执行成功", readable=True, writable=True)

        plan = create_rename_rollback_plan(self.settings, 9, created_by="admin")
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", side_effect=fake_check_rename_ready),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", side_effect=fake_move_file),
        ):
            checked = dry_run_rename_rollback_plan(self.settings, plan.id)
            executed = execute_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual("checked", checked.status)
        self.assertEqual(1, checked.executable_count)
        self.assertEqual(0, checked.conflict_count)
        self.assertEqual("executed", executed.status)
        self.assertEqual("rolled_back", executed.items[0].status)
        self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", captured_move["source_path"])
        self.assertEqual("https://nas.example/dav/Movie.2024.1080p.mkv", captured_move["target_path"])
        self.assertEqual("webdav", captured_move["context_path_type"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            media_row = connection.execute(
                "SELECT file_path, file_name, extension FROM media_files WHERE id = 9"
            ).fetchone()
            preview_row = connection.execute(
                "SELECT status, message FROM rename_previews WHERE id = 9"
            ).fetchone()
            remote_row = connection.execute(
                "SELECT operation_type, source_path, target_path, status, recovery_json "
                "FROM remote_operation_items "
                "WHERE operation_type = 'rollback'"
            ).fetchone()

        self.assertEqual("https://nas.example/dav/Movie.2024.1080p.mkv", media_row[0])
        self.assertEqual("Movie.2024.1080p.mkv", media_row[1])
        self.assertEqual(".mkv", media_row[2])
        self.assertEqual(("rolled_back", "已回滚"), preview_row)
        self.assertEqual(
            (
                "rollback",
                "https://nas.example/dav/Movie.Safe.mkv",
                "https://nas.example/dav/Movie.2024.1080p.mkv",
                "completed",
            ),
            remote_row[:4],
        )
        recovery = json.loads(remote_row[4])
        self.assertEqual(plan.id, recovery["rollback_plan_id"])
        self.assertEqual(executed.items[0].id, recovery["rollback_item_id"])
        self.assertEqual(9, recovery["operation_item_id"])

    def _insert_webdav_renamed_operation(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources "
                "(id, name, path, path_type, protocol, username, encrypted_secret, auth_type, enabled, created_at, updated_at) "
                "VALUES (9, 'webdav', 'https://nas.example/dav/', 'webdav', 'webdav', NULL, NULL, 'none', 1, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (9, 9, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES "
                "(9, 9, 9, 'https://nas.example/dav/Movie.Safe.mkv', "
                "'Movie.Safe.mkv', '.mkv', 2048, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_previews "
                "(id, media_file_id, media_type, parsed_title, parsed_year, season, episode, original_extension, "
                "suggested_name, edited_name, status, created_at, updated_at) "
                "VALUES (9, 9, 'movie', 'Movie', 2024, NULL, NULL, '.mkv', "
                "'Movie.Safe.mkv', 'Movie.Safe.mkv', 'renamed', 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operations "
                "(id, status, mode, total_count, ready_count, conflict_count, renamed_count, failed_count, created_at, updated_at) "
                "VALUES (9, 'completed', 'safe_rename', 1, 0, 0, 1, 0, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(id, operation_id, rename_preview_id, source_path, target_path, status, message, created_at, updated_at) "
                "VALUES (9, 9, 9, 'https://nas.example/dav/Movie.2024.1080p.mkv', "
                "'https://nas.example/dav/Movie.Safe.mkv', 'renamed', NULL, 'now', 'now')"
            )
            connection.commit()

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
