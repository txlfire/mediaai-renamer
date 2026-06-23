"""日志系统测试。

验证项目日志初始化、文件创建和重复初始化行为，避免日志 handler 重复挂载。
"""

import logging
import tempfile
import unittest
from pathlib import Path

from app.core.config import LoggingSettings
from app.core.logger import (
    PROJECT_HANDLER_MARK,
    get_batch_logger,
    get_llm_logger,
    get_logger,
    shutdown_logging,
    setup_logging,
)


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


if __name__ == "__main__":
    unittest.main()
