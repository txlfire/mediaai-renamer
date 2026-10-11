"""回滚后台链仅使用临时媒体，覆盖受理及跨任务文件槽。"""

import sqlite3
from pathlib import Path
from contextlib import closing
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_rename_queue as support
from app.service.audit_service import AuditWriteContext
from app.service.rename_operation_service import execute_rename_operation
from app.service.rename_rollback_service import create_rename_rollback_plan, dry_run_rename_rollback_plan, get_rename_rollback_plan
from app.service.rollback_queue_service import acceptRollback, claimRollback, executeAcceptedRollback, finishInterruptedRollback


class RollbackQueueTest(unittest.TestCase):
    def setUp(self):
        fixture = support.RenameQueueTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.settings, self.root, self.sql = fixture.settings, fixture.root, fixture.sql
        self.operation = execute_rename_operation(self.settings, fixture.operation.id)
        plan = create_rename_rollback_plan(self.settings, self.operation.id)
        self.plan = dry_run_rename_rollback_plan(self.settings, plan.id)

    def accept(self):
        return acceptRollback(self.settings, self.plan.id, AuditWriteContext())

    def test_accept_does_not_probe_files_and_duplicate_has_one_intent(self):
        with patch("app.service.rename_rollback_service.get_protocol", side_effect=AssertionError("no file probe")):
            first, second = self.accept(), self.accept()
        self.assertEqual(first, second)
        self.assertEqual("queued", first.status)
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM file_action_audit_intents WHERE task_type='rollback_plan'")[0][0])

    def test_audit_failure_rolls_back_acceptance(self):
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError): self.accept()
        self.assertEqual("checked", get_rename_rollback_plan(self.settings, self.plan.id).status)
        self.assertEqual([], self.sql("SELECT id FROM file_action_audit_intents"))

    def test_concurrent_accept_and_claim_are_single_use(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            accepted = list(executor.map(lambda _: self.accept(), range(2)))
        self.assertEqual(accepted[0], accepted[1])
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: claimRollback(self.settings), range(2)))
        self.assertEqual(1, sum(claim is not None for claim in claims))

    def test_complete_keeps_slot_until_process_exit_and_records_real_counts(self):
        from app.service.rename_queue_service import claimRename
        self.accept(); planId, token = claimRollback(self.settings)
        result = executeAcceptedRollback(self.settings, planId, token)
        self.assertEqual(("executed", 2, 0), (result.status, result.rolled_back_count, result.failed_count))
        self.assertIsNone(claimRename(self.settings))
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM rename_rollback_plans WHERE execution_token IS NOT NULL")[0][0])
        finishInterruptedRollback(self.settings, planId, token, "temporary worker exited", processExited=True)
        self.assertEqual([], self.sql("SELECT id FROM rename_rollback_plans WHERE execution_token IS NOT NULL"))
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents")[0][0])

    def test_synchronous_rollback_persists_owner_before_first_file_move(self):
        from app.service.rename_rollback_service import execute_rename_rollback_plan
        from app.utils.file_operations import moveFileNoReplace

        observedOwners = []

        def inspectOwnerAndMove(source, target):
            owner = self.sql(
                "SELECT execution_token,execution_pid,execution_instance_id,execution_started_at "
                "FROM rename_rollback_plans WHERE id=?",
                (self.plan.id,),
            )[0]
            observedOwners.append(owner)
            self.assertTrue(all(value is not None for value in owner))
            moveFileNoReplace(source, target)

        with patch("app.service.rename_rollback_service.moveFileNoReplace", side_effect=inspectOwnerAndMove):
            result = execute_rename_rollback_plan(self.settings, self.plan.id)

        self.assertEqual("executed", result.status)
        self.assertEqual(2, len(observedOwners))
        self.assertEqual((None, None, None, None), self.sql(
            "SELECT execution_token,execution_pid,execution_instance_id,execution_started_at "
            "FROM rename_rollback_plans WHERE id=?",
            (self.plan.id,),
        )[0])

    def expire(self):
        self.sql("UPDATE rename_rollback_plans SET execution_expires_at='2000' WHERE id=?", (self.plan.id,))

    def test_wrong_expired_and_malformed_authorization_never_moves(self):
        from app.service.rollback_queue_service import heartbeatRollback, recoverExpiredRollbacks
        self.accept(); planId, token = claimRollback(self.settings)
        with patch("app.service.rename_rollback_service.moveFileNoReplace", side_effect=AssertionError("no move")):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRollback(self.settings, planId, "wrong")
            self.sql("UPDATE rename_rollback_plans SET execution_expires_at='not-a-date' WHERE id=?", (planId,))
            self.assertFalse(heartbeatRollback(self.settings, planId, token))
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRollback(self.settings, planId, token)
        self.assertEqual(1, recoverExpiredRollbacks(self.settings))
        self.assertEqual("interrupted", self.accept().status)
        self.assertIsNone(claimRollback(self.settings))

    def test_permission_revocation_during_first_move_preserves_known_result(self):
        from app.service.auth_service import create_user
        from app.utils.file_operations import moveFileNoReplace
        user = create_user(self.settings, "rollbacker", "temporary", "SyntheticPassword123!", ["rollback:execute"], True)
        acceptRollback(self.settings, self.plan.id, AuditWriteContext(user))
        planId, token = claimRollback(self.settings)
        def move(source, target):
            moveFileNoReplace(source, target)
            self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (user.id,))
        with patch("app.service.rename_rollback_service.moveFileNoReplace", side_effect=move):
            with self.assertRaisesRegex(ValueError, "权限被撤销"):
                executeAcceptedRollback(self.settings, planId, token)
        result = get_rename_rollback_plan(self.settings, planId)
        self.assertEqual((1, 0), (result.rolled_back_count, result.failed_count))
        self.assertEqual(["rolled_back", "ready"], [item.status for item in result.items])
        finishInterruptedRollback(self.settings, planId, token, "permission revoked", processExited=True)
        self.assertEqual("interrupted", self.accept().status)

    def test_expiration_during_preparation_keeps_intent_and_never_moves(self):
        from app.service.local_file_action_service import prepareLocalFileAction
        self.accept(); planId, token = claimRollback(self.settings)
        def prepare(*args, **kwargs):
            intent = prepareLocalFileAction(*args, **kwargs)
            self.expire()
            return intent
        with patch("app.service.rename_rollback_service.prepareLocalFileAction", side_effect=prepare), patch(
                "app.service.rename_rollback_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRollback(self.settings, planId, token)
        self.assertEqual("prepared", self.sql("SELECT status FROM local_file_actions WHERE action_type='rollback'")[0][0])
        self.assertTrue(Path(self.plan.items[0].current_path).exists())

    def test_expiration_after_first_move_does_not_erase_success(self):
        from app.utils.file_operations import moveFileNoReplace
        self.accept(); planId, token = claimRollback(self.settings)
        def move(source, target):
            moveFileNoReplace(source, target); self.expire()
        with patch("app.service.rename_rollback_service.moveFileNoReplace", side_effect=move):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedRollback(self.settings, planId, token)
        result = get_rename_rollback_plan(self.settings, planId)
        self.assertEqual((1, 0), (result.rolled_back_count, result.failed_count))
        self.assertEqual(["rolled_back", "ready"], [item.status for item in result.items])

    def test_queued_plan_freezes_source_preview_and_duplicate_plan(self):
        from app.service.media_source_service import delete_media_source
        from app.service.preview_service import update_rename_preview
        other = create_rename_rollback_plan(self.settings, self.operation.id)
        dry_run_rename_rollback_plan(self.settings, other.id)
        self.accept()
        with self.assertRaisesRegex(ValueError, "已有"):
            acceptRollback(self.settings, other.id, AuditWriteContext())
        with self.assertRaisesRegex(ValueError, "存在运行中"):
            delete_media_source(self.settings, self.fixture.source.id)
        with self.assertRaises(ValueError):
            update_rename_preview(self.settings, self.fixture.previews[0].id, "edited.mkv")
        with self.assertRaisesRegex(ValueError, "已有文件写任务"):
            from app.service.rename_rollback_service import execute_rename_rollback_plan
            execute_rename_rollback_plan(self.settings, other.id)
        self.assertEqual("checked", get_rename_rollback_plan(self.settings, other.id).status)

    def test_global_fifo_and_legacy_rename_cannot_bypass_rollback(self):
        from app.service.rename_operation_service import create_rename_dry_run, get_rename_operation
        from app.service.rename_queue_service import acceptRename, claimRename
        from app.service.preview_service import generate_rename_previews, list_rename_previews
        from app.service.scan_service import run_full_scan
        (self.root / "media" / "Third.2024.1080p.mkv").write_bytes(b"synthetic")
        job = run_full_scan(self.settings, self.fixture.source.id)
        generate_rename_previews(self.settings, scan_job_id=job.id)
        preview = next(p for p in list_rename_previews(self.settings, scan_job_id=job.id) if "Third" in p.file_name)
        other = create_rename_dry_run(self.settings, [preview.id])
        self.accept()
        with self.assertRaisesRegex(ValueError, "已有文件写任务"):
            execute_rename_operation(self.settings, other.id)
        self.assertEqual("dry_run", get_rename_operation(self.settings, other.id).status)
        acceptRename(self.settings, other.id, AuditWriteContext())
        self.assertIsNone(claimRename(self.settings))
        planId, token = claimRollback(self.settings)
        self.assertIsNone(claimRename(self.settings))
        executeAcceptedRollback(self.settings, planId, token)
        self.assertIsNone(claimRename(self.settings))
        finishInterruptedRollback(self.settings, planId, token, "exited", processExited=True)
        self.assertEqual(other.id, claimRename(self.settings)[0])

    def test_final_audit_failure_keeps_slot_and_can_flush_known_outcome(self):
        from app.service.file_action_audit_service import flushFileActionAuditIntents
        self.accept(); planId, token = claimRollback(self.settings)
        executeAcceptedRollback(self.settings, planId, token)
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                finishInterruptedRollback(self.settings, planId, token, "exited", processExited=True)
        self.assertIsNone(claimRollback(self.settings))
        self.assertEqual(1, flushFileActionAuditIntents(self.settings)["completed"])
        finishInterruptedRollback(self.settings, planId, token, "exited", processExited=True)
        self.assertEqual([], self.sql("SELECT id FROM rename_rollback_plans WHERE execution_token IS NOT NULL"))

    def test_real_hidden_worker_only_rolls_back_temporary_media(self):
        from app.service.rollback_worker_service import startRollbackProcess
        self.accept(); planId, token = claimRollback(self.settings)
        process = startRollbackProcess(self.settings, planId, token)
        try:
            self.assertEqual(0, process.wait(timeout=20))
            self.assertEqual(2, get_rename_rollback_plan(self.settings, planId).rolled_back_count)
            self.assertEqual(process.pid, self.sql("SELECT execution_pid FROM rename_rollback_plans WHERE id=?", (planId,))[0][0])
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            finishInterruptedRollback(self.settings, planId, token, "temporary worker exited", processExited=True)

    def test_api_accepts_once_and_legacy_endpoint_only_reads_queue(self):
        from api_test_support import authenticatedClient
        from app.main import create_app
        client = authenticatedClient(create_app(self.settings))
        with patch("app.service.rename_rollback_service.moveFileNoReplace", side_effect=AssertionError("no request file move")):
            for _ in range(2):
                response = client.post(f"/api/rename-rollback-plans/{self.plan.id}/execute-async", json={})
                self.assertEqual(202, response.status_code)
                self.assertEqual("queued", response.json()["status"])
            self.assertEqual("queued", client.post(f"/api/rename-rollback-plans/{self.plan.id}/execute", json={}).json()["status"])
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM file_action_audit_intents")[0][0])
        self.sql("UPDATE users SET permissions_json='[]'")
        self.assertEqual(403, client.post(f"/api/rename-rollback-plans/{self.plan.id}/execute-async", json={}).status_code)

    def test_lifespan_executes_rollbacks_and_governance_points_to_exact_plan(self):
        from api_test_support import authenticatedClient
        from app.main import create_app
        import time
        with authenticatedClient(create_app(self.settings)) as client:
            queuedTasks = client.get("/api/tasks/page", params={"task_type": "rollback_plan"}).json()["items"]
            self.assertEqual(0, queuedTasks[0]["success_count"])
            self.assertEqual(202, client.post(f"/api/rename-rollback-plans/{self.plan.id}/execute-async", json={}).status_code)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = client.get(f"/api/rename-rollback-plans/{self.plan.id}").json()
                if result["status"] not in {"queued", "running"}: break
                time.sleep(0.05)
            self.assertEqual(("executed", 2), (result["status"], result["rolled_back_count"]))
            tasks = client.get("/api/tasks/page", params={"task_type": "rollback_plan"}).json()["items"]
            self.assertEqual(str(self.plan.id), tasks[0]["target_query"]["rollback_plan_id"])
            self.assertEqual(2, tasks[0]["success_count"])
        self.assertEqual([], self.sql("SELECT id FROM rename_rollback_plans WHERE execution_token IS NOT NULL"))
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents")[0][0])

    def test_readonly_recovery_requires_dead_known_process_and_never_replays(self):
        from app.service.rollback_queue_service import inspectInterruptedRollbacks, releaseInterruptedRollback, recoverExpiredRollbacks
        self.accept(); planId, token = claimRollback(self.settings)
        self.sql("UPDATE rename_rollback_plans SET execution_pid=12345 WHERE id=?", (planId,))
        self.expire(); recoverExpiredRollbacks(self.settings)
        before = self.settings.database_path.read_bytes()
        self.assertEqual(planId, inspectInterruptedRollbacks(self.settings, limit=1)["items"][0]["id"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "确认服务已停止"):
            releaseInterruptedRollback(self.settings, planId, confirmStopped=False)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=True):
            with self.assertRaisesRegex(ValueError, "拒绝释放"):
                releaseInterruptedRollback(self.settings, planId, confirmStopped=True)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False), patch(
                "app.service.rename_rollback_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            releaseInterruptedRollback(self.settings, planId, confirmStopped=True)
        self.assertEqual([], inspectInterruptedRollbacks(self.settings)["items"])
        self.assertTrue(Path(self.plan.items[0].current_path).exists())

    def test_v31_upgrade_preserves_checked_plan_and_backfills_only_real_results(self):
        from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
        columns = ("execution_token", "execution_pid", "execution_expires_at", "actor_id", "actor_name",
                   "audit_context_json", "audit_intent_id", "error_message", "rolled_back_count", "failed_count")
        self.sql("DROP INDEX idx_rollback_plans_queue")
        self.sql("DROP INDEX idx_rollback_execution_slot")
        for column in columns:
            self.sql(f"ALTER TABLE rename_rollback_plans DROP COLUMN {column}")
        self.sql("UPDATE app_meta SET value='31' WHERE key='schema_version'")
        ensure_database(self.settings); ensure_database(self.settings)
        self.assertEqual(self.plan, get_rename_rollback_plan(self.settings, self.plan.id))
        self.assertIsNone(claimRollback(self.settings))
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])

    def test_recovery_cli_is_readonly_and_requires_apply_gates(self):
        from app.recover_rollbacks import main
        from contextlib import redirect_stdout, redirect_stderr
        import io, json
        config = self.root / "queue.toml"
        config.write_text(f"[paths]\ndata_dir='{self.settings.data_dir.as_posix()}'\n"
                          f"database_path='{self.settings.database_path.as_posix()}'\n", encoding="utf-8")
        before = self.settings.database_path.read_bytes()
        output = io.StringIO()
        with patch("sys.argv", ["recover_rollbacks", "--config", str(config)]), redirect_stdout(output):
            self.assertEqual(0, main())
        self.assertEqual([], json.loads(output.getvalue())["items"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with patch("sys.argv", ["recover_rollbacks", "--config", str(config), "--apply", "--plan-id", "1"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised: main()
        self.assertEqual(2, raised.exception.code)

    def test_completed_history_migration_counts_real_items_not_ready_candidates(self):
        from app.core.database import ensure_database
        self.sql("UPDATE rename_rollback_plans SET status='partial_failed' WHERE id=?", (self.plan.id,))
        self.sql("UPDATE rename_rollback_items SET status='rolled_back' WHERE id=?", (self.plan.items[0].id,))
        self.sql("UPDATE rename_rollback_items SET status='failed' WHERE id=?", (self.plan.items[1].id,))
        self.sql("UPDATE rename_rollback_plans SET rolled_back_count=0,failed_count=0")
        self.sql("UPDATE app_meta SET value='31' WHERE key='schema_version'")
        ensure_database(self.settings); ensure_database(self.settings)
        result = get_rename_rollback_plan(self.settings, self.plan.id)
        self.assertEqual((1, 1, "partial_failed"), (result.rolled_back_count, result.failed_count, result.status))
        self.assertIsNone(claimRollback(self.settings))

    def test_missing_database_recovery_never_initializes_default_instance(self):
        from dataclasses import replace
        from app.service.rollback_queue_service import inspectInterruptedRollbacks, releaseInterruptedRollback
        settings = replace(self.settings, database_path=self.root / "missing.db")
        with self.assertRaises(sqlite3.OperationalError): inspectInterruptedRollbacks(settings)
        with self.assertRaises(sqlite3.OperationalError): releaseInterruptedRollback(settings, 1, confirmStopped=True)
        self.assertFalse(settings.database_path.exists())

    def test_remote_release_failure_preserves_committed_rollback(self):
        import test_rename_rollback as remoteSupport
        from app.service.shared_protocols.base import ConnectionTestResult, RemoteFileEntry
        fixture = remoteSupport.RenameRollbackTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        fixture._insert_webdav_renamed_operation()
        plan = create_rename_rollback_plan(fixture.settings, 9)
        info = RemoteFileEntry(path="https://nas.example/dav/Movie.2024.1080p.mkv", name="Movie.2024.1080p.mkv",
            extension=".mkv", file_size=2048, modified_at="2026-01-01T00:00:00+00:00", version='"target-etag"')
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                   return_value=ConnectionTestResult(True, "synthetic", readable=True, writable=True)), patch(
                   "app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=info), patch(
                   "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                   return_value=ConnectionTestResult(True, "synthetic", readable=True, writable=True)), patch(
                   "app.service.rename_rollback_service.release_remote_operation_lock", side_effect=sqlite3.OperationalError("synthetic")):
            dry_run_rename_rollback_plan(fixture.settings, plan.id)
            acceptRollback(fixture.settings, plan.id, AuditWriteContext())
            planId, token = claimRollback(fixture.settings)
            result = executeAcceptedRollback(fixture.settings, planId, token)
        self.assertEqual(("executed", 1, 0), (result.status, result.rolled_back_count, result.failed_count))
        self.assertEqual("rolled_back", result.items[0].status)
        finishInterruptedRollback(fixture.settings, planId, token, "exited", processExited=True)

    def test_remote_business_rollback_does_not_keep_uncommitted_success_count(self):
        import test_rename_rollback as remoteSupport
        from app.service.shared_protocols.base import ConnectionTestResult, RemoteFileEntry
        from app.service.operation_log_service import record_operation_log
        fixture = remoteSupport.RenameRollbackTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups); self.addCleanup(fixture.tearDown)
        fixture._insert_webdav_renamed_operation()
        plan = create_rename_rollback_plan(fixture.settings, 9)
        info = RemoteFileEntry(path="https://nas.example/dav/Movie.2024.1080p.mkv", name="Movie.2024.1080p.mkv",
            extension=".mkv", file_size=2048, modified_at="2026-01-01T00:00:00+00:00", version='"target-etag"')
        failedOnce = False
        def record(*args, **kwargs):
            nonlocal failedOnce
            if kwargs.get("stage") == "rollback_progress" and not failedOnce:
                failedOnce = True
                raise sqlite3.OperationalError("synthetic commit boundary")
            return record_operation_log(*args, **kwargs)
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                   return_value=ConnectionTestResult(True, "synthetic", readable=True, writable=True)), patch(
                   "app.service.shared_protocols.webdav.WebDavProtocol.get_file_info", return_value=info), patch(
                   "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                   return_value=ConnectionTestResult(True, "synthetic", readable=True, writable=True)):
            dry_run_rename_rollback_plan(fixture.settings, plan.id)
            acceptRollback(fixture.settings, plan.id, AuditWriteContext())
            planId, token = claimRollback(fixture.settings)
            with patch("app.service.rename_rollback_service.record_operation_log", side_effect=record):
                result = executeAcceptedRollback(fixture.settings, planId, token)
        self.assertEqual(("failed", 0, 1), (result.status, result.rolled_back_count, result.failed_count))
        self.assertEqual("failed", result.items[0].status)
        with closing(sqlite3.connect(fixture.settings.database_path)) as connection:
            self.assertEqual("recovery_required", connection.execute(
                "SELECT status FROM remote_operation_items WHERE operation_type='rollback'").fetchone()[0])

    def test_supervisor_reaps_before_clearing_rollback_slot(self):
        import asyncio
        from app.service.rollback_worker_service import runRollbackWorkerLoop
        self.accept(); stop = asyncio.Event(); owner = self
        class FakeProcess:
            exited = False
            waited = False
            def poll(self): return -9 if self.exited else None
            def kill(self):
                owner.assertEqual(1, owner.sql("SELECT COUNT(*) FROM rename_rollback_plans WHERE execution_token IS NOT NULL")[0][0])
                self.exited = True
            def wait(self): self.waited = True
        process = FakeProcess()
        def renew(*_): stop.set(); return True
        with patch("app.service.rollback_worker_service.startRollbackProcess", return_value=process), patch(
                "app.service.rollback_worker_service.heartbeatRollback", side_effect=renew):
            asyncio.run(runRollbackWorkerLoop(self.settings, stop))
        self.assertTrue(process.waited)
        self.assertEqual("interrupted", get_rename_rollback_plan(self.settings, self.plan.id).status)
        self.assertEqual([], self.sql("SELECT id FROM rename_rollback_plans WHERE execution_token IS NOT NULL"))
