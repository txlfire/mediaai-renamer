"""应用配置模块。

负责集中定义运行时配置对象。后续读取 config.toml 时，也应先映射到本模块中的配置模型，
避免业务代码直接依赖配置文件结构。
"""

from dataclasses import dataclass
from pathlib import Path
import tomllib


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
    version: str = "0.7.7"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/mediaai.sqlite3")
    logging: LoggingSettings = LoggingSettings()
    scan: ScanSettings = ScanSettings()


DEFAULT_CONFIG_PATH = Path("config/config.toml")


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """加载应用配置。

    优先读取正式配置文件 config/config.toml；文件不存在时回退到代码默认值。

    Returns:
        应用运行配置。
    """

    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return AppSettings()

    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    app_config = config.get("app", {})
    paths_config = config.get("paths", {})
    logging_config = config.get("logging", {})
    scan_config = config.get("scan", {})

    data_dir = Path(str(paths_config.get("data_dir", AppSettings.data_dir)))
    database_path = Path(str(paths_config.get("database_path", data_dir / "mediaai.sqlite3")))

    logging = LoggingSettings(
        level=str(logging_config.get("level", LoggingSettings.level)),
        log_dir=Path(str(logging_config.get("log_dir", LoggingSettings.log_dir))),
        max_size_mb=int(logging_config.get("max_size_mb", LoggingSettings.max_size_mb)),
        backup_count=int(logging_config.get("backup_count", LoggingSettings.backup_count)),
        console_output=bool(logging_config.get("console_output", LoggingSettings.console_output)),
    )
    scan = ScanSettings(
        batch_size=int(scan_config.get("batch_size", ScanSettings.batch_size)),
        batch_interval_seconds=float(
            scan_config.get("batch_interval_seconds", ScanSettings.batch_interval_seconds)
        ),
    )

    return AppSettings(
        app_name=str(app_config.get("name", AppSettings.app_name)),
        version=str(app_config.get("version", AppSettings.version)),
        data_dir=data_dir,
        database_path=database_path,
        logging=logging,
        scan=scan,
    )
