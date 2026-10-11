"""M3 rename operation tests."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError
from dataclasses import replace

from api_test_support import authenticatedClient as TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.preview_service import (
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
    exclude_rename_preview,
)
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.file_action_audit_service import (
    createFileActionAuditIntent, flushFileActionAuditIntents, inspectFileActionAuditIntents, runAuditedFileAction,
    completeFileActionAuditIntent,
)
from fastapi import Response
from app.service.shared_protocols.base import ConnectionTestResult, RemoteFileEntry
from app.service.credential_key import CredentialError
from app.service.settings_service import update_setting_values


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

    def test_prepare_then_source_replacement_is_rejected_before_move(self):
        _, operation = self._safeDryRun()
        import app.service.rename_operation_service as service

        originalPrepare = service.prepareLocalFileAction

        def prepareAndReplace(*args, **kwargs):
            actionId = originalPrepare(*args, **kwargs)
            self.source.unlink()
            self.source.write_text("replacement", encoding="utf-8")
            return actionId

        with (
            patch.object(service, "prepareLocalFileAction", side_effect=prepareAndReplace),
            patch.object(service, "moveFileNoReplace") as mover,
        ):
            executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", executed.status)
        mover.assert_not_called()
        self.assertEqual("replacement", self.source.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            status, message = connection.execute(
                "SELECT status,message FROM local_file_actions"
            ).fetchone()
        self.assertEqual("recovery_required", status)
        self.assertIn("身份已变化", message)

    def test_post_move_target_replacement_requires_recovery(self):
        _, operation = self._safeDryRun()
        target = self.media_dir / "Movie.Safe.mkv"
        import app.service.rename_operation_service as service

        originalMove = service.moveFileNoReplace

        def moveAndReplace(sourcePath, targetPath):
            originalMove(sourcePath, targetPath)
            Path(targetPath).unlink()
            Path(targetPath).write_text("replacement", encoding="utf-8")

        with patch.object(service, "moveFileNoReplace", side_effect=moveAndReplace):
            executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", executed.status)
        self.assertFalse(self.source.exists())
        self.assertEqual("replacement", target.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            status, targetIdentity, message = connection.execute(
                "SELECT status,target_identity_json,message FROM local_file_actions"
            ).fetchone()
            mediaPath = connection.execute("SELECT file_path FROM media_files WHERE id=1").fetchone()[0]
        self.assertEqual("recovery_required", status)
        self.assertIsNone(targetIdentity)
        self.assertIn("目标文件身份无法确认", message)
        self.assertEqual(str(self.source), mediaPath)

    def _safeDryRun(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Safe")
        return preview, create_rename_dry_run(self.settings, [preview.id])

    def test_execute_api_audit_records_real_dataclass_outcome_and_counts(self):
        _, operation = self._safeDryRun()
        client = TestClient(create_app(self.settings))
        for status, expectedAudit in (("failed", "failed"), ("partial_failed", "partial_success"), ("running", "pending"), ("completed", "failed")):
            with self.subTest(status=status):
                result = replace(operation, status=status, failed_count=1, renamed_count=1 if status == "partial_failed" else 0,
                                 items=[replace(operation.items[0], status="failed", message="controlled failure")])
                with patch("app.api.rename_operations.execute_rename_operation", return_value=result):
                    response = client.post(f"/api/rename-operations/{operation.id}/execute")
                self.assertEqual(200, response.status_code)
                with closing(sqlite3.connect(self.settings.database_path)) as connection:
                    eventResult, detailJson = connection.execute(
                        "SELECT result, detail_json FROM audit_events WHERE event_type = 'rename.execute' ORDER BY id DESC LIMIT 1",
                    ).fetchone()
                detail = json.loads(detailJson)
                self.assertEqual(expectedAudit, eventResult)
                if status == "running":
                    self.assertEqual("pending", response.headers["X-MediaAI-Audit-State"])
                    self.assertIn("audit_intent_id", detail)
                    continue
                self.assertEqual(status, detail["status"])
                self.assertEqual(1, detail["failed_count"])
                self.assertEqual(1, detail["item_count"])
                self.assertEqual("controlled failure", detail["failure_examples"][0]["message"])

    def test_audit_intent_failure_prevents_file_execution(self):
        _, operation = self._safeDryRun()
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("audit unavailable")), \
             patch("app.api.rename_operations.execute_rename_operation") as execute:
            client = TestClient(create_app(self.settings), raise_server_exceptions=False)
            response = client.post(f"/api/rename-operations/{operation.id}/execute")
        self.assertEqual(500, response.status_code)
        execute.assert_not_called()
        self.assertTrue(self.source.exists())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM file_action_audit_intents").fetchone()[0])

    def test_final_audit_failure_preserves_real_result_and_can_be_flushed_once(self):
        _, operation = self._safeDryRun()
        def failFinal(settings, **kwargs):
            if kwargs["result"] != "pending":
                raise sqlite3.OperationalError("audit unavailable")
            return record_audit_event(settings, **kwargs)
        client = TestClient(create_app(self.settings))
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=failFinal):
            response = client.post(f"/api/rename-operations/{operation.id}/execute")
        self.assertEqual(200, response.status_code)
        self.assertEqual("completed", response.json()["status"])
        self.assertEqual("pending", response.headers["X-MediaAI-Audit-State"])
        self.assertFalse(self.source.exists())
        self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            status, errorType, snapshot = connection.execute("SELECT status, last_error_type, outcome_json FROM file_action_audit_intents").fetchone()
        self.assertEqual(("pending", "OperationalError"), (status, errorType))
        self.assertEqual("success", json.loads(snapshot)["result"])
        self.assertEqual(1, flushFileActionAuditIntents(self.settings)["completed"])
        self.assertEqual(0, flushFileActionAuditIntents(self.settings)["completed"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'rename.execute'").fetchone()[0])

    def test_rejected_request_snapshot_survives_final_audit_failure_and_later_task_change(self):
        _, operation = self._safeDryRun()
        def failFinal(settings, **kwargs):
            if kwargs["result"] != "pending":
                raise sqlite3.OperationalError("audit unavailable")
            return record_audit_event(settings, **kwargs)
        def reject():
            raise ValueError("controlled rejection https://test:secret@example.invalid/root")
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=failFinal):
            with self.assertRaisesRegex(ValueError, "controlled rejection"):
                runAuditedFileAction(self.settings, taskType="rename_operation", taskId=operation.id,
                                     context=AuditWriteContext(userAgent="https://test:secret@example.invalid/root"),
                                     execute=reject, response=Response())
        execute_rename_operation(self.settings, operation.id)
        self.assertEqual(1, flushFileActionAuditIntents(self.settings)["completed"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            contextJson, outcomeJson = connection.execute("SELECT context_json, outcome_json FROM file_action_audit_intents").fetchone()
            eventResult = connection.execute("SELECT result FROM audit_events WHERE event_type = 'rename.execute' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual("failed", eventResult)
        self.assertNotIn("secret", contextJson + outcomeJson)
        self.assertIn("controlled rejection", outcomeJson)

    def test_running_intent_is_not_finalized_and_flush_limit_is_bounded(self):
        _, operation = self._safeDryRun()
        for _ in range(2):
            createFileActionAuditIntent(self.settings, "rename_operation", operation.id, AuditWriteContext())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE rename_operations SET status = 'running' WHERE id = ?", (operation.id,))
            connection.commit()
        self.assertEqual({"completed": 0, "pending": 1, "has_more": 1, "next_id": 1}, flushFileActionAuditIntents(self.settings, limit=1))
        self.assertEqual(2, inspectFileActionAuditIntents(self.settings, limit=1, afterId=1)["next_id"])
        for limit in (0, 5001):
            with self.assertRaises(ValueError):
                flushFileActionAuditIntents(self.settings, limit=limit)

    def test_audit_inspection_is_readonly_and_does_not_create_missing_database(self):
        _, operation = self._safeDryRun()
        createFileActionAuditIntent(self.settings, "rename_operation", operation.id, AuditWriteContext())
        before = self.settings.database_path.read_bytes()
        report = inspectFileActionAuditIntents(self.settings)
        self.assertEqual(1, len(report["items"]))
        self.assertFalse(report["items"][0]["hasOutcome"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        missingPath = self.root / "must-not-create.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            inspectFileActionAuditIntents(replace(self.settings, database_path=missingPath))
        self.assertFalse(missingPath.exists())

    def test_concurrent_audit_flush_does_not_duplicate_final_event(self):
        _, operation = self._safeDryRun()
        intentId = createFileActionAuditIntent(self.settings, "rename_operation", operation.id, AuditWriteContext())
        result = execute_rename_operation(self.settings, operation.id)
        with patch("app.service.file_action_audit_service.record_audit_event",
                   side_effect=sqlite3.OperationalError("synthetic final failure")):
            with self.assertRaises(sqlite3.OperationalError):
                completeFileActionAuditIntent(self.settings, intentId, executionResult=result)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: flushFileActionAuditIntents(self.settings), range(2)))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'rename.execute'").fetchone()[0])

    def test_maintenance_does_not_attribute_old_success_to_rejected_request(self):
        _, operation = self._safeDryRun()
        execute_rename_operation(self.settings, operation.id)

        def rejectAfterMaintenance():
            report = flushFileActionAuditIntents(self.settings)
            self.assertEqual(0, report["completed"])
            self.assertEqual(1, report["pending"])
            raise ValueError("synthetic duplicate request")

        with self.assertRaisesRegex(ValueError, "duplicate request"):
            runAuditedFileAction(self.settings, taskType="rename_operation", taskId=operation.id,
                context=AuditWriteContext(), execute=rejectAfterMaintenance, response=Response())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute("SELECT result,detail_json FROM audit_events WHERE event_type='rename.execute' ORDER BY id").fetchall()
        self.assertEqual(["pending", "failed"], [row[0] for row in rows])
        self.assertEqual("completed", json.loads(rows[-1][1])["status"])
        self.assertEqual("synthetic duplicate request", json.loads(rows[-1][1])["request_error"])

    def test_repeated_execute_api_returns_existing_result_without_false_success_audit(self):
        _, operation = self._safeDryRun()
        client = TestClient(create_app(self.settings))
        first = client.post(f"/api/rename-operations/{operation.id}/execute")
        self.assertEqual(200, first.status_code)
        self.assertEqual("completed", first.json()["status"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='rename.execute' AND result='success'"
            ).fetchone()[0]

        repeated = client.post(f"/api/rename-operations/{operation.id}/execute")
        self.assertEqual(200, repeated.status_code)
        self.assertEqual("completed", repeated.json()["status"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='rename.execute' AND result='success'"
            ).fetchone()[0]
            intents = connection.execute(
                "SELECT COUNT(*) FROM file_action_audit_intents "
                "WHERE task_type='rename_operation' AND task_id=?",
                (operation.id,),
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(1, intents)

    def test_rejected_running_request_is_audited_without_finishing_business_task(self):
        _, operation = self._safeDryRun()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE rename_operations SET status='running' WHERE id=?", (operation.id,))
            connection.commit()
        def reject():
            raise ValueError("synthetic busy")
        with self.assertRaisesRegex(ValueError, "synthetic busy"):
            runAuditedFileAction(self.settings, taskType="rename_operation", taskId=operation.id,
                context=AuditWriteContext(), execute=reject,
                response=Response())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("running", connection.execute("SELECT status FROM rename_operations WHERE id=?", (operation.id,)).fetchone()[0])
            self.assertEqual("failed", connection.execute("SELECT result FROM audit_events WHERE event_type='rename.execute' ORDER BY id DESC LIMIT 1").fetchone()[0])

    def test_execution_rejects_edited_or_excluded_preview(self):
        for action in ("edit", "exclude"):
            with self.subTest(action=action):
                preview, operation = self._safeDryRun()
                if action == "edit":
                    update_rename_preview(self.settings, preview.id, "Movie.New")
                else:
                    exclude_rename_preview(self.settings, preview.id)
                executed = execute_rename_operation(self.settings, operation.id)
                self.assertEqual("failed", executed.status)
                self.assertIn("重新生成干跑", executed.items[0].message)
                self.assertTrue(self.source.exists())
                self.assertFalse((self.media_dir / "Movie.Safe.mkv").exists())

    def test_execution_rejects_changed_file_source_or_missing_snapshot(self):
        preview, operation = self._safeDryRun()
        self.source.write_text("changed movie", encoding="utf-8")
        result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(1, result.failed_count)
        self.assertTrue(self.source.exists())
        for change in ("source", "legacy"):
            with self.subTest(change=change):
                operation = create_rename_dry_run(self.settings, [preview.id])
                with closing(sqlite3.connect(self.settings.database_path)) as connection:
                    if change == "source":
                        connection.execute("UPDATE media_sources SET updated_at = 'changed' WHERE id = 1")
                    else:
                        connection.execute(
                            "UPDATE rename_operation_items SET snapshot_json = NULL WHERE operation_id = ?",
                            (operation.id,),
                        )
                    connection.commit()
                result = execute_rename_operation(self.settings, operation.id)
                self.assertEqual(1, result.failed_count)
                self.assertIn("重新生成干跑", result.items[0].message)
                self.assertTrue(self.source.exists())

    def test_duplicate_execution_and_preview_mutations_are_blocked_while_running(self):
        preview, operation = self._safeDryRun()
        competing = create_rename_dry_run(self.settings, [preview.id])
        entered = Event()
        proceed = Event()

        def checkReady(*args):
            entered.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError("测试等待超时")
            return ConnectionTestResult(True, "可执行")

        with patch("app.service.shared_protocols.local.LocalProtocol.check_rename_ready", side_effect=checkReady) as readiness:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(execute_rename_operation, self.settings, operation.id)
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    duplicate = execute_rename_operation(self.settings, operation.id)
                    self.assertEqual("running", duplicate.status)
                    with self.assertRaisesRegex(ValueError, "其他批次"):
                        execute_rename_operation(self.settings, competing.id)
                    for mutate in (
                        lambda: update_rename_preview(self.settings, preview.id, "Other"),
                        lambda: exclude_rename_preview(self.settings, preview.id),
                        lambda: generate_rename_previews(self.settings),
                    ):
                        with self.assertRaisesRegex(ValueError, "正在执行"):
                            mutate()
                finally:
                    proceed.set()
                result = future.result(timeout=10)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.renamed_count)
        self.assertEqual(1, readiness.call_count)

    def test_execute_updates_scan_file_index_after_successful_rename(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Index")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO scan_file_index "
                "(media_source_id, file_path, normalized_path, file_name, extension, file_size, "
                "modified_at, fingerprint, last_scan_job_id, last_seen_at, status, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, '.mkv', 5, 'now', 'old', 1, 'now', 'active', 'now', 'now')",
                (str(self.source), self.source.name.lower(), self.source.name),
            )
            connection.commit()
        operation = create_rename_dry_run(self.settings, [preview.id])

        execute_rename_operation(self.settings, operation.id)

        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            source_row = connection.execute(
                "SELECT status, rename_preview_id FROM scan_file_index WHERE normalized_path = ?",
                (self.source.name.lower(),),
            ).fetchone()
            target_row = connection.execute(
                "SELECT status, rename_preview_id, file_name FROM scan_file_index WHERE normalized_path = ?",
                ("movie.index.mkv",),
            ).fetchone()

        self.assertEqual("renamed", source_row["status"])
        self.assertEqual(preview.id, source_row["rename_preview_id"])
        self.assertEqual("active", target_row["status"])
        self.assertEqual(preview.id, target_row["rename_preview_id"])
        self.assertEqual("Movie.Index.mkv", target_row["file_name"])

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

    def test_systemic_credential_failure_stops_remaining_items(self):
        second_source = self.media_dir / "Movie.2025.2160p.mkv"
        second_source.write_text("movie-2", encoding="utf-8")
        self._insert_additional_media_file(2, second_source)
        generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)
        update_rename_preview(self.settings, previews[0].id, "Movie.First")
        update_rename_preview(self.settings, previews[1].id, "Movie.Second")
        operation = create_rename_dry_run(self.settings, [preview.id for preview in previews])

        with (
            patch(
                "app.service.rename_operation_service._validateItemSnapshot",
                side_effect=CredentialError("凭据密钥缺失"),
            ) as validate_snapshot,
            patch("app.service.rename_operation_service.moveFileNoReplace") as mover,
        ):
            result = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", result.status)
        self.assertEqual(["failed", "ready"], [item.status for item in result.items])
        self.assertIn("凭据密钥缺失", result.items[0].message or "")
        self.assertEqual(1, validate_snapshot.call_count)
        mover.assert_not_called()
        self.assertTrue(self.source.exists())
        self.assertTrue(second_source.exists())

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

    def test_dry_run_rejects_batch_over_operation_limit(self):
        update_setting_values(self.settings, {"operations.batch_limit": 1}, operator="test")
        second_source = self.media_dir / "Movie.2025.2160p.mkv"
        second_source.write_text("movie-2", encoding="utf-8")
        self._insert_additional_media_file(2, second_source)
        generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)

        with self.assertRaises(ValueError):
            create_rename_dry_run(self.settings, [preview.id for preview in previews])

    def test_dry_run_uses_media_source_protocol_validation(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE media_sources SET path_type = 'unc', protocol = 'unc' WHERE id = 1"
            )
            connection.commit()
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Protocol")

        operation = create_rename_dry_run(self.settings, [preview.id])

        self.assertEqual(0, operation.ready_count)
        self.assertEqual(1, operation.conflict_count)
        self.assertEqual("conflict", operation.items[0].status)
        self.assertEqual("UNC 路径格式不正确", operation.items[0].message)

    def test_webdav_dry_run_uses_remote_url_target_without_local_path_check(self):
        self._insert_webdav_preview()

        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=self._fake_webdav_dry_run_urlopen):
            operation = create_rename_dry_run(self.settings, [3])

        self.assertEqual(1, operation.ready_count)
        self.assertEqual(0, operation.conflict_count)
        self.assertEqual("ready", operation.items[0].status)
        self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", operation.items[0].target_path)

    def test_webdav_execute_ready_item_runs_move_and_updates_records(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=self._fake_webdav_dry_run_urlopen):
            operation = create_rename_dry_run(self.settings, [3])
        captured_move = {}

        def fake_move_file(source_path, target_path, context=None):
            captured_move["source_path"] = source_path
            captured_move["target_path"] = target_path
            captured_move["context_path_type"] = context.path_type if context else None
            from app.service.shared_protocols.base import ConnectionTestResult

            return ConnectionTestResult(True, "WebDAV MOVE 执行成功", readable=True, writable=True)

        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", side_effect=fake_move_file):
            executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("completed", executed.status)
        self.assertEqual(1, executed.renamed_count)
        self.assertEqual(0, executed.failed_count)
        self.assertEqual("renamed", executed.items[0].status)
        self.assertEqual("https://nas.example/dav/Movie.2024.1080p.mkv", captured_move["source_path"])
        self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", captured_move["target_path"])
        self.assertEqual("webdav", captured_move["context_path_type"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            media_row = connection.execute(
                "SELECT file_path, file_name, extension FROM media_files WHERE id = 3"
            ).fetchone()
            preview_row = connection.execute(
                "SELECT status FROM rename_previews WHERE id = 3"
            ).fetchone()
            remote_row = connection.execute(
                "SELECT operation_type, source_path, target_path, status, recovery_json "
                "FROM remote_operation_items"
            ).fetchone()
        self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", media_row[0])
        self.assertEqual("Movie.Safe.mkv", media_row[1])
        self.assertEqual(".mkv", media_row[2])
        self.assertEqual("renamed", preview_row[0])
        self.assertEqual(
            ("rename", "https://nas.example/dav/Movie.2024.1080p.mkv", "https://nas.example/dav/Movie.Safe.mkv", "completed"),
            remote_row[:4],
        )
        recovery = json.loads(remote_row[4])
        self.assertEqual(operation.id, recovery["operation_id"])
        self.assertEqual(executed.items[0].id, recovery["operation_item_id"])
        self.assertEqual(3, recovery["rename_preview_id"])

    def test_webdav_execute_is_blocked_by_active_remote_lock(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=self._fake_webdav_dry_run_urlopen):
            operation = create_rename_dry_run(self.settings, [3])
        from app.service.remote_operation_service import acquire_remote_operation_lock

        acquire_remote_operation_lock(
            self.settings,
            media_source_id=2,
            lock_key="media-source:2:write",
            owner="other",
            task_type="rename_operation",
            task_id=999,
            ttl_seconds=60,
        )

        executed = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", executed.status)
        self.assertEqual(1, executed.failed_count)
        self.assertIn("远程媒体源正在执行写操作", executed.items[0].message)

    def test_two_remote_items_commit_individually_without_holding_write_lock_during_move(self):
        self._insert_webdav_preview()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_files (id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (4, 2, 2, ?, 'Second.mkv', '.mkv', 2048, 'now', 'now')",
                ("https://nas.example/dav/Second.mkv",),
            )
            connection.execute(
                "INSERT INTO rename_previews (id, media_file_id, media_type, parsed_title, original_extension, "
                "suggested_name, status, created_at, updated_at) "
                "VALUES (4, 4, 'movie', 'Second', '.mkv', 'Second.Safe.mkv', 'generated', 'now', 'now')"
            )
            connection.commit()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3, 4])
        moves = []

        def moveFile(sourcePath, targetPath, context=None):
            with closing(sqlite3.connect(self.settings.database_path, timeout=0.1)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if moves:
                    self.assertEqual("renamed", connection.execute(
                        "SELECT status FROM rename_operation_items WHERE id = ?", (operation.items[0].id,),
                    ).fetchone()[0])
                connection.rollback()
            moves.append(sourcePath)
            return ConnectionTestResult(True, "已移动")

        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", side_effect=moveFile):
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(2, len(moves))
        self.assertEqual(2, result.renamed_count)
        self.assertEqual(0, result.failed_count)

    def test_remote_move_success_with_business_failure_remains_recoverable(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", return_value=ConnectionTestResult(True, "已移动")),
            patch("app.service.rename_operation_service._update_successful_remote_media_record", side_effect=sqlite3.OperationalError("注入业务提交失败")),
        ):
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual("failed", result.status)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            remoteRow = connection.execute("SELECT status, error_message FROM remote_operation_items").fetchone()
            mediaPath = connection.execute("SELECT file_path FROM media_files WHERE id = 3").fetchone()[0]
            lockStatus = connection.execute("SELECT status FROM remote_operation_locks").fetchone()[0]
        self.assertEqual("recovery_required", remoteRow[0])
        self.assertIn("远端已移动", remoteRow[1])
        self.assertTrue(mediaPath.endswith("Movie.2024.1080p.mkv"))
        self.assertEqual("released", lockStatus)

    def test_remote_move_indeterminate_remains_recovery_required(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        with patch(
            "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
            return_value=ConnectionTestResult(False, "HTTP 204 结果待核查", indeterminate=True),
        ) as mover:
            result = execute_rename_operation(self.settings, operation.id)

        self.assertEqual("failed", result.status)
        self.assertEqual(1, result.failed_count)
        mover.assert_called_once()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            remoteRow = connection.execute(
                "SELECT status, error_message FROM remote_operation_items"
            ).fetchone()
        self.assertEqual("recovery_required", remoteRow[0])
        self.assertIn("人工核查", remoteRow[1])

    def test_remote_intent_failure_releases_lease_and_does_not_move(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        with (
            patch("app.service.rename_operation_service.create_remote_operation_item", side_effect=sqlite3.OperationalError("注入意图失败")),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover,
        ):
            result = execute_rename_operation(self.settings, operation.id)
        mover.assert_not_called()
        self.assertEqual("failed", result.status)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("released", connection.execute("SELECT status FROM remote_operation_locks").fetchone()[0])

    def test_remote_identity_change_after_dry_run_does_not_move(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        changed = RemoteFileEntry(
            path="https://nas.example/dav/Movie.2024.1080p.mkv", name="Movie.2024.1080p.mkv",
            extension=".mkv", file_size=2048, modified_at="2026-01-01T00:00:00+00:00", version='"replaced"',
        )
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=changed),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover,
        ):
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(1, result.failed_count)
        mover.assert_not_called()

    def test_remote_execution_passes_dry_run_etag_for_conditional_move(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", return_value=ConnectionTestResult(True, "已移动")) as mover:
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual("completed", result.status)
        self.assertEqual('"source-etag"', mover.call_args.args[2].expected_source_version)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            index = connection.execute(
                "SELECT file_path, status, fingerprint, rename_preview_id FROM scan_file_index WHERE media_source_id = 2"
            ).fetchone()
            targetVersion = connection.execute("SELECT target_version FROM remote_operation_items").fetchone()[0]
        self.assertEqual("https://nas.example/dav/Movie.Safe.mkv", index[0])
        self.assertEqual("active", index[1])
        self.assertIn('"source-etag"', index[2])
        self.assertEqual(3, index[3])
        self.assertEqual('"source-etag"', targetVersion)

    def test_remote_dry_run_requires_strong_etag(self):
        self._insert_webdav_preview()
        for version in (None, 'W/"weak"'):
            with (
                self.subTest(version=version),
                patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")),
                patch("app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=RemoteFileEntry(
                    path="https://nas.example/dav/Movie.2024.1080p.mkv", name="Movie.mkv", extension=".mkv",
                    file_size=2048, modified_at="", version=version,
                )),
            ):
                with self.assertRaisesRegex(ValueError, "强 ETag"):
                    create_rename_dry_run(self.settings, [3])

    def test_remote_lock_release_failure_does_not_erase_committed_rename(self):
        self._insert_webdav_preview()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "可执行")):
            operation = create_rename_dry_run(self.settings, [3])
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", return_value=ConnectionTestResult(True, "已移动")), patch(
                "app.service.rename_operation_service.release_remote_operation_lock", side_effect=sqlite3.OperationalError("synthetic release failure")):
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(("completed", 1, 0, 0), (result.status, result.renamed_count, result.failed_count, result.ready_count))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("completed", connection.execute("SELECT status FROM remote_operation_items").fetchone()[0])
            self.assertEqual("active", connection.execute("SELECT status FROM remote_operation_locks").fetchone()[0])
            self.assertEqual("renamed", connection.execute("SELECT status FROM rename_operation_items WHERE operation_id=?", (operation.id,)).fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM operation_logs WHERE task_id=? AND stage='rename_lock_release_failed'", (operation.id,)).fetchone()[0])

    def _insert_webdav_preview(self):
        infoPatch = patch(
            "app.service.shared_protocols.webdav.WebDavProtocol.get_file_info",
            side_effect=lambda path, context=None: RemoteFileEntry(
                path=path, name=path.rsplit("/", 1)[-1], extension=".mkv",
                file_size=2048, modified_at="2026-01-01T00:00:00+00:00", version='"source-etag"',
            ),
        )
        infoPatch.start()
        self.addCleanup(infoPatch.stop)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources "
                "(id, name, path, path_type, protocol, username, encrypted_secret, auth_type, credential_version, "
                "capability_snapshot_json, enabled, created_at, updated_at) "
                "VALUES (2, 'webdav', 'https://nas.example/dav/', 'webdav', 'webdav', NULL, NULL, 'none', 1, ?, 1, 'now', 'now')",
                (json.dumps({"conditional_move": {"status": "verified", "endpoint": "https://nas.example/dav/",
                    "credential_version": 1}}),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (2, 2, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES "
                "(3, 2, 2, 'https://nas.example/dav/Movie.2024.1080p.mkv', "
                "'Movie.2024.1080p.mkv', '.mkv', 2048, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_previews "
                "(id, media_file_id, media_type, parsed_title, parsed_year, season, episode, original_extension, "
                "suggested_name, edited_name, status, created_at, updated_at) "
                "VALUES (3, 3, 'movie', 'Movie', 2024, NULL, NULL, '.mkv', "
                "'Movie.2024.mkv', 'Movie.Safe.mkv', 'generated', 'now', 'now')"
            )
            connection.commit()

    def _fake_webdav_dry_run_urlopen(self, request, timeout=5):
        class FakeResponse:
            status = 207

            def __init__(self, body: str):
                self.body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, size=-1):
                return self.body if size < 0 else self.body[:size]

        source_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/Movie.2024.1080p.mkv</d:href>
            <d:propstat><d:prop>
              <d:resourcetype />
              <d:getcontentlength>2048</d:getcontentlength>
              <d:getetag>"source-etag"</d:getetag>
            </d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """
        if request.full_url.endswith("/Movie.Safe.mkv"):
            raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)
        return FakeResponse(source_xml)

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
