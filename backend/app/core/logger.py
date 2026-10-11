"""日志配置模块。

负责初始化 MediaAI Renamer 的项目日志体系。日志输出按用途拆分为应用日志、错误日志、
LLM 调用日志和批量任务日志，便于后续页面查看和导出。
"""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
from threading import RLock
from contextlib import contextmanager, ExitStack

from app.core.config import LoggingSettings

PROJECT_LOGGER_NAME = "mediaai"
LLM_LOGGER_NAME = "mediaai.llm"
BATCH_LOGGER_NAME = "mediaai.batch"
PROJECT_HANDLER_MARK = "_mediaai_project_handler"
loggingConfigurationLock = RLock()
LOG_DIRECTORY_LOCK_FILE = ".mediaai-log.lock"
LOG_DIRECTORY_LOCK_TIMEOUT_SECONDS = 30.0


@contextmanager
def _processLogDirectoryLock(logDir: Path):
    """串行化同一日志目录的跨进程写入、轮转和清理。"""

    logDir.mkdir(parents=True, exist_ok=True)
    lockPath = logDir / LOG_DIRECTORY_LOCK_FILE
    with lockPath.open("a+b") as lockFile:
        lockFile.seek(0, os.SEEK_END)
        if lockFile.tell() == 0:
            lockFile.write(b"0")
            lockFile.flush()
        lockFile.seek(0)
        deadline = time.monotonic() + LOG_DIRECTORY_LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(lockFile.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("等待日志目录锁超时") from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                lockFile.seek(0)
                msvcrt.locking(lockFile.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(lockFile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("等待日志目录锁超时") from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(lockFile.fileno(), fcntl.LOCK_UN)


@contextmanager
def guardLogFileMutation(logDir: Path):
    """暂停本机各进程的日志写入和轮转后再变更日志文件。"""
    with loggingConfigurationLock, ExitStack() as stack:
        for name in (PROJECT_LOGGER_NAME, LLM_LOGGER_NAME, BATCH_LOGGER_NAME):
            for handler in logging.getLogger(name).handlers:
                if (isinstance(handler, RotatingFileHandler)
                        and getattr(handler, PROJECT_HANDLER_MARK, False)
                        and Path(handler.baseFilename).parent.resolve() == logDir.resolve()):
                    handler.acquire()
                    stack.callback(handler.release)
        with _processLogDirectoryLock(logDir):
            yield

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


class ProcessSafeRotatingFileHandler(RotatingFileHandler):
    """使用目录锁协调 Windows 多进程轮转，并避免空闲句柄阻止重命名。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with _processLogDirectoryLock(Path(self.baseFilename).parent):
                super().emit(record)
        except Exception:
            self.handleError(record)
        finally:
            if self.stream is not None:
                try:
                    self.flush()
                finally:
                    self.stream.close()
                    self.stream = None


class PreparedLogging:
    """预先打开新日志文件；提交失败时关闭，不替换已有处理器。"""

    def __init__(self, level: int):
        self.level = level
        self.handlers: dict[str, list[logging.Handler]] = {name: [] for name in (PROJECT_LOGGER_NAME, LLM_LOGGER_NAME, BATCH_LOGGER_NAME)}
        self.active = False

    def activate(self) -> None:
        if self.active:
            return
        with loggingConfigurationLock:
            oldHandlers = []
            for name, handlers in self.handlers.items():
                logger = logging.getLogger(name)
                logger.setLevel(self.level)
                logger.propagate = True
                for old in list(logger.handlers):
                    if getattr(old, PROJECT_HANDLER_MARK, False):
                        logger.removeHandler(old)
                        oldHandlers.append(old)
                for handler in handlers:
                    logger.addHandler(handler)
            self.active = True
            for handler in oldHandlers:
                try:
                    handler.close()
                except Exception:  # noqa: BLE001 - 新配置已生效，旧句柄关闭故障不伪报保存失败。
                    pass

    def close(self) -> None:
        if not self.active:
            for handlers in self.handlers.values():
                for handler in handlers:
                    try:
                        handler.close()
                    except Exception:  # noqa: BLE001 - 预备资源清理不覆盖事务的原始异常。
                        pass


def prepareLogging(settings: LoggingSettings) -> PreparedLogging:
    logDir = Path(settings.log_dir)
    logDir.mkdir(parents=True, exist_ok=True)
    level = _resolve_level(settings.level)
    prepared = PreparedLogging(level)
    formatter = logging.Formatter(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    maxBytes = settings.max_size_mb * 1024 * 1024
    try:
        for name, fileName, handlerLevel in (
            (PROJECT_LOGGER_NAME, "app.log", level),
            (PROJECT_LOGGER_NAME, "error.log", max(logging.ERROR, level)),
            (LLM_LOGGER_NAME, "llm.log", level),
            (BATCH_LOGGER_NAME, "batch.log", level),
        ):
            handler = _create_file_handler(logDir / fileName, handlerLevel, maxBytes, settings.backup_count, formatter)
            prepared.handlers[name].append(handler)
            if name != PROJECT_LOGGER_NAME:
                handler.addFilter(ExactLoggerFilter(name))
        if settings.console_output:
            console = logging.StreamHandler()
            console.setLevel(level)
            console.setFormatter(formatter)
            _mark_project_handler(console)
            prepared.handlers[PROJECT_LOGGER_NAME].append(console)
    except Exception:
        prepared.close()
        raise
    return prepared


def setup_logging(settings: LoggingSettings) -> None:
    """文件可用后再替换处理器，避免初始化失败先关闭旧日志。"""
    with loggingConfigurationLock:
        prepared = prepareLogging(settings)
        try:
            prepared.activate()
        finally:
            prepared.close()
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
        用于记录请求关联、模型、耗时、错误分类及数字 token 用量；不保存正文或估算未知费用。
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

    handler = ProcessSafeRotatingFileHandler(
        filename=file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
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
