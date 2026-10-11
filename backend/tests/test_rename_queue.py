"""仅临时媒体的后台重命名受理、授权和真实进程回归。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
from app.core.logger import shutdown_logging
from app.service.audit_service import AuditWriteContext
from app.service.media_source_service import create_media_source, delete_media_source
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation, get_rename_operation
from app.service.rename_queue_service import (
    acceptRename, claimRename, executeAcceptedRename, finishInterruptedRename, heartbeatRename, recoverExpiredRenames,
)
from app.service.rename_worker_service import startRenameProcess, runRenameWorkerLoop
from app.service.scan_service import run_full_scan
from app.service.settings_service import update_setting_values


class RenameQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(shutdown_logging)
        self.root = Path(self.temp.name)
        media = self.root / "media"
        media.mkdir()
        for name in ("Movie.2024.1080p.mkv", "Other.2024.1080p.mkv"):
            (media / name).write_bytes(b"temporary media")
        self.settings = AppSettings(data_dir=self.root / "data", database_path=self.root / "data/test.db",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            scan=ScanSettings(batch_size=100, batch_interval_seconds=0))
        ensure_database(self.settings)
        self.source = create_media_source(self.settings, "temporary source", media)
        update_setting_values(self.settings, {"scan.minimum_file_size": 0, "scan.batch_interval_seconds": 0})
        job = run_full_scan(self.settings, self.source.id)
        generate_rename_previews(self.settings, scan_job_id=job.id)
        self.previews = list_rename_previews(self.settings, scan_job_id=job.id)
        self.operation = create_rename_dry_run(self.settings, [p.id for p in self.previews])
        self.assertEqual(2, self.operation.ready_count)

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(query, args).fetchall()
            connection.commit()
            return rows

    def accept(self):
        return acceptRename(self.settings, self.operation.id, AuditWriteContext())

    def expire(self):
        self.sql("UPDATE rename_operations SET execution_expires_at='2000' WHERE id=?", (self.operation.id,))

    def test_accept_only_uses_database_and_duplicate_has_one_intent(self):
        with patch("app.service.rename_operation_service.get_protocol", side_effect=AssertionError("no directory probe")):
            first = self.accept()
            second = self.accept()
        self.assertEqual("queued", first.status)
        self.assertEqual(first, second)
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM file_action_audit_intents")[0][0])
        self.assertTrue(Path(self.operation.items[0].source_path).exists())
        self.assertEqual("queued", execute_rename_operation(self.settings, self.operation.id).status)

    def test_accept_audit_failure_rolls_back_status_and_intent(self):
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                self.accept()
        self.assertEqual("dry_run", get_rename_operation(self.settings, self.operation.id).status)
        self.assertEqual([], self.sql("SELECT id FROM file_action_audit_intents"))

    def test_two_acceptors_and_claimers_do_not_duplicate_work(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            accepted = list(executor.map(lambda _: self.accept(), range(2)))
        self.assertEqual(accepted[0], accepted[1])
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: claimRename(self.settings), range(2)))
        self.assertEqual(1, sum(claim is not None for claim in claims))

    def test_queued_conflicting_batch_preview_mutation_and_source_retirement_are_blocked(self):
        other = create_rename_dry_run(self.settings, [self.previews[0].id])
        self.accept()
        with self.assertRaisesRegex(ValueError, "已有"):
            acceptRename(self.settings, other.id, AuditWriteContext())
        with self.assertRaisesRegex(ValueError, "正在其他批次"):
            execute_rename_operation(self.settings, other.id)
        with self.assertRaisesRegex(ValueError, "暂不能"):
            update_rename_preview(self.settings, self.previews[0].id, "edited.mkv")
        with self.assertRaisesRegex(ValueError, "存在运行中"):
            delete_media_source(self.settings, self.source.id)

    def test_valid_execution_finishes_but_slot_stays_reserved_until_exit(self):
        self.accept()
        operationId, token = claimRename(self.settings)
        result = executeAcceptedRename(self.settings, operationId, token)
        self.assertEqual("completed", result.status)
        self.assertEqual(2, result.renamed_count)
        self.assertIsNone(claimRename(self.settings))
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM rename_operations WHERE execution_token IS NOT NULL")[0][0])
        finishInterruptedRename(self.settings, operationId, token, "process exited", processExited=True)
        self.assertEqual([], self.sql("SELECT id FROM rename_operations WHERE execution_token IS NOT NULL"))
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents")[0][0])
        for item in result.items:
            self.assertTrue(Path(item.target_path).exists())
            self.assertFalse(Path(item.source_path).exists())

    def test_synchronous_execution_persists_owner_before_first_file_move(self):
        from app.utils.file_operations import moveFileNoReplace

        observedOwners = []

        def inspectOwnerAndMove(source, target):
            owner = self.sql(
                "SELECT execution_token,execution_pid,execution_instance_id,execution_started_at "
                "FROM rename_operations WHERE id=?",
                (self.operation.id,),
            )[0]
            observedOwners.append(owner)
            self.assertTrue(all(value is not None for value in owner))
            moveFileNoReplace(source, target)

        with patch("app.service.rename_operation_service.moveFileNoReplace", side_effect=inspectOwnerAndMove):
            result = execute_rename_operation(self.settings, self.operation.id)

        self.assertEqual("completed", result.status)
        self.assertEqual(2, len(observedOwners))
        self.assertEqual((None, None, None, None), self.sql(
            "SELECT execution_token,execution_pid,execution_instance_id,execution_started_at "
            "FROM rename_operations WHERE id=?",
            (self.operation.id,),
        )[0])

    def test_expired_or_wrong_token_cannot_execute_or_release_slot(self):
        self.accept()
        operationId, token = claimRename(self.settings)
        with self.assertRaisesRegex(ValueError, "授权已失效"):
            executeAcceptedRename(self.settings, operationId, "wrong")
        finishInterruptedRename(self.settings, operationId, "wrong", "wrong", processExited=True)
        self.expire()
        self.assertFalse(heartbeatRename(self.settings, operationId, token))
        with self.assertRaisesRegex(ValueError, "授权已失效"):
            executeAcceptedRename(self.settings, operationId, token)
        self.assertEqual(1, recoverExpiredRenames(self.settings))
        self.assertEqual("interrupted", get_rename_operation(self.settings, operationId).status)
        self.assertIsNone(claimRename(self.settings))

    def test_permission_revocation_after_first_item_stops_without_erasing_success(self):
        from app.service.auth_service import create_user
        from app.utils.file_operations import moveFileNoReplace
        user = create_user(self.settings, "renamer", "temporary", "SyntheticPassword123!", ["rename:execute"], True)
        acceptRename(self.settings, self.operation.id, AuditWriteContext(user))
        operationId, token = claimRename(self.settings)
        def move(source, target):
            moveFileNoReplace(source, target)
            self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (user.id,))
        with patch("app.service.rename_operation_service.moveFileNoReplace", side_effect=move):
            with self.assertRaisesRegex(ValueError, "权限被撤销"):
                executeAcceptedRename(self.settings, operationId, token)
        result = get_rename_operation(self.settings, operationId)
        self.assertEqual((1, 0, 1), (result.renamed_count, result.failed_count, result.ready_count))
        finishInterruptedRename(self.settings, operationId, token, "permission revoked", processExited=True)
        self.assertEqual("interrupted", get_rename_operation(self.settings, operationId).status)

    def test_real_hidden_worker_renames_only_temporary_media(self):
        self.accept()
        operationId, token = claimRename(self.settings)
        process = startRenameProcess(self.settings, operationId, token)
        try:
            self.assertEqual(0, process.wait(timeout=20))
            self.assertEqual(2, get_rename_operation(self.settings, operationId).renamed_count)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            finishInterruptedRename(self.settings, operationId, token, "temporary worker exited", processExited=True)

    def test_supervisor_stops_and_reaps_before_clearing_lease(self):
        self.accept()
        stop = asyncio.Event()
        owner = self
        class FakeProcess:
            exited = False
            waited = False
            def poll(self): return -9 if self.exited else None
            def kill(self):
                owner.assertEqual(1, owner.sql("SELECT COUNT(*) FROM rename_operations WHERE execution_token IS NOT NULL")[0][0])
                self.exited = True
            def wait(self): self.waited = True
        process = FakeProcess()
        def renew(*_): stop.set(); return True
        with patch("app.service.rename_worker_service.startRenameProcess", return_value=process), patch(
            "app.service.rename_worker_service.heartbeatRename", side_effect=renew):
            asyncio.run(runRenameWorkerLoop(self.settings, stop))
        self.assertTrue(process.waited)
        self.assertEqual("interrupted", get_rename_operation(self.settings, self.operation.id).status)
        self.assertEqual([], self.sql("SELECT id FROM rename_operations WHERE execution_token IS NOT NULL"))

    def test_v30_upgrade_preserves_history_and_does_not_queue_old_dry_run(self):
        columns = ("execution_token", "execution_pid", "execution_expires_at", "actor_id", "actor_name",
                   "audit_context_json", "audit_intent_id", "error_message")
        self.sql("DROP INDEX idx_rename_operations_queue")
        self.sql("DROP INDEX idx_rename_execution_slot")
        for column in columns:
            self.sql(f"ALTER TABLE rename_operations DROP COLUMN {column}")
        self.sql("UPDATE app_meta SET value='30' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(self.operation, get_rename_operation(self.settings, self.operation.id))
        self.assertIsNone(claimRename(self.settings))
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])

    def test_api_returns_202_readable_task_and_sync_endpoint_does_not_execute_queue(self):
        from api_test_support import authenticatedClient
        from app.main import create_app
        client = authenticatedClient(create_app(self.settings))
        with patch("app.service.rename_operation_service.moveFileNoReplace", side_effect=AssertionError("no file write")):
            response = client.post(f"/api/rename-operations/{self.operation.id}/execute-async", json={})
            self.assertEqual(202, response.status_code)
            self.assertEqual("queued", response.json()["status"])
            self.assertEqual(202, client.post(f"/api/rename-operations/{self.operation.id}/execute-async", json={}).status_code)
            self.assertEqual("queued", client.get(f"/api/rename-operations/{self.operation.id}").json()["status"])
            self.assertEqual("queued", client.post(f"/api/rename-operations/{self.operation.id}/execute", json={}).json()["status"])
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM file_action_audit_intents")[0][0])
        self.sql("UPDATE users SET permissions_json='[]'")
        self.assertEqual(403, client.post(f"/api/rename-operations/{self.operation.id}/execute-async", json={}).status_code)

    def test_lease_expiring_after_first_move_preserves_known_commit_and_never_replays(self):
        from app.utils.file_operations import moveFileNoReplace
        self.accept(); operationId, token = claimRename(self.settings)
        def move(source, target):
            moveFileNoReplace(source, target); self.expire()
        with patch("app.service.rename_operation_service.moveFileNoReplace", side_effect=move):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRename(self.settings, operationId, token)
        result = get_rename_operation(self.settings, operationId)
        self.assertEqual((1, 0, 1), (result.renamed_count, result.failed_count, result.ready_count))
        finishInterruptedRename(self.settings, operationId, token, "expired during move", processExited=True)
        self.assertEqual("interrupted", self.accept().status)
        self.assertIsNone(claimRename(self.settings))

    def test_readonly_recovery_and_confirmed_dead_process_release_do_not_rename(self):
        from app.service.rename_queue_service import inspectInterruptedRenames, releaseInterruptedRename
        self.accept(); operationId, token = claimRename(self.settings)
        self.sql("UPDATE rename_operations SET execution_pid=12345 WHERE id=?", (operationId,))
        self.expire(); recoverExpiredRenames(self.settings)
        before = self.settings.database_path.read_bytes()
        report = inspectInterruptedRenames(self.settings, limit=1)
        self.assertEqual(operationId, report["items"][0]["id"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "确认服务已停止"):
            releaseInterruptedRename(self.settings, operationId, confirmStopped=False)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=True):
            with self.assertRaisesRegex(ValueError, "拒绝释放"):
                releaseInterruptedRename(self.settings, operationId, confirmStopped=True)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False), patch(
                "app.service.rename_operation_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            releaseInterruptedRename(self.settings, operationId, confirmStopped=True)
        self.assertEqual([], inspectInterruptedRenames(self.settings)["items"])
        self.assertEqual("interrupted", get_rename_operation(self.settings, operationId).status)
        self.assertTrue(Path(self.operation.items[0].source_path).exists())

    def test_final_audit_failure_keeps_slot_and_fixed_outcome_for_later_flush(self):
        from app.service.file_action_audit_service import flushFileActionAuditIntents
        self.accept(); operationId, token = claimRename(self.settings)
        executeAcceptedRename(self.settings, operationId, token)
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                finishInterruptedRename(self.settings, operationId, token, "exited", processExited=True)
        self.assertIsNone(claimRename(self.settings))
        self.assertEqual(1, flushFileActionAuditIntents(self.settings)["completed"])
        finishInterruptedRename(self.settings, operationId, token, "exited", processExited=True)
        self.assertEqual("completed", get_rename_operation(self.settings, operationId).status)

    def test_corrupted_expiry_is_not_treated_as_live_authorization(self):
        self.accept(); operationId, token = claimRename(self.settings)
        self.sql("UPDATE rename_operations SET execution_expires_at='not-a-date' WHERE id=?", (operationId,))
        self.assertFalse(heartbeatRename(self.settings, operationId, token))
        with self.assertRaisesRegex(ValueError, "授权已失效"):
            executeAcceptedRename(self.settings, operationId, token)
        self.assertEqual(1, recoverExpiredRenames(self.settings))

    def test_recovery_missing_database_does_not_initialize_it(self):
        from dataclasses import replace
        from app.service.rename_queue_service import inspectInterruptedRenames, releaseInterruptedRename
        missing = replace(self.settings, database_path=self.root / "missing.db")
        with self.assertRaises(sqlite3.OperationalError): inspectInterruptedRenames(missing)
        with self.assertRaises(sqlite3.OperationalError): releaseInterruptedRename(missing, 1, confirmStopped=True)
        self.assertFalse(missing.database_path.exists())

    def test_permission_or_lease_loss_during_intent_preparation_never_moves_file(self):
        from app.service.local_file_action_service import prepareLocalFileAction
        self.accept(); operationId, token = claimRename(self.settings)
        def prepare(*args, **kwargs):
            intentId = prepareLocalFileAction(*args, **kwargs)
            self.expire()
            return intentId
        with patch("app.service.rename_operation_service.prepareLocalFileAction", side_effect=prepare), patch(
                "app.service.rename_operation_service.moveFileNoReplace", side_effect=AssertionError("no move after expiration")):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRename(self.settings, operationId, token)
        self.assertTrue(Path(self.operation.items[0].source_path).exists())
        self.assertEqual("prepared", self.sql("SELECT status FROM local_file_actions")[0][0])

    def test_partial_running_task_cannot_create_rollback_before_process_exit(self):
        from app.service.rename_rollback_service import create_rename_rollback_plan
        self.accept(); operationId, token = claimRename(self.settings)
        executeAcceptedRename(self.settings, operationId, token)
        with self.assertRaisesRegex(ValueError, "进程待核对"):
            create_rename_rollback_plan(self.settings, operationId)
        finishInterruptedRename(self.settings, operationId, token, "exited", processExited=True)
        self.assertEqual(2, create_rename_rollback_plan(self.settings, operationId).item_count)

    def test_recovery_cli_requires_explicit_config_and_apply_gates(self):
        from app.recover_renames import main
        from contextlib import redirect_stdout, redirect_stderr
        import io
        import json
        config = self.root / "queue.toml"
        config.write_text(f"[paths]\ndata_dir='{self.settings.data_dir.as_posix()}'\n"
                          f"database_path='{self.settings.database_path.as_posix()}'\n", encoding="utf-8")
        before = self.settings.database_path.read_bytes()
        output = io.StringIO()
        with patch("sys.argv", ["recover_renames", "--config", str(config)]), redirect_stdout(output):
            self.assertEqual(0, main())
        self.assertEqual([], json.loads(output.getvalue())["items"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with patch("sys.argv", ["recover_renames", "--config", str(config), "--apply", "--operation-id", "1"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised: main()
        self.assertEqual(2, raised.exception.code)

    def test_application_lifespan_consumes_queue_and_commits_result_audit(self):
        from api_test_support import authenticatedClient
        from app.main import create_app
        import time
        with authenticatedClient(create_app(self.settings)) as client:
            response = client.post(f"/api/rename-operations/{self.operation.id}/execute-async", json={})
            self.assertEqual(202, response.status_code)
            self.assertEqual(self.operation.id, response.json()["id"])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = client.get(f"/api/rename-operations/{self.operation.id}").json()
                if result["status"] not in {"queued", "running"}: break
                time.sleep(0.05)
            self.assertEqual("completed", result["status"])
            self.assertEqual(2, result["renamed_count"])
            tasks = client.get("/api/tasks/page", params={"task_type": "rename_operation"}).json()["items"]
            task = next(item for item in tasks if item["task_id"] == self.operation.id)
            self.assertEqual(str(self.operation.id), task["target_query"]["operation_id"])
        self.assertEqual([], self.sql("SELECT id FROM rename_operations WHERE execution_token IS NOT NULL"))
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents")[0][0])
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM audit_events WHERE event_type='rename.execute' AND result='success'")[0][0])

    def test_batch_reads_and_preview_conflict_lookup_use_bounded_index_ranges(self):
        plans = (
            ("SELECT id FROM rename_operation_items WHERE operation_id=? ORDER BY id", self.operation.id, "idx_rename_items_operation"),
            ("SELECT operation_id FROM rename_operation_items WHERE rename_preview_id=?", self.previews[0].id, "idx_rename_items_preview"),
        )
        for query, value, index in plans:
            with self.subTest(index=index):
                plan = " ".join(str(row[3]) for row in self.sql("EXPLAIN QUERY PLAN " + query, (value,)))
                self.assertIn(index, plan)
                self.assertNotIn("SCAN rename_operation_items", plan)
