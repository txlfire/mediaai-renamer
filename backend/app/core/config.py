"""应用配置模块。

负责集中定义运行时配置对象。后续读取 config.toml 时，也应先映射到本模块中的配置模型，
避免业务代码直接依赖配置文件结构。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoggingSettings:
    """日志配置。

    Attributes:
        level: 日志级别，支持 DEBUG、INFO、WARNING、ERROR、CRITICAL。
        log_dir: 日志文件输出目录。
        max_size_mb: 单个日志文件最大体积，单位为 MB。
        backup_count: 轮转日志保留份数。
        console_output: 是否同时输出到控制台。
    """

    level: str = "INFO"
    log_dir: Path = Path("logs")
    max_size_mb: int = 10
    backup_count: int = 5
    console_output: bool = True


@dataclass(frozen=True)
class ScanSettings:
    """扫描配置。

    Attributes:
        batch_size: 单批处理的文件数量。
        batch_interval_seconds: 每批之间的等待秒数。
    """

    batch_size: int = 100
    batch_interval_seconds: float = 1

    def __post_init__(self):
        """校验扫描配置，避免无效配置导致扫描任务失控。"""

        if self.batch_size <= 0:
            raise ValueError("扫描批大小必须大于 0")
        if self.batch_interval_seconds < 0:
            raise ValueError("扫描批间隔不得小于 0")


@dataclass(frozen=True)
class AppSettings:
    """应用运行配置。"""

    app_name: str = "MediaAI Renamer"
    version: str = "0.3.1"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/mediaai.sqlite3")
    logging: LoggingSettings = LoggingSettings()
    scan: ScanSettings = ScanSettings()


def load_settings() -> AppSettings:
    """加载应用配置。

    当前 M0 阶段先返回默认配置；后续接入 config.toml 时，应在这里完成解析和校验。

    Returns:
        应用运行配置。
    """

    return AppSettings()
