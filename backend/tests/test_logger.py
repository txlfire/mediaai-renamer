"""日志系统测试。

验证项目日志初始化、文件创建和重复初始化行为，避免日志 handler 重复挂载。
"""

import logging
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.logger import (
    PROJECT_HANDLER_MARK,
    get_batch_logger,
    get_llm_logger,
    get_logger,
    shutdown_logging,
    setup_logging,
)
from app.service.log_service import cleanup_logs
from app.service.settings_service import update_setting_values


def _writeLogsFromChild(logDir: str, workerId: int, count: int) -> None:
    settings = LoggingSettings(
        level="INFO",
        log_dir=Path(logDir),
        max_size_mb=1,
        backup_count=8,
        console_output=False,
    )
    setup_logging(settings)
    logger = get_logger(f"multiprocess.worker{workerId}")
    padding = "x" * 2500
    for index in range(count):
        logger.info("worker=%s item=%s %s", workerId, index, padding)
    shutdown_logging()


class LoggerSetupTest(unittest.TestCase):
    """日志基础设施测试用例。"""

    def test_setup_logging_creates_rotating_log_files(self):
        """初始化日志后应创建所有标准日志文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LoggingSettings(
                level="DEBUG",
                log_dir=Path(temp_dir),
                max_size_mb=1,
                backup_count=2,
                console_output=False,
            )

            setup_logging(settings)

            get_logger(__name__).info("应用日志测试")
            get_llm_logger().info("LLM 日志测试")
            get_batch_logger().info("批量任务日志测试")
            logging.getLogger("mediaai.test").error("错误日志测试")

            for file_name in ["app.log", "error.log", "llm.log", "batch.log"]:
                log_file = Path(temp_dir) / file_name
                self.assertTrue(log_file.exists(), f"{file_name} should be created")
            shutdown_logging()

    def test_setup_logging_does_not_duplicate_project_handlers(self):
        """重复初始化日志时，不应重复挂载项目 handler。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LoggingSettings(log_dir=Path(temp_dir), console_output=False)

            setup_logging(settings)
            setup_logging(settings)

            mediaai_logger = logging.getLogger("mediaai")
            project_handlers = [
                handler
                for handler in mediaai_logger.handlers
                if getattr(handler, PROJECT_HANDLER_MARK, False)
            ]

            self.assertEqual(2, len(project_handlers))
            shutdown_logging()

    def test_cleanup_logs_archives_old_rotated_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_dir = root / "logs"
            log_dir.mkdir()
            old_log = log_dir / "app.log.1"
            old_log.write_text("old", encoding="utf-8")
            old_time = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
            os.utime(old_log, (old_time, old_time))
            settings = AppSettings(
                data_dir=root,
                database_path=root / "mediaai.sqlite3",
                logging=LoggingSettings(log_dir=log_dir, console_output=False),
            )
            from app.core.database import ensure_database

            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "logging.path": str(log_dir),
                    "logging.archive_after_days": 7,
                    "logging.retention_days": 30,
                },
            )

            result = cleanup_logs(settings)

            self.assertEqual(1, result["archived_count"])
            self.assertFalse(old_log.exists())
            self.assertTrue((log_dir / "archive" / "app.log.1.zip").exists())
            shutdown_logging()

    @unittest.skipUnless(os.name == "nt", "仅验证 Windows 多进程日志句柄与轮转")
    def test_multiple_processes_rotate_while_cleanup_runs_without_losing_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_dir = root / "logs"
            log_dir.mkdir()
            old_log = log_dir / "batch.log.9"
            old_log.write_text("old", encoding="utf-8")
            old_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
            os.utime(old_log, (old_time, old_time))
            settings = AppSettings(
                data_dir=root,
                database_path=root / "mediaai.sqlite3",
                logging=LoggingSettings(log_dir=log_dir, console_output=False),
            )
            from app.core.database import ensure_database

            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "logging.path": str(log_dir),
                    "logging.archive_after_days": 7,
                    "logging.retention_days": 30,
                },
            )
            context = multiprocessing.get_context("spawn")
            processes = [
                context.Process(target=_writeLogsFromChild, args=(str(log_dir), workerId, 300))
                for workerId in range(2)
            ]
            for process in processes:
                process.start()

            cleanup = cleanup_logs(settings)
            for process in processes:
                process.join(30)
                self.assertFalse(process.is_alive())
                self.assertEqual(0, process.exitcode)

            self.assertGreaterEqual(cleanup["deleted_count"], 1)
            contents = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(log_dir.glob("app.log*"))
                if path.is_file() and path.name != ".mediaai-log.lock"
            )
            for workerId in range(2):
                for index in range(300):
                    self.assertIn(f"worker={workerId} item={index} ", contents)
            shutdown_logging()


if __name__ == "__main__":
    unittest.main()
