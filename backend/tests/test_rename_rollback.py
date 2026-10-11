"""M9 rename rollback tests."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from api_test_support import authenticatedClient as TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import runAuditedFileAction, flushFileActionAuditIntents
from fastapi import Response
from app.main import create_app
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation
from app.service.shared_protocols.base import RemoteFileEntry
from app.service.shared_protocols.base import ConnectionTestResult
from app.service.media_source_service import set_media_source_enabled, update_media_source
from app.service.rename_rollback_service import (
    create_rename_rollback_plan,
    dry_run_rename_rollback_plan,
    execute_rename_rollback_plan,
    get_rename_rollback_plan,
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

    def test_plan_creation_audit_failure_rolls_back_plan_and_items(self):
        operation = self._execute_sample_rename()
        with patch("app.service.rename_rollback_service.record_audit_event",
                   side_effect=sqlite3.OperationalError("synthetic audit failure")):
            with self.assertRaises(sqlite3.OperationalError):
                create_rename_rollback_plan(self.settings, operation.id)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM rename_rollback_plans").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM rename_rollback_items").fetchone()[0])
        self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())

    def test_plan_dry_run_audit_failure_rolls_back_snapshots_and_state(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        with patch("app.service.rename_rollback_service.record_audit_event",
                   side_effect=sqlite3.OperationalError("synthetic audit failure")):
            with self.assertRaises(sqlite3.OperationalError):
                dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual(plan, get_rename_rollback_plan(self.settings, plan.id))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertIsNone(connection.execute("SELECT snapshot_json FROM rename_rollback_items WHERE plan_id=?",
                                                 (plan.id,)).fetchone()[0])

    def test_dry_run_conflicts_are_not_audited_as_success(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        self.source.write_text("occupied", encoding="utf-8")
        checked = dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual(1, checked.conflict_count)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            result, detail = connection.execute("SELECT result,detail_json FROM audit_events "
                "WHERE action='dry_run_rollback_plan' AND target_id=?", (str(plan.id),)).fetchone()
        self.assertEqual("failed", result)
        self.assertEqual(1, json.loads(detail)["conflict_count"])

    def test_rollback_maintenance_does_not_attribute_old_success_to_new_request(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        execute_rename_rollback_plan(self.settings, plan.id)

        def reject():
            self.assertEqual(0, flushFileActionAuditIntents(self.settings)["completed"])
            raise ValueError("synthetic duplicate rollback")

        with self.assertRaisesRegex(ValueError, "duplicate rollback"):
            runAuditedFileAction(self.settings, taskType="rollback_plan", taskId=plan.id,
                context=AuditWriteContext(), execute=reject, response=Response())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute("SELECT result,detail_json FROM audit_events "
                "WHERE action='execute_rollback_plan' ORDER BY id").fetchall()
        self.assertEqual(["pending", "failed"], [row[0] for row in rows])
        self.assertEqual("executed", json.loads(rows[-1][1])["status"])
        self.assertTrue(self.source.exists())

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

    def test_rollback_prepare_then_source_replacement_is_rejected_before_move(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        current = self.media_dir / "Movie.Safe.mkv"
        import app.service.rename_rollback_service as service

        originalPrepare = service.prepareLocalFileAction

        def prepareAndReplace(*args, **kwargs):
            actionId = originalPrepare(*args, **kwargs)
            current.unlink()
            current.write_text("replacement", encoding="utf-8")
            return actionId

        with (
            patch.object(service, "prepareLocalFileAction", side_effect=prepareAndReplace),
            patch.object(service, "moveFileNoReplace") as mover,
        ):
            executed = execute_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual("failed", executed.status)
        mover.assert_not_called()
        self.assertEqual("replacement", current.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            status, message = connection.execute(
                "SELECT status,message FROM local_file_actions WHERE action_type='rollback'"
            ).fetchone()
        self.assertEqual("recovery_required", status)
        self.assertIn("身份已变化", message)

    def test_rollback_post_move_target_replacement_requires_recovery(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        current = self.media_dir / "Movie.Safe.mkv"
        import app.service.rename_rollback_service as service

        originalMove = service.moveFileNoReplace

        def moveAndReplace(sourcePath, targetPath):
            originalMove(sourcePath, targetPath)
            Path(targetPath).unlink()
            Path(targetPath).write_text("replacement", encoding="utf-8")

        with patch.object(service, "moveFileNoReplace", side_effect=moveAndReplace):
            executed = execute_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual("failed", executed.status)
        self.assertFalse(current.exists())
        self.assertEqual("replacement", self.source.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            status, targetIdentity, message = connection.execute(
                "SELECT status,target_identity_json,message FROM local_file_actions WHERE action_type='rollback'"
            ).fetchone()
            mediaPath = connection.execute("SELECT file_path FROM media_files WHERE id=1").fetchone()[0]
        self.assertEqual("recovery_required", status)
        self.assertIsNone(targetIdentity)
        self.assertIn("目标文件身份无法确认", message)
        self.assertEqual(str(current), mediaPath)

    def test_changed_local_file_after_dry_run_is_not_rolled_back(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        target = self.media_dir / "Movie.Safe.mkv"
        target.write_text("external changed content", encoding="utf-8")
        result = execute_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual("failed", result.items[0].status)
        self.assertIn("已变化", result.items[0].message)
        self.assertFalse(self.source.exists())
        self.assertEqual("external changed content", target.read_text(encoding="utf-8"))

    def test_source_root_change_preserves_completed_history_but_blocks_new_rollback(self):
        operation = self._execute_sample_rename()
        newRoot = self.root / "new-root"
        newRoot.mkdir()
        update_media_source(self.settings, 1, "changed", newRoot, True, clear_history_on_path_change=True)
        with self.assertRaisesRegex(ValueError, "历史批次不能"):
            create_rename_rollback_plan(self.settings, operation.id)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("renamed", connection.execute("SELECT status FROM rename_operation_items").fetchone()[0])
        self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())

    def test_legacy_checked_plan_without_snapshot_never_moves_file(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE rename_rollback_items SET snapshot_json = NULL WHERE plan_id = ?", (plan.id,))
            connection.commit()
        result = execute_rename_rollback_plan(self.settings, plan.id)
        self.assertIn("缺少安全快照", result.items[0].message)
        self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())
        self.assertFalse(self.source.exists())

    def test_changed_webdav_etag_after_dry_run_never_sends_move(self):
        self._insert_webdav_renamed_operation()
        plan = create_rename_rollback_plan(self.settings, 9)
        entry = RemoteFileEntry(
            path="https://nas.example/dav/Movie.Safe.mkv", name="Movie.Safe.mkv", extension=".mkv",
            file_size=2048, modified_at="now", version='"before"',
        )
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "ready")),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=entry) as info,
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover,
        ):
            dry_run_rename_rollback_plan(self.settings, plan.id)
            info.return_value = RemoteFileEntry(
                path=entry.path, name=entry.name, extension=entry.extension,
                file_size=entry.file_size, modified_at=entry.modified_at, version='"after"',
            )
            result = execute_rename_rollback_plan(self.settings, plan.id)
            mover.assert_not_called()
        self.assertIn("已变化", result.items[0].message)

    def test_running_rollback_blocks_duplicate_execution_source_change_and_preview_edit(self):
        operation = self._execute_sample_rename()
        plan = create_rename_rollback_plan(self.settings, operation.id)
        dry_run_rename_rollback_plan(self.settings, plan.id)
        entered = Event()
        proceed = Event()

        def checkReady(*args):
            entered.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError("测试等待超时")
            return ConnectionTestResult(True, "可回滚")

        with patch("app.service.shared_protocols.local.LocalProtocol.check_rename_ready", side_effect=checkReady):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(execute_rename_rollback_plan, self.settings, plan.id)
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    with self.assertRaises(ValueError):
                        execute_rename_rollback_plan(self.settings, plan.id)
                    with self.assertRaisesRegex(ValueError, "运行中任务"):
                        set_media_source_enabled(self.settings, 1, False)
                    with self.assertRaisesRegex(ValueError, "正在执行回滚"):
                        update_rename_preview(self.settings, operation.items[0].rename_preview_id, "Other")
                finally:
                    proceed.set()
                result = future.result(timeout=10)
        self.assertEqual("executed", result.status)

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
            captured_move["expected_version"] = context.expected_source_version
            return ConnectionTestResult(True, "WebDAV MOVE 执行成功", readable=True, writable=True)

        plan = create_rename_rollback_plan(self.settings, 9, created_by="admin")
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", side_effect=fake_check_rename_ready),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", side_effect=fake_move_file),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=RemoteFileEntry(
                path="https://nas.example/dav/Movie.2024.1080p.mkv", name="Movie.2024.1080p.mkv",
                extension=".mkv", file_size=2048, modified_at="2026-01-01T00:00:00+00:00", version='"target-etag"',
            )),
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
        self.assertEqual('"target-etag"', captured_move["expected_version"])
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

    def test_webdav_rollback_indeterminate_remains_recovery_required(self):
        self._insert_webdav_renamed_operation()
        entry = RemoteFileEntry(
            path="https://nas.example/dav/Movie.Safe.mkv",
            name="Movie.Safe.mkv",
            extension=".mkv",
            file_size=2048,
            modified_at="now",
            version='"target-etag"',
        )
        plan = create_rename_rollback_plan(self.settings, 9, created_by="admin")
        with patch(
            "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
            return_value=ConnectionTestResult(True, "可回滚"),
        ), patch(
            "app.service.shared_protocols.webdav.WebDavProtocol.get_file_info",
            return_value=entry,
        ) as info:
            dry_run_rename_rollback_plan(self.settings, plan.id)
            with patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                return_value=ConnectionTestResult(False, "HTTP 204 结果待核查", indeterminate=True),
            ) as mover:
                executed = execute_rename_rollback_plan(self.settings, plan.id)

        self.assertEqual("failed", executed.status)
        self.assertGreaterEqual(info.call_count, 2)
        mover.assert_called_once()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            remoteRow = connection.execute(
                "SELECT status, error_message FROM remote_operation_items WHERE operation_type='rollback'"
            ).fetchone()
        self.assertEqual("recovery_required", remoteRow[0])
        self.assertIn("人工核查", remoteRow[1])

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
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            for action in ("create_rollback_plan", "dry_run_rollback_plan"):
                rows = connection.execute("SELECT actor_id,actor_name,result FROM audit_events WHERE action=?",
                                          (action,)).fetchall()
                self.assertEqual(1, len(rows))
                self.assertIsNotNone(rows[0][0])
                self.assertEqual(("api-review", "success"), rows[0][1:])
        self.assertEqual(200, execute_response.status_code)
        self.assertEqual("executed", execute_response.json()["status"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            audit_total = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = 'rename.rollback'"
            ).fetchone()[0]
        self.assertGreaterEqual(audit_total, 3)


if __name__ == "__main__":
    unittest.main()
