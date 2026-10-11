"""迁移任务仅用临时媒体和数据库，故障/权限/进程边界回归。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import errno
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
from app.core.logger import shutdown_logging
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import bootstrap_admin
from app.service.file_task_queue_service import inspectInterruptedFileTasks, releaseInterruptedFileTask, assertFileTaskSlotFree
from app.service.pending_move_service import (
    acceptPendingMove, claimPendingMove, executeAcceptedPendingMove, findPendingMove, getPendingMove,
    finishInterruptedPendingMove, recoverExpiredPendingMoves,
)
from app.service.pending_move_worker_service import startPendingMoveProcess
from app.service.pending_file_service import move_pending_files, remove_pending_file, clear_pending_files, parse_pending_file_with_ai
from app.service.task_governance_service import listTaskGovernancePage
from app.service.operation_log_service import list_operation_logs
from app.utils.file_operations import moveFileNoReplace, copyFileNoReplace
import test_pending_files as support


class PendingMoveQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(shutdown_logging)
        self.root = Path(self.temp.name)
        self.settings, self.media, self.target, self.pending = support.PendingFileTest()._moveFixture(self.root)
        self.actor = bootstrap_admin(self.settings, "migrator", "测试用户", "SyntheticPassword123!")
        self.context = AuditWriteContext(self.actor)

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(statement, args).fetchall()
            connection.commit()
            return rows

    def accept(self, key="synthetic-request-1", ids=None):
        return acceptPendingMove(self.settings, ids or [item.id for item in self.pending], str(self.target), key, self.context)

    def finish(self, taskId, token):
        finishInterruptedPendingMove(self.settings, taskId, token, "临时执行已退出，不重试", processExited=True)

    def test_accept_has_no_filesystem_io_duplicate_has_one_intent_and_payload_is_frozen(self):
        with patch("pathlib.Path.lstat", side_effect=AssertionError("no probe")), patch("pathlib.Path.resolve", side_effect=AssertionError("no resolve")):
            first, second = self.accept(), self.accept()
        self.assertEqual(first, second)
        self.assertEqual("queued", first.status)
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM file_action_audit_intents WHERE task_type='pending_move'")[0][0])
        self.assertEqual([("moving",), ("moving",)], self.sql("SELECT status FROM pending_files ORDER BY id"))
        self.assertEqual(first, findPendingMove(self.settings, "synthetic-request-1", self.actor.id))
        self.assertIsNone(findPendingMove(self.settings, "synthetic-request-1", self.actor.id + 1))
        with self.assertRaisesRegex(ValueError, "其他内容"):
            self.accept(ids=[self.pending[0].id])

    def test_audit_admission_failure_rolls_back_task_and_reservations(self):
        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError): self.accept()
        self.assertEqual([], self.sql("SELECT id FROM pending_move_tasks"))
        self.assertEqual([("pending",), ("pending",)], self.sql("SELECT status FROM pending_files"))

    def test_concurrent_accept_and_claim_are_single_use(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = list(pool.map(lambda _: self.accept(), range(2)))
        self.assertEqual(tasks[0], tasks[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claimPendingMove(self.settings), range(2)))
        self.assertEqual(1, sum(claim is not None for claim in claims))

    def test_completed_counts_and_slot_retained_until_actual_execution_exit(self):
        self.accept(); taskId, token = claimPendingMove(self.settings)
        result = executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertEqual(("completed", 2, 0), (result.status, result.moved_count, result.failed_count))
        self.assertIsNone(claimPendingMove(self.settings))
        self.finish(taskId, token)
        self.assertEqual([], self.sql("SELECT id FROM pending_move_tasks WHERE execution_token IS NOT NULL"))
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents WHERE task_type='pending_move'")[0][0])

    def test_fifo_move_and_rename_both_directions_and_retained_token_block_other_types(self):
        from app.service.file_task_queue_service import claimFileTask
        task = self.accept()
        self.sql("UPDATE pending_move_tasks SET updated_at='2000' WHERE id=?", (task.id,))
        self.sql("INSERT INTO rename_operations(status,mode,created_at,updated_at) VALUES ('queued','safe_rename','2001','2001')")
        self.assertIsNone(claimFileTask(self.settings, "rename_operation"))
        taskId, token = claimPendingMove(self.settings)
        executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertIsNone(claimFileTask(self.settings, "rename_operation"))
        self.finish(taskId, token)
        self.assertIsNotNone(claimFileTask(self.settings, "rename_operation"))
        self.sql("UPDATE rename_operations SET execution_token=NULL,status='queued',updated_at='2000'")
        self.sql("UPDATE pending_move_tasks SET status='queued',updated_at='2001'")
        self.assertIsNone(claimPendingMove(self.settings))
        self.assertIsNotNone(claimFileTask(self.settings, "rename_operation"))

    def test_final_audit_failure_does_not_overwrite_legacy_known_success(self):
        from types import SimpleNamespace
        response = SimpleNamespace(headers={})
        with patch("app.service.file_action_audit_service._writeFinalAudit", side_effect=sqlite3.OperationalError("synthetic")), \
                patch("app.core.logger.get_logger", side_effect=OSError("synthetic logger failure")):
            result = move_pending_files(self.settings, [self.pending[0].id], str(self.target), context=self.context, response=response)
        self.assertEqual("moved", result[0].status)
        self.assertEqual("pending", response.headers["X-MediaAI-Audit-State"])
        intent = self.sql("SELECT status,outcome_json FROM file_action_audit_intents WHERE task_type='pending_move'")[0]
        self.assertEqual("pending", intent[0])
        self.assertEqual(1, json.loads(intent[1])["detail"]["succeeded_count"])
        self.assertIsNotNone(self.sql("SELECT execution_token FROM pending_move_tasks")[0][0])
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            taskId, token = self.sql("SELECT id,execution_token FROM pending_move_tasks")[0]
            self.finish(taskId, token)
        self.assertEqual("completed", self.sql("SELECT status FROM file_action_audit_intents WHERE task_type='pending_move'")[0][0])
        self.assertEqual(1, getPendingMove(self.settings, taskId).moved_count)

    def test_legacy_sync_and_source_lifecycle_cannot_bypass_queued_task(self):
        from app.service.media_source_service import delete_media_source
        self.accept()
        with self.assertRaisesRegex(ValueError, "已有文件写任务"):
            move_pending_files(self.settings, [self.pending[0].id], str(self.target))
        with self.assertRaises(ValueError): delete_media_source(self.settings, self.pending[0].media_source_id)
        with self.assertRaises(ValueError): remove_pending_file(self.settings, self.pending[0].id)
        with self.assertRaises(ValueError): parse_pending_file_with_ai(self.settings, self.pending[0].id)
        self.assertEqual(0, clear_pending_files(self.settings))

    def test_permission_revocation_after_first_move_preserves_known_result_and_stops_next(self):
        self.accept(); taskId, token = claimPendingMove(self.settings)
        def moved(source, target):
            moveFileNoReplace(source, target)
            self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.actor.id,))
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=moved):
            with self.assertRaisesRegex(ValueError, "权限被撤销"):
                executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertEqual(1, getPendingMove(self.settings, taskId).moved_count)
        self.finish(taskId, token)
        self.assertTrue((self.media / "b.mkv").exists())
        self.assertEqual([("moved",), ("pending",)], self.sql("SELECT status FROM pending_files ORDER BY id"))

    def test_permission_revocation_after_copy_never_unlinks_source(self):
        self.accept(); taskId, token = claimPendingMove(self.settings)
        def copied(source, target, identity):
            result = copyFileNoReplace(source, target, identity)
            self.sql("UPDATE users SET enabled=0 WHERE id=?", (self.actor.id,))
            return result
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=OSError(errno.EXDEV, "synthetic")), \
                patch("app.service.pending_file_service.copyFileNoReplace", side_effect=copied):
            with self.assertRaises(ValueError): executeAcceptedPendingMove(self.settings, taskId, token)
        self.finish(taskId, token)
        self.assertTrue((self.media / "a.mkv").exists())
        self.assertTrue((self.target / "a.mkv").exists())
        self.assertEqual("recovery_required", getPendingMove(self.settings, taskId).items[0].status)
        self.assertEqual(0, getPendingMove(self.settings, taskId).moved_count)

    def test_conflict_stops_later_items_and_never_overwrites(self):
        (self.target / "b.mkv").write_text("existing", encoding="utf-8")
        self.accept(); taskId, token = claimPendingMove(self.settings)
        result = executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertEqual(("partial_failed", 1, 1), (result.status, result.moved_count, result.failed_count))
        self.finish(taskId, token)
        self.assertEqual("existing", (self.target / "b.mkv").read_text())

    def test_directory_replacement_after_identity_capture_never_moves_source(self):
        from app.service.pending_move_service import directoryIdentity
        self.accept(); taskId, token = claimPendingMove(self.settings)
        captured = False
        def replaced(path):
            nonlocal captured
            identity = directoryIdentity(path)
            if not captured:
                captured = True
                self.target.rename(self.root / "original-target")
                self.target.mkdir()
            return identity
        with patch("app.service.pending_move_service.directoryIdentity", side_effect=replaced), \
                patch("app.service.pending_file_service.moveFileNoReplace", side_effect=AssertionError("no move")):
            with self.assertRaisesRegex(ValueError, "目录已替换"):
                executeAcceptedPendingMove(self.settings, taskId, token)
        self.finish(taskId, token)
        self.assertTrue((self.media / "a.mkv").exists())
        self.assertEqual(0, getPendingMove(self.settings, taskId).moved_count)

    def test_wrong_token_and_invalid_acceptance_never_prepare_file_actions(self):
        for ids, target in (([], str(self.target)), ([True], str(self.target)), ([-1], str(self.target)),
                            ([self.pending[0].id], "relative-target")):
            with self.assertRaises(ValueError):
                acceptPendingMove(self.settings, ids, target, "invalid-request", self.context)
        task = self.accept(); taskId, token = claimPendingMove(self.settings)
        with self.assertRaises(ValueError): executeAcceptedPendingMove(self.settings, taskId, "wrong-token")
        self.assertEqual([], self.sql("SELECT id FROM local_file_actions"))
        self.assertEqual(0, getPendingMove(self.settings, task.id).moved_count)
        self.finish(taskId, token)

    def test_business_commit_failure_keeps_zero_counts_and_unknown_file_intent(self):
        from app.service.local_file_action_service import updateLocalFileAction
        self.accept(); taskId, token = claimPendingMove(self.settings)
        def completed(*args, **kwargs):
            if args[2] == "completed": raise sqlite3.OperationalError("synthetic")
            return updateLocalFileAction(*args, **kwargs)
        with patch("app.service.pending_file_service.updateLocalFileAction", side_effect=completed):
            result = executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertEqual(("interrupted", 0), (result.status, result.moved_count))
        self.assertFalse((self.media / "a.mkv").exists())
        from app.service.local_file_recovery_service import recoverLocalFileActions
        self.assertEqual(1, recoverLocalFileActions(self.settings)["wouldRepair"])
        self.assertEqual(1, recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)["failed"])
        self.assertEqual(0, getPendingMove(self.settings, taskId).moved_count)
        self.finish(taskId, token)
        self.assertEqual("recovery_required", getPendingMove(self.settings, taskId).items[0].status)
        originalOutcome = self.sql("SELECT outcome_json FROM file_action_audit_intents WHERE task_type='pending_move'")[0][0]
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            report = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report["repaired"])
        recovered = getPendingMove(self.settings, taskId)
        self.assertEqual(("interrupted", 1), (recovered.status, recovered.moved_count))
        self.assertEqual("moved", recovered.items[0].status)
        self.assertEqual(originalOutcome, self.sql("SELECT outcome_json FROM file_action_audit_intents WHERE task_type='pending_move'")[0][0])

    def test_local_recovery_of_known_unapplied_action_releases_item_without_replay(self):
        from app.service.local_file_action_service import prepareLocalFileAction
        from app.service.local_file_recovery_service import recoverLocalFileActions
        from app.service.file_task_queue_service import FileTaskLeaseError
        self.accept(); taskId, token = claimPendingMove(self.settings)
        def interrupted(*args, **kwargs):
            prepareLocalFileAction(*args, **kwargs)
            raise FileTaskLeaseError("模拟意图提交后执行中断")
        with patch("app.service.pending_file_service.prepareLocalFileAction", side_effect=interrupted):
            with self.assertRaises(ValueError): executeAcceptedPendingMove(self.settings, taskId, token)
        self.finish(taskId, token)
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=AssertionError("no replay")):
            report = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report["notApplied"])
        self.assertEqual([("pending",), ("pending",)], self.sql("SELECT status FROM pending_files ORDER BY id"))
        result = getPendingMove(self.settings, taskId)
        self.assertEqual(("interrupted", 0, "not_started"), (result.status, result.moved_count, result.items[0].status))

    def test_expired_lease_never_replays_and_readonly_release_requires_dead_pid(self):
        self.accept(); taskId, token = claimPendingMove(self.settings)
        self.sql("UPDATE pending_move_tasks SET execution_expires_at='2000',execution_pid=12345 WHERE id=?", (taskId,))
        self.assertEqual(1, recoverExpiredPendingMoves(self.settings))
        with self.assertRaises(ValueError): executeAcceptedPendingMove(self.settings, taskId, token)
        self.assertIsNone(claimPendingMove(self.settings))
        before = self.settings.database_path.read_bytes()
        self.assertEqual(taskId, inspectInterruptedFileTasks(self.settings, "pending_move")["items"][0]["id"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=True):
            with self.assertRaises(ValueError): releaseInterruptedFileTask(self.settings, "pending_move", taskId, confirmStopped=True)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False):
            releaseInterruptedFileTask(self.settings, "pending_move", taskId, confirmStopped=True)
        self.assertEqual("interrupted", getPendingMove(self.settings, taskId).status)
        self.assertTrue((self.media / "a.mkv").exists())
        self.assertEqual([("pending",), ("pending",)], self.sql("SELECT status FROM pending_files"))

    def test_real_hidden_worker_moves_only_temporary_files(self):
        self.accept(); taskId, token = claimPendingMove(self.settings)
        process = startPendingMoveProcess(self.settings, taskId, token)
        try:
            self.assertEqual(0, process.wait(timeout=15))
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
        self.assertEqual(2, getPendingMove(self.settings, taskId).moved_count)
        self.assertIsNotNone(self.sql("SELECT execution_token FROM pending_move_tasks")[0][0])
        self.finish(taskId, token)

    def test_governance_and_logs_point_to_exact_task(self):
        task = self.accept()
        page = listTaskGovernancePage(self.settings, task_type="pending_move")
        self.assertEqual(1, page["total"])
        self.assertEqual(str(task.id), page["items"][0].target_query["pending_move_task_id"])
        self.assertEqual(str(self.pending[0].scan_job_id), page["items"][0].target_query["scan_job_id"])
        logs = list_operation_logs(self.settings, task_type="pending_move", task_id=task.id)
        self.assertTrue(logs.running)
        self.assertTrue(logs.items)

    def test_mixed_sources_are_filterable_without_loading_all_historical_move_items(self):
        from app.service.media_source_service import create_media_source
        from app.service.scan_service import run_full_scan
        from app.service.pending_file_service import list_pending_files
        from app.service.task_governance_service import _TASK_QUERY
        other = self.root / "other-media"
        other.mkdir(); (other / "c.mkv").write_text("x", encoding="utf-8")
        source = create_media_source(self.settings, "第二来源", other, True)
        job = run_full_scan(self.settings, source.id)
        pending = list_pending_files(self.settings, scan_job_id=job.id)[0]
        task = self.accept(ids=[self.pending[0].id, pending.id])
        page = listTaskGovernancePage(self.settings, task_type="pending_move", media_source_id=source.id)
        self.assertEqual(1, page["total"])
        item = page["items"][0]
        self.assertEqual(task.id, item.task_id)
        self.assertIsNone(item.media_source_id)
        self.assertNotIn("media_source_id", item.target_query)
        self.assertNotIn("scan_job_id", item.target_query)
        plan = self.sql("EXPLAIN QUERY PLAN " + _TASK_QUERY + "SELECT task_type,task_id FROM tasks ORDER BY updated_at DESC LIMIT 50")
        self.assertNotIn("pending_move_items", " ".join(str(row[-1]) for row in plan))

    def test_v33_upgrade_is_empty_and_preserves_existing_pending_records(self):
        self.sql("DROP TABLE pending_move_items"); self.sql("DROP TABLE pending_move_tasks")
        self.sql("UPDATE app_meta SET value='33' WHERE key='schema_version'")
        ensure_database(self.settings); ensure_database(self.settings)
        self.assertEqual(2, self.sql("SELECT COUNT(*) FROM pending_files")[0][0])
        self.assertEqual([], self.sql("SELECT id FROM pending_move_tasks"))
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])

    def test_api_accept_repeat_and_lookup_never_execute_files_in_request(self):
        from app.main import create_app
        from fastapi.testclient import TestClient
        from app.service.auth_service import login
        client = TestClient(create_app(self.settings))
        client.headers["Authorization"] = f"Bearer {login(self.settings, self.actor.username, 'SyntheticPassword123!').access_token}"
        payload = {"ids": [item.id for item in self.pending], "target_directory": str(self.target), "request_id": "api-test-request-1"}
        with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=AssertionError("no move")):
            first = client.post("/api/pending-files/move-async", json=payload)
            self.assertEqual(202, first.status_code)
            self.assertEqual(first.json(), client.post("/api/pending-files/move-async", json=payload).json())
        self.assertEqual(first.json(), client.get(f"/api/pending-files/moves/{first.json()['id']}").json())
        self.assertEqual(first.json(), client.get("/api/pending-files/moves/by-request/api-test-request-1").json())

    def test_supervisor_timeout_reaps_before_releasing_pending_reservations(self):
        import asyncio
        from app.service.pending_move_worker_service import runPendingMoveWorkerLoop
        task = self.accept()
        stop = asyncio.Event()
        class Process:
            exited = False
            def poll(self): return -9 if self.exited else None
            def kill(self): self.exited = True
            def wait(self): stop.set(); return -9
        process = Process()
        with patch("app.service.pending_move_worker_service.startPendingMoveProcess", return_value=process), \
                patch("app.service.pending_move_worker_service.FILE_TASK_TIMEOUT_SECONDS", 0):
            asyncio.run(runPendingMoveWorkerLoop(self.settings, stop))
        self.assertTrue(process.exited)
        self.assertEqual("interrupted", getPendingMove(self.settings, task.id).status)
        self.assertEqual([("pending",), ("pending",)], self.sql("SELECT status FROM pending_files"))
        self.assertEqual([], self.sql("SELECT id FROM pending_move_tasks WHERE execution_token IS NOT NULL"))

    def test_cli_is_readonly_and_requires_explicit_apply_gates(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from app.recover_pending_moves import main
        config = self.root / "config.toml"
        config.write_text("", encoding="utf-8")
        before = self.settings.database_path.read_bytes()
        with patch("sys.argv", ["recover_pending_moves", "--config", str(config)]), \
                patch("app.recover_pending_moves.load_settings", return_value=self.settings), redirect_stdout(io.StringIO()):
            self.assertEqual(0, main())
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with patch("sys.argv", ["recover_pending_moves", "--config", str(config), "--apply", "--task-id", "1"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): main()
