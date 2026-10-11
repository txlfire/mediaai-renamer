"""审查证据复现：只使用临时文件、临时数据库和模拟远程协议。

这些断言确认审查时的缺陷行为，不是修复后的回归验收标准。
"""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import itertools
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

projectRoot = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(projectRoot / "backend"))

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.service.auth_service import bootstrap_admin, change_password, get_user_by_token, login
from app.service.log_service import cleanup_logs
from app.service.media_source_service import create_media_source, delete_media_source
from app.service.external_submission_guard import check_external_submission, update_external_submission_block_decision
from app.service.operation_log_service import list_operation_logs, record_operation_log
from app.service.pending_file_service import list_pending_files, move_pending_files
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation, get_rename_operation
from app.service.remote_operation_service import acquire_remote_operation_lock, release_remote_operation_lock
from app.service.scan_service import run_full_scan
from app.service.settings_service import update_setting_values
from app.service.shared_protocols.base import ConnectionTestResult
from app.service.shared_protocols.webdav import _url_from_href


class ReviewEvidence(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tempDir.name)
        self.mediaDir = self.root / "media"
        self.mediaDir.mkdir()
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "review.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            scan=ScanSettings(batch_size=1, batch_interval_seconds=0),
        )
        ensure_database(self.settings)
        update_setting_values(self.settings, {
            "scan.minimum_file_size": 0, "scan.batch_size": 1, "scan.batch_interval_seconds": 0,
        })
        self.source = create_media_source(self.settings, "review", self.mediaDir, True)

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def seedFiles(self, count=1, threshold=0):
        for index in range(count):
            (self.mediaDir / f"Review{index}.2024.mkv").write_bytes(b"review")
        update_setting_values(self.settings, {"scan.minimum_file_size": threshold})
        return run_full_scan(self.settings, self.source.id)

    def test_R01_anonymous_file_move_after_bootstrap(self):
        from fastapi.testclient import TestClient
        from app.main import create_app

        self.seedFiles(threshold=100)
        client = TestClient(create_app(self.settings))
        bootstrap_admin(self.settings, "reviewadmin", "review", "ReviewOnly123!")
        destination = self.root / "destination"
        destination.mkdir()
        item = list_pending_files(self.settings)[0]
        with client:
            response = client.post("/api/pending-files/move", json={
                "ids": [item.id], "target_directory": str(destination),
            })
        self.assertEqual(200, response.status_code)
        self.assertTrue((destination / item.file_name).exists())
        print("R01: anonymous pending move returned 200 and moved temporary file")

    def test_R02_log_cursor_skips_unreturned_rows(self):
        job = self.seedFiles()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("DELETE FROM operation_logs")
            for index in range(150):
                record_operation_log(self.settings, task_type="scan_job", task_id=job.id,
                                     level="info", stage="proof", message=str(index), connection=connection)
            connection.commit()
        first = list_operation_logs(self.settings, task_type="scan_job", task_id=job.id, limit=100)
        second = list_operation_logs(self.settings, task_type="scan_job", task_id=job.id,
                                     limit=100, after_id=first.latest_id)
        self.assertEqual(100, len(first.items))
        self.assertGreater(first.latest_id, first.items[-1].id)
        self.assertEqual(0, len(second.items))
        print("R02: total=150, received=100, next page=0, skipped=50")

    def test_R03_scan_progress_is_not_visible_before_commit(self):
        visibleStages = []
        originalRecorder = record_operation_log

        def observe(*args, **kwargs):
            result = originalRecorder(*args, **kwargs)
            if kwargs.get("stage") == "scan_progress":
                page = list_operation_logs(self.settings, task_type="scan_job", task_id=kwargs["task_id"])
                visibleStages.extend(item.stage for item in page.items)
            return result

        with patch("app.service.scan_service.record_operation_log", side_effect=observe):
            self.seedFiles(2)
        self.assertNotIn("scan_progress", visibleStages)
        self.assertIn("scan_prepare", visibleStages)
        print("R03: independent reader sees preparation but no scan progress during scan")

    def test_R04_pending_move_partial_failure_rolls_back_only_database(self):
        self.seedFiles(2, threshold=100)
        items = list_pending_files(self.settings)
        items.sort(key=lambda item: item.id)
        Path(items[1].file_path).unlink()
        destination = self.root / "destination"
        destination.mkdir()
        with self.assertRaises(ValueError):
            move_pending_files(self.settings, [item.id for item in items], str(destination))
        self.assertTrue((destination / items[0].file_name).exists())
        current = {item.id: item for item in list_pending_files(self.settings)}
        self.assertEqual("pending", current[items[0].id].status)
        self.assertFalse(Path(current[items[0].id].file_path).exists())
        print("R04: first file moved; database still says pending at nonexistent source")

    def test_R05_expired_archive_is_never_removed(self):
        archiveDir = self.settings.logging.log_dir / "archive"
        archiveDir.mkdir(parents=True)
        archiveFile = archiveDir / "old.log.zip"
        archiveFile.write_bytes(b"review")
        oldTime = (datetime.now(timezone.utc) - timedelta(days=100)).timestamp()
        os.utime(archiveFile, (oldTime, oldTime))
        cleanup_logs(self.settings)
        self.assertTrue(archiveFile.exists())
        print("R05: 100-day-old archive survived retention cleanup")

    def test_R06_password_change_keeps_existing_sessions(self):
        user = bootstrap_admin(self.settings, "reviewadmin", "review", "ReviewOnly123!")
        oldSession = login(self.settings, user.username, "ReviewOnly123!")
        change_password(self.settings, user.id, "ReviewOnly123!", "ReviewNew456!")
        self.assertEqual(user.id, get_user_by_token(self.settings, oldSession.access_token).id)
        print("R06: old session remains valid after password change")

    def test_R07_threshold_change_does_not_reprocess_ignored_file(self):
        self.seedFiles(threshold=100)
        update_setting_values(self.settings, {"scan.minimum_file_size": 0})
        job = run_full_scan(self.settings, self.source.id, scan_mode="incremental")
        self.assertEqual(0, job.video_count)
        self.assertEqual(1, job.skipped_count)
        print("R07: lower threshold + incremental scan still skips ignored file")

    def test_R08_cross_origin_href_is_accepted(self):
        result = _url_from_href("https://review.example/media/", "https://other.example/private/")
        self.assertEqual("https://other.example/private/", result)
        print("R08: remote href can redirect traversal to a different origin")

    def prepareRename(self, count=1):
        self.seedFiles(count)
        generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)
        for preview in previews:
            update_rename_preview(self.settings, preview.id, f"Safe{preview.id}")
        return previews, create_rename_dry_run(self.settings, [item.id for item in previews])

    def test_R09_excluded_preview_still_executes_old_dry_run(self):
        previews, operation = self.prepareRename()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE rename_previews SET status = 'excluded' WHERE id = ?", (previews[0].id,))
            connection.commit()
        result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(1, result.renamed_count)
        print("R09: preview excluded after dry-run; old operation still renames file")

    def test_R10_two_remote_items_hit_nested_connection_lock(self):
        previews, operation = self.prepareRename(2)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE media_sources SET path_type = 'webdav', path = 'https://review.example/media/'")
            for item in operation.items:
                sourceUrl = f"https://review.example/media/source{item.id}.mkv"
                targetUrl = f"https://review.example/media/target{item.id}.mkv"
                connection.execute("UPDATE rename_operation_items SET source_path = ?, target_path = ? WHERE id = ?",
                                   (sourceUrl, targetUrl, item.id))
            connection.commit()

        class FakeProtocol:
            def move_file(self, *args):
                return ConnectionTestResult(True, "simulated")

        with patch("app.service.rename_operation_service.get_protocol", return_value=FakeProtocol()):
            result = execute_rename_operation(self.settings, operation.id)
        self.assertEqual(1, result.renamed_count)
        self.assertEqual(1, result.failed_count)
        self.assertTrue(any("database is locked" in (item.message or "") for item in result.items))
        print("R10: two simulated remote moves => one success, one database is locked")

    def test_R11_released_lock_can_be_acquired_twice_concurrently(self):
        from app.service import remote_operation_service as lockService

        lockKey = f"media-source:{self.source.id}:write"
        first = acquire_remote_operation_lock(self.settings, media_source_id=self.source.id, lock_key=lockKey)
        release_remote_operation_lock(self.settings, lockKey, first.lease_token)
        barrier = threading.Barrier(2)
        originalFetch = lockService._fetch_lock

        def synchronizedFetch(connection, key):
            result = originalFetch(connection, key)
            barrier.wait(timeout=10)
            return result

        def acquire():
            return acquire_remote_operation_lock(self.settings, media_source_id=self.source.id, lock_key=lockKey)

        with patch.object(lockService, "_fetch_lock", side_effect=synchronizedFetch):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(acquire) for _ in range(2)]
                leases = [future.result(timeout=15) for future in futures]
        self.assertNotEqual(leases[0].lease_token, leases[1].lease_token)
        print("R11: both concurrent callers acquired the same released lock")

    def test_R12_nonrecursive_scan_marks_existing_nested_file_missing(self):
        nestedDir = self.mediaDir / "nested"
        nestedDir.mkdir()
        nestedFile = nestedDir / "Nested.2024.mkv"
        nestedFile.write_bytes(b"review")
        run_full_scan(self.settings, self.source.id)
        update_setting_values(self.settings, {"scan.recursive": False})
        job = run_full_scan(self.settings, self.source.id)
        self.assertEqual(1, job.missing_count)
        self.assertTrue(nestedFile.exists())
        print("R12: nonrecursive scan marks existing nested file missing")

    def test_R19_source_deletion_leaves_pending_and_index_rows(self):
        self.seedFiles(threshold=100)
        delete_media_source(self.settings, self.source.id)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            pendingCount = connection.execute("SELECT COUNT(*) FROM pending_files").fetchone()[0]
            indexCount = connection.execute("SELECT COUNT(*) FROM scan_file_index").fetchone()[0]
        self.assertEqual(1, pendingCount)
        self.assertEqual(1, indexCount)
        print("R19: source deleted but pending/index rows remain")

    def test_R14_concurrent_local_execution_overwrites_success(self):
        from app.service import rename_operation_service as renameService

        previews, operation = self.prepareRename()
        barrier = threading.Barrier(2)
        originalGet = get_rename_operation
        firstDone = threading.Event()
        ordinalCounter = itertools.count(1)
        workerState = threading.local()

        def synchronizedGet(settings, operationId):
            result = originalGet(settings, operationId)
            if result.status == "dry_run":
                workerState.ordinal = next(ordinalCounter)
                barrier.wait(timeout=10)
                if workerState.ordinal == 2:
                    self.assertTrue(firstDone.wait(timeout=10))
            return result

        def execute():
            try:
                return execute_rename_operation(self.settings, operation.id)
            finally:
                if getattr(workerState, "ordinal", None) == 1:
                    firstDone.set()

        with patch.object(renameService, "get_rename_operation", side_effect=synchronizedGet):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(execute) for _ in range(2)]
                for future in futures:
                    future.result(timeout=15)
        final = originalGet(self.settings, operation.id)
        self.assertEqual("failed", final.status)
        self.assertTrue(Path(operation.items[0].target_path).exists())
        print("R14: concurrent execution overwrote successful rename with failed status")

    def test_F01_override_decision_does_not_allow_submission(self):
        update_setting_values(self.settings, {
            "privacy.default_sensitive_words_enabled": False,
            "privacy.custom_sensitive_words": ["reviewblocked"],
        })
        parameters = dict(source_module="rename_preview", source_record_id=1,
                          file_name="reviewblocked.mkv", file_path="/review/reviewblocked.mkv",
                          match_title="reviewblocked", target_service="ai")
        first = check_external_submission(self.settings, **parameters)
        update_external_submission_block_decision(
            self.settings, first.block_record.id, "override_submitted", "review", "review only", "reviewadmin",
        )
        second = check_external_submission(self.settings, **parameters)
        self.assertFalse(second.allowed)
        self.assertNotEqual(first.block_record.id, second.block_record.id)
        print("F01: confirmed override still blocks next submission and creates another block")


if __name__ == "__main__":
    unittest.main(verbosity=2)
