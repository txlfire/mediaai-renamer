"""隔离数据库中的队列、租约、策略和真实子进程回归。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.media_source_service import create_media_source
from app.service.scan_queue_service import (
    acceptScan, claimScan, executeAcceptedScan, finishInterruptedScan, getScan, heartbeatScan, recoverExpiredScans,
)
from app.service.scan_service import list_media_files, list_scan_jobs, recover_interrupted_scan_jobs
from app.service.scan_worker_service import startScanProcess
from app.service.settings_service import update_setting_values


class ScanQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        media = self.root / "media"
        media.mkdir()
        (media / "movie.mkv").write_bytes(b"video")
        self.settings = AppSettings(data_dir=self.root / "data", database_path=self.root / "data" / "test.db",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            scan=ScanSettings(batch_size=1, batch_interval_seconds=0))
        ensure_database(self.settings)
        self.source = create_media_source(self.settings, "queue", media)
        update_setting_values(self.settings, {"scan.minimum_file_size": 0, "scan.batch_interval_seconds": 0, "scan.batch_size": 1})

    def accept(self):
        return acceptScan(self.settings, self.source.id, "full", AuditWriteContext())

    def expire(self, jobId):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE scan_jobs SET execution_expires_at='2000' WHERE id=?", (jobId,))
            connection.commit()

    def test_accept_does_not_probe_the_media_directory_and_rejects_duplicate(self):
        with patch("app.service.scan_service.get_protocol", side_effect=AssertionError("must not probe")):
            job = self.accept()
        self.assertEqual("queued", job.status)
        self.assertIsNone(job.started_at)
        with self.assertRaisesRegex(ValueError, "已有"):
            self.accept()

    def test_startup_warning_describes_failed_legacy_task_without_claiming_execution_recovery(self):
        from app.main import create_app
        from app.core.logger import shutdown_logging
        self.addCleanup(shutdown_logging)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) "
                               "VALUES (?,'running',1,0,'synthetic')", (self.source.id,))
            connection.commit()
        with patch("app.main.logger.warning") as warning:
            create_app(self.settings)
        recovery = [call.args for call in warning.call_args_list if "遗留扫描任务" in call.args[0]]
        self.assertEqual(1, len(recovery))
        self.assertEqual(1, recovery[0][1])
        self.assertIn("标记失败", recovery[0][0])
        self.assertIn("未重新执行", recovery[0][0])
        self.assertEqual("failed", list_scan_jobs(self.settings)[0].status)

    def test_accept_audit_failure_rolls_back_job(self):
        with patch("app.service.scan_queue_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                self.accept()
        self.assertEqual([], list_scan_jobs(self.settings))

    def test_two_claimers_only_one_receives_the_job(self):
        self.accept()
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: claimScan(self.settings), range(2)))
        self.assertEqual(1, sum(claim is not None for claim in claims))

    def test_completed_worker_reserves_slot_until_exit_is_confirmed(self):
        self.accept()
        jobId, token = claimScan(self.settings)
        executeAcceptedScan(self.settings, jobId, token)
        otherPath = self.root / "other-media"
        otherPath.mkdir()
        other = create_media_source(self.settings, "other queue", otherPath)
        queued = acceptScan(self.settings, other.id, "full", AuditWriteContext())
        self.assertIsNone(claimScan(self.settings))
        finishInterruptedScan(self.settings, jobId, token, "worker exited", processExited=True)
        nextClaim = claimScan(self.settings)
        self.assertEqual(queued.id, nextClaim[0])
        self.assertEqual("completed", getScan(self.settings, jobId).status)

    def test_terminal_slot_recovery_preserves_business_result(self):
        from app.service.scan_recovery_service import inspectInterruptedScans, releaseInterruptedScan
        job = self.accept()
        jobId, token = claimScan(self.settings)
        executeAcceptedScan(self.settings, jobId, token)
        for status in ("completed", "partial_completed", "failed"):
            with self.subTest(status=status):
                with closing(sqlite3.connect(self.settings.database_path)) as connection:
                    connection.execute("UPDATE scan_jobs SET status=?,execution_token=?,execution_pid=12345,"
                                       "error_message='synthetic result' WHERE id=?", (status, token, jobId))
                    connection.commit()
                before = getScan(self.settings, jobId)
                report = inspectInterruptedScans(self.settings)
                self.assertEqual(1, len(report["items"]))
                self.assertEqual(job.id, report["items"][0]["id"])
                with patch("app.service.scan_recovery_service.isProcessAlive", return_value=True):
                    with self.assertRaisesRegex(ValueError, "拒绝释放"):
                        releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
                with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False):
                    releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
                self.assertEqual(before, getScan(self.settings, jobId))
                self.assertEqual([], inspectInterruptedScans(self.settings)["items"])

    def test_running_slot_cannot_be_released_by_local_recovery(self):
        from app.service.scan_recovery_service import releaseInterruptedScan
        self.accept()
        jobId, _ = claimScan(self.settings)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False):
            with self.assertRaises(ValueError):
                releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
        self.assertEqual("running", getScan(self.settings, jobId).status)

    def test_execution_uses_accepted_policy_and_same_task_id(self):
        job = self.accept()
        update_setting_values(self.settings, {"scan.minimum_file_size": 999})
        jobId, token = claimScan(self.settings)
        result = executeAcceptedScan(self.settings, jobId, token)
        self.assertEqual(job.id, result.id)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.video_count)
        self.assertEqual(1, len(list_scan_jobs(self.settings)))

    def test_expired_lease_cannot_heartbeat_execute_or_be_replayed(self):
        self.accept()
        jobId, token = claimScan(self.settings)
        self.expire(jobId)
        self.assertFalse(heartbeatScan(self.settings, jobId, token))
        with self.assertRaisesRegex(ValueError, "授权已失效"):
            executeAcceptedScan(self.settings, jobId, token)
        self.assertEqual(1, recoverExpiredScans(self.settings))
        self.assertEqual("interrupted", getScan(self.settings, jobId).status)
        self.assertIsNone(claimScan(self.settings))
        self.assertEqual([], list_media_files(self.settings))

    def test_lease_expiry_during_scan_prevents_publication(self):
        update_setting_values(self.settings, {"scan.batch_interval_seconds": 1})
        self.accept()
        jobId, token = claimScan(self.settings)
        with patch("app.service.scan_service.time.sleep", side_effect=lambda _: self.expire(jobId)):
            with self.assertRaisesRegex(ValueError, "授权已失效"):
                executeAcceptedScan(self.settings, jobId, token)
        self.assertEqual([], list_media_files(self.settings))
        self.assertEqual(1, recoverExpiredScans(self.settings))

    def test_scan_failure_after_lease_loss_preserves_original_error_without_publishing(self):
        self.accept()
        jobId, token = claimScan(self.settings)

        def failAfterLeaseLoss(*_args, **_kwargs):
            self.expire(jobId)
            raise RuntimeError("synthetic scan failure")

        with patch("app.service.scan_service._prepareScanStage", side_effect=failAfterLeaseLoss):
            with self.assertRaisesRegex(RuntimeError, "synthetic scan failure"):
                executeAcceptedScan(self.settings, jobId, token)

        job = getScan(self.settings, jobId)
        self.assertEqual("running", job.status)
        self.assertEqual("扫描任务失败：synthetic scan failure", job.error_message)
        self.assertIsNotNone(job.ended_at)
        self.assertEqual([], list_media_files(self.settings))

        self.assertEqual(1, recoverExpiredScans(self.settings))
        interrupted = getScan(self.settings, jobId)
        self.assertEqual("interrupted", interrupted.status)
        self.assertIn("扫描任务失败：synthetic scan failure", interrupted.error_message)
        self.assertIn("扫描执行授权过期", interrupted.error_message)

    def test_readonly_recovery_lists_running_job_with_unknown_pid_without_releasing_it(self):
        from app.service.scan_recovery_service import inspectInterruptedScans, releaseInterruptedScan
        self.accept()
        jobId, _ = claimScan(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE scan_jobs SET execution_pid=NULL WHERE id=?", (jobId,))
            connection.commit()

        before = self.settings.database_path.read_bytes()
        report = inspectInterruptedScans(self.settings)

        self.assertEqual(before, self.settings.database_path.read_bytes())
        self.assertEqual(1, len(report["items"]))
        self.assertEqual(jobId, report["items"][0]["id"])
        self.assertEqual("ownership_unknown", report["items"][0]["inspection_reason"])
        with self.assertRaisesRegex(ValueError, "终态执行槽"):
            releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
        self.assertEqual("running", getScan(self.settings, jobId).status)

    def test_startup_recovery_does_not_touch_a_live_async_job(self):
        self.accept()
        jobId, token = claimScan(self.settings)
        self.assertEqual(0, recover_interrupted_scan_jobs(self.settings))
        self.assertEqual("running", getScan(self.settings, jobId).status)
        self.assertTrue(heartbeatScan(self.settings, jobId, token))

    def test_wrong_token_cannot_release_reserved_slot(self):
        self.accept()
        jobId, token = claimScan(self.settings)
        finishInterruptedScan(self.settings, jobId, "wrong", "test", processExited=True)
        self.assertEqual("running", getScan(self.settings, jobId).status)
        finishInterruptedScan(self.settings, jobId, token, "test", processExited=False)
        self.assertIsNone(claimScan(self.settings))

    def test_real_hidden_worker_process_completes_the_accepted_job(self):
        job = self.accept()
        jobId, token = claimScan(self.settings)
        process = startScanProcess(self.settings, jobId, token)
        try:
            self.assertEqual(0, process.wait(timeout=20))
            self.assertEqual("completed", getScan(self.settings, job.id).status)
            self.assertEqual(1, len(list_media_files(self.settings)))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            finishInterruptedScan(self.settings, jobId, token, "test worker exited", processExited=True)

    def test_actor_permission_revocation_prevents_execution(self):
        from app.service.auth_service import create_user
        user = create_user(self.settings, "scanner", "test", "SyntheticPassword123!", ["scan:run"], True)
        acceptScan(self.settings, self.source.id, "full", AuditWriteContext(user))
        jobId, token = claimScan(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE users SET permissions_json='[]' WHERE id=?", (user.id,))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "权限被撤销"):
            executeAcceptedScan(self.settings, jobId, token)
        self.assertEqual([], list_media_files(self.settings))

    def test_supervisor_stop_kills_and_reaps_before_releasing_slot(self):
        import asyncio
        from app.service.scan_worker_service import runScanWorkerLoop
        self.accept()
        stop = asyncio.Event()

        class FakeProcess:
            exited = False
            waited = False
            def poll(self):
                return -9 if self.exited else None
            def kill(self):
                self.exited = True
            def wait(self):
                self.waited = True
                return -9

        process = FakeProcess()
        def renew(*args):
            stop.set()
            return True

        with patch("app.service.scan_worker_service.startScanProcess", return_value=process), patch(
                "app.service.scan_worker_service.heartbeatScan", side_effect=renew):
            asyncio.run(runScanWorkerLoop(self.settings, stop))
        self.assertTrue(process.exited and process.waited)
        self.assertEqual("interrupted", list_scan_jobs(self.settings)[0].status)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertIsNone(connection.execute("SELECT execution_token FROM scan_jobs").fetchone()[0])

    def test_supervisor_total_deadline_interrupts_without_replaying(self):
        import asyncio
        from app.service.scan_worker_service import runScanWorkerLoop
        self.accept()
        stop = asyncio.Event()

        class FakeProcess:
            exited = False
            def poll(self):
                return -9 if self.exited else None
            def kill(self):
                self.exited = True
            def wait(self):
                stop.set()
                return -9

        with patch("app.service.scan_worker_service.startScanProcess", return_value=FakeProcess()), patch(
                "app.service.scan_worker_service.scanTimeoutSeconds", return_value=0):
            asyncio.run(runScanWorkerLoop(self.settings, stop))
        self.assertEqual(1, len(list_scan_jobs(self.settings)))
        result = list_scan_jobs(self.settings)[0]
        self.assertEqual("interrupted", result.status)
        self.assertIn("超过任务总时间", result.error_message)
        self.assertEqual([], list_media_files(self.settings))

    def test_readonly_recovery_and_dead_process_release_do_not_replay_scan(self):
        from app.service.scan_recovery_service import inspectInterruptedScans, releaseInterruptedScan
        self.accept()
        jobId, token = claimScan(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE scan_jobs SET execution_pid=12345 WHERE id=?", (jobId,))
            connection.commit()
        self.expire(jobId)
        recoverExpiredScans(self.settings)
        before = self.settings.database_path.read_bytes()
        report = inspectInterruptedScans(self.settings)
        self.assertEqual(jobId, report["items"][0]["id"])
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "确认服务已停止"):
            releaseInterruptedScan(self.settings, jobId, confirmStopped=False)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=True):
            with self.assertRaisesRegex(ValueError, "拒绝释放"):
                releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
        with patch("app.service.scan_recovery_service.isProcessAlive", return_value=False):
            releaseInterruptedScan(self.settings, jobId, confirmStopped=True)
        self.assertEqual([], inspectInterruptedScans(self.settings)["items"])
        self.assertEqual("interrupted", getScan(self.settings, jobId).status)
        self.assertEqual([], list_media_files(self.settings))

    def test_process_liveness_checks_own_process_without_terminating_it(self):
        import os
        from app.service.scan_recovery_service import isProcessAlive
        self.assertTrue(isProcessAlive(os.getpid()))

    def test_process_start_handoff_failure_keeps_child_owned_until_reaped(self):
        import asyncio
        from app.service.scan_worker_service import ScanProcessStartError, runScanWorkerLoop
        self.accept()
        stop = asyncio.Event()

        class FakeProcess:
            exited = False
            def poll(self):
                return -9 if self.exited else None
            def kill(self):
                self.exited = True
            def wait(self):
                stop.set()
                return -9

        process = FakeProcess()
        with patch("app.service.scan_worker_service.startScanProcess", side_effect=ScanProcessStartError(process)):
            asyncio.run(runScanWorkerLoop(self.settings, stop))
        self.assertTrue(process.exited)
        self.assertEqual("interrupted", list_scan_jobs(self.settings)[0].status)

    def test_broken_start_pipe_preserves_process_handle_even_if_close_fails(self):
        from app.service.scan_worker_service import ScanProcessStartError
        self.accept()
        jobId, token = claimScan(self.settings)

        class BrokenPipe:
            closed = False
            def write(self, data):
                raise BrokenPipeError("synthetic")
            def close(self):
                raise BrokenPipeError("synthetic close")

        class FakeProcess:
            pid = 12345
            stdin = BrokenPipe()

        process = FakeProcess()
        with patch("app.service.scan_worker_service.subprocess.Popen", return_value=process):
            with self.assertRaises(ScanProcessStartError) as raised:
                startScanProcess(self.settings, jobId, token)
        self.assertIs(process, raised.exception.process)

    def test_recovery_cli_defaults_to_readonly_and_requires_explicit_apply_gate(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from app.recover_scans import main
        config = self.root / "config.toml"
        config.write_text("", encoding="utf-8")
        before = self.settings.database_path.read_bytes()
        with patch("sys.argv", ["recover_scans", "--config", str(config)]), patch(
                "app.recover_scans.load_settings", return_value=self.settings), redirect_stdout(io.StringIO()):
            self.assertEqual(0, main())
        self.assertEqual(before, self.settings.database_path.read_bytes())
        with patch("sys.argv", ["recover_scans", "--config", str(config), "--apply", "--job-id", "1"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main()
            self.assertNotEqual(0, raised.exception.code)
