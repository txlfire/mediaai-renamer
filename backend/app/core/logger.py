"""日志配置模块。

负责初始化 MediaAI Renamer 的项目日志体系。日志输出按用途拆分为应用日志、错误日志、
LLM 调用日志和批量任务日志，便于后续页面查看和导出。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import LoggingSettings

PROJECT_LOGGER_NAME = "mediaai"
LLM_LOGGER_NAME = "mediaai.llm"
BATCH_LOGGER_NAME = "mediaai.batch"
PROJECT_HANDLER_MARK = "_mediaai_project_handler"

LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class ExactLoggerFilter(logging.Filter):
    """只允许指定 logger 自身的记录进入对应文件。"""

    def __init__(self, logger_name: str) -> None:
        """初始化精确 logger 过滤器。

        Args:
            logger_name: 允许写入的 logger 名称。
        """

        super().__init__()
        self.logger_name = logger_name

    def filter(self, record: logging.LogRecord) -> bool:
        """判断日志记录是否属于指定 logger。

        Args:
            record: Python logging 传入的日志记录。

        Returns:
            属于指定 logger 时返回 True，否则返回 False。
        """

        return record.name == self.logger_name


def setup_logging(settings: LoggingSettings) -> None:
    """初始化项目日志系统。

    Args:
        settings: 日志配置。
    """

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    mediaai_logger = logging.getLogger(PROJECT_LOGGER_NAME)
    mediaai_logger.setLevel(_resolve_level(settings.level))
    mediaai_logger.propagate = True
    _remove_project_handlers(mediaai_logger)

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    max_bytes = settings.max_size_mb * 1024 * 1024

    app_handler = _create_file_handler(
        log_dir / "app.log",
        logging.INFO,
        max_bytes,
        settings.backup_count,
        formatter,
    )
    mediaai_logger.addHandler(app_handler)

    error_handler = _create_file_handler(
        log_dir / "error.log",
        logging.ERROR,
        max_bytes,
        settings.backup_count,
        formatter,
    )
    mediaai_logger.addHandler(error_handler)

    if settings.console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(_resolve_level(settings.level))
        console_handler.setFormatter(formatter)
        _mark_project_handler(console_handler)
        mediaai_logger.addHandler(console_handler)

    _configure_dedicated_logger(
        logger_name=LLM_LOGGER_NAME,
        file_path=log_dir / "llm.log",
        level=logging.INFO,
        max_bytes=max_bytes,
        backup_count=settings.backup_count,
        formatter=formatter,
    )
    _configure_dedicated_logger(
        logger_name=BATCH_LOGGER_NAME,
        file_path=log_dir / "batch.log",
        level=logging.INFO,
        max_bytes=max_bytes,
        backup_count=settings.backup_count,
        formatter=formatter,
    )

    get_logger(__name__).info("日志系统初始化完成")


def shutdown_logging() -> None:
    """关闭项目创建的日志 handler。

    测试或应用优雅停机时调用，确保 Windows 环境下日志文件句柄被释放。
    """

    for logger_name in [PROJECT_LOGGER_NAME, LLM_LOGGER_NAME, BATCH_LOGGER_NAME]:
        _remove_project_handlers(logging.getLogger(logger_name))


def get_logger(name: str) -> logging.Logger:
    """获取项目 logger。

    Args:
        name: 调用方模块名，通常传入 __name__。

    Returns:
        带有项目命名空间的 logger。
    """

    if name.startswith(PROJECT_LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{PROJECT_LOGGER_NAME}.{name}")


def get_llm_logger() -> logging.Logger:
    """获取 LLM 专用 logger。

    Returns:
        用于记录模型、token、耗时、费用估算等统计信息的 logger。
    """

    return logging.getLogger(LLM_LOGGER_NAME)


def get_batch_logger() -> logging.Logger:
    """获取批量任务专用 logger。

    Returns:
        用于记录扫描、预览、重命名等批量任务历史的 logger。
    """

    return logging.getLogger(BATCH_LOGGER_NAME)


def _resolve_level(level: str) -> int:
    """解析配置中的日志级别。"""

    return LEVEL_MAP.get(level.upper(), logging.INFO)


def _create_file_handler(
    file_path: Path,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> RotatingFileHandler:
    """创建带项目标记的轮转文件 handler。"""

    handler = RotatingFileHandler(
        filename=file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    _mark_project_handler(handler)
    return handler


def _configure_dedicated_logger(
    logger_name: str,
    file_path: Path,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> None:
    """配置独立文件 logger，并避免重复挂载 handler。"""

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = True
    _remove_project_handlers(logger)

    handler = _create_file_handler(file_path, level, max_bytes, backup_count, formatter)
    handler.addFilter(ExactLoggerFilter(logger_name))
    logger.addHandler(handler)


def _mark_project_handler(handler: logging.Handler) -> None:
    """给项目创建的 handler 打标，便于重复初始化时安全清理。"""

    setattr(handler, PROJECT_HANDLER_MARK, True)


def _remove_project_handlers(logger: logging.Logger) -> None:
    """移除项目创建的 handler，避免重复初始化导致重复写日志。"""

    for handler in list(logger.handlers):
        if getattr(handler, PROJECT_HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()
