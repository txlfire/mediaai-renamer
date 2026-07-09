import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.operation_log_service import (
    LEVEL_INFO,
    TASK_TYPE_SCAN_JOB,
    cleanup_operation_logs,
    list_operation_logs,
    record_operation_log,
)
from app.service.settings_service import update_setting_values


class OperationLogServiceTest(unittest.TestCase):
    """任务运行日志服务。"""

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
