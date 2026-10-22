import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.operation_log_service import (
    LEVEL_INFO,
    TASK_TYPE_SCAN_JOB,
    cleanup_operation_logs,
    list_operation_logs,
    record_operation_log,
    cleanup_operation_logs_best_effort,
)
from app.service.settings_service import update_setting_values


class OperationLogServiceTest(unittest.TestCase):
    """任务运行日志服务。"""

    def test_borrowed_transaction_sqlite_full_is_not_an_auxiliary_failure(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory=sqlite3.Row
            connection.execute("CREATE TABLE operation_logs(id INTEGER PRIMARY KEY,task_type TEXT,task_id INTEGER,"
                "level TEXT,stage TEXT,message TEXT,progress_current INTEGER,progress_total INTEGER,detail_json TEXT,approx_bytes INTEGER,created_at TEXT)")
            connection.execute("CREATE TABLE business_state(value INTEGER)")
            connection.execute("INSERT INTO business_state VALUES(0)")
            connection.commit()
            connection.execute("PRAGMA max_page_count=3")
            connection.execute("UPDATE business_state SET value=1")
            with self.assertRaises(sqlite3.OperationalError) as raised:
                record_operation_log(None,task_type=TASK_TYPE_SCAN_JOB,task_id=1,level=LEVEL_INFO,stage="test",
                    message="x"*131072,connection=connection)
            self.assertEqual(sqlite3.SQLITE_FULL,raised.exception.sqlite_errorcode)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(0,connection.execute("SELECT value FROM business_state").fetchone()[0])

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def insert_scan_job(self, settings: AppSettings) -> int:
        with closing(sqlite3.connect(settings.database_path)) as connection:
            cursor = connection.execute(
                "INSERT INTO scan_jobs "
                "(media_source_id, status, batch_size, batch_interval_seconds, scan_mode, "
                "scanned_count, video_count, warning_count, new_count, changed_count, "
                "skipped_count, missing_count, indexed_count, started_at, created_at) "
                "VALUES (1, 'completed', 100, 0, 'full', 0, 0, 0, 0, 0, 0, 0, 0, "
                "'2026-07-09T00:00:00+00:00', '2026-07-09T00:00:00+00:00')"
            )
            connection.commit()
            return int(cursor.lastrowid)

    def test_cursor_pages_return_every_log_without_skipping(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            taskId = self.insert_scan_job(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                for index in range(7):
                    record_operation_log(settings, task_type=TASK_TYPE_SCAN_JOB, task_id=taskId,
                                         level=LEVEL_INFO, stage="progress", message=str(index), connection=connection)
                connection.commit()
            cursor = 0
            seen = []
            for _ in range(4):
                page = list_operation_logs(settings, task_type=TASK_TYPE_SCAN_JOB, task_id=taskId, after_id=cursor, limit=2)
                seen.extend(item.message for item in page.items)
                self.assertGreaterEqual(page.latest_id, cursor)
                cursor = page.latest_id
            self.assertEqual([str(index) for index in range(7)], seen)
            empty = list_operation_logs(settings, task_type=TASK_TYPE_SCAN_JOB, task_id=taskId, after_id=cursor, limit=2)
            self.assertEqual(cursor, empty.latest_id)
            self.assertEqual([], empty.items)

    def test_serialization_connection_and_cleanup_failures_are_auxiliary(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            kwargs = dict(task_type=TASK_TYPE_SCAN_JOB, task_id=1, level=LEVEL_INFO, stage="test", message="test")
            self.assertIsNone(record_operation_log(settings, **kwargs, detail={"unsupported": object()}))
            with patch("app.service.operation_log_service._connect", side_effect=sqlite3.OperationalError("connect failed")):
                self.assertIsNone(record_operation_log(settings, **kwargs))
            with patch("app.service.operation_log_service.cleanup_operation_logs", side_effect=RuntimeError("cleanup failed")):
                self.assertIsNotNone(record_operation_log(settings, **kwargs))
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0])
            with patch("app.service.operation_log_service.cleanup_operation_logs", side_effect=RuntimeError("cleanup failed")):
                self.assertIsNone(cleanup_operation_logs_best_effort(settings))

    def test_running_rename_and_rollback_are_reported_as_running(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("INSERT INTO rename_operations (id, status, mode, total_count, created_at, updated_at) VALUES (1, 'running', 'safe_rename', 1, 'now', 'now')")
                connection.execute("INSERT INTO rename_rollback_plans (id, operation_id, status, item_count, created_at, updated_at) VALUES (1, 1, 'running', 1, 'now', 'now')")
                connection.commit()
            for taskType in ("rename_operation", "rollback_plan"):
                page = list_operation_logs(settings, task_type=taskType, task_id=1)
                self.assertTrue(page.running)
                self.assertFalse(page.cleared)
                self.assertIn("正在等待", page.message)

    def test_record_and_list_operation_logs_sanitizes_sensitive_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            task_id = self.insert_scan_job(settings)

            record_operation_log(
                settings,
                task_type=TASK_TYPE_SCAN_JOB,
                task_id=task_id,
                level=LEVEL_INFO,
                stage="scan_prepare",
                message="开始扫描",
                detail={"api_key": "secret-key", "visible": "ok"},
            )

            page = list_operation_logs(
                settings,
                task_type=TASK_TYPE_SCAN_JOB,
                task_id=task_id,
            )

            self.assertTrue(page.log_available)
            self.assertFalse(page.cleared)
            self.assertEqual(1, page.total)
            self.assertEqual("******", page.items[0].detail["api_key"])
            self.assertEqual("ok", page.items[0].detail["visible"])

    def test_cleanup_operation_logs_trims_by_size_and_reports_cleared_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            task_id = self.insert_scan_job(settings)
            update_setting_values(
                settings,
                {
                    "operation_logs.max_total_mb": 1,
                    "operation_logs.max_task_mb": 1,
                    "operation_logs.cleanup_batch_size": 100,
                },
            )

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                record_operation_log(
                    settings,
                    task_type=TASK_TYPE_SCAN_JOB,
                    task_id=task_id,
                    level=LEVEL_INFO,
                    stage="scan_progress",
                    message="x" * (1200 * 1024),
                    connection=connection,
                )
                connection.commit()

            summary = cleanup_operation_logs(settings)
            page = list_operation_logs(
                settings,
                task_type=TASK_TYPE_SCAN_JOB,
                task_id=task_id,
            )

            self.assertGreaterEqual(summary.deleted, 1)
            self.assertFalse(page.log_available)
            self.assertTrue(page.cleared)
            self.assertIn("保留策略清理", page.message)


if __name__ == "__main__":
    unittest.main()
