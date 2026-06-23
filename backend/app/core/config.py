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
class AppSettings:
    """应用运行配置。"""

    app_name: str = "MediaAI Renamer"
    version: str = "0.1.0"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/mediaai.sqlite3")
    logging: LoggingSettings = LoggingSettings()


def load_settings() -> AppSettings:
    """加载应用配置。

    当前 M0 阶段先返回默认配置；后续接入 config.toml 时，应在这里完成解析和校验。

    Returns:
        应用运行配置。
    """

    return AppSettings()
