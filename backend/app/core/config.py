"""应用配置模块。

负责集中定义运行时配置对象。后续读取 config.toml 时，也应先映射到本模块中的配置模型，
避免业务代码直接依赖配置文件结构。
"""

from dataclasses import dataclass, field
import math
from pathlib import Path
import os
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
    task_timeout_seconds: int = 3600

    def __post_init__(self):
        """校验扫描配置，避免无效配置导致扫描任务失控。"""

        if self.batch_size <= 0:
            raise ValueError("扫描批大小必须大于 0")
        if self.batch_interval_seconds < 0:
            raise ValueError("扫描批间隔不得小于 0")
        if self.task_timeout_seconds <= 0:
            raise ValueError("扫描任务总超时必须大于 0")


@dataclass(frozen=True)
class AuthSettings:
    """认证配置。

    Attributes:
        default_admin_enabled: 是否在用户表为空时自动创建默认 admin。
        default_admin_username: 默认管理员用户名。
        default_admin_display_name: 默认管理员显示名称。
        default_admin_password: 默认管理员初始密码。
        admin_password_reset_enabled: 是否启用隐藏的 admin 密码重置接口。
    """

    default_admin_enabled: bool = False
    default_admin_username: str = "admin"
    default_admin_display_name: str = "系统管理员"
    default_admin_password: str = "123456"
    admin_password_reset_enabled: bool = False


@dataclass(frozen=True)
class ExternalSiteLimits:
    """配置服务的启动频率和实际HTTP并发，不涉及本地操作。"""

    requests_per_second: float = 2
    max_concurrency: int = 1

    def __post_init__(self):
        if (isinstance(self.requests_per_second, bool)
                or not isinstance(self.requests_per_second, (int, float))
                or not 0.1 <= self.requests_per_second <= 100
                or not math.isfinite(self.requests_per_second)):
            raise ValueError("外部请求频率须为0.1至100的有限数值")
        if isinstance(self.max_concurrency, bool) or not isinstance(self.max_concurrency, int) or not 1 <= self.max_concurrency <= 64:
            raise ValueError("外部站点并发须为1至64的整数")


@dataclass(frozen=True)
class ExternalRequestSettings:
    """同一数据库实例跨工作进程共享预算，可按服务类别/Provider覆盖。"""

    max_concurrency: int = 4
    ai: ExternalSiteLimits = ExternalSiteLimits()
    metadata: ExternalSiteLimits = ExternalSiteLimits()
    providers: dict[str, ExternalSiteLimits] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.max_concurrency, bool) or not isinstance(self.max_concurrency, int) or not 1 <= self.max_concurrency <= 64:
            raise ValueError("外部请求全局并发须为1至64的整数")
        if not isinstance(self.ai, ExternalSiteLimits) or not isinstance(self.metadata, ExternalSiteLimits):
            raise ValueError("外部请求分组配置无效")
        if not isinstance(self.providers, dict) or set(self.providers) - {"tmdb", "bangumi", "tvdb", "douban_proxy", "imdb"} or any(not isinstance(value, ExternalSiteLimits) for value in self.providers.values()):
            raise ValueError("外部请求Provider覆盖配置无效")

    def forService(self, service: str) -> ExternalSiteLimits:
        if service == "ai":
            return self.ai
        if service not in {"tmdb", "bangumi", "tvdb", "douban_proxy", "imdb"}:
            raise ValueError("不支持的外部请求服务")
        return self.providers.get(service, self.metadata)


def externalRequestSettingsFromDict(value: dict) -> ExternalRequestSettings:
    """统一解析TOML和父进程传来的非敏感预算配置，拒绝隐式类型转换。"""
    if not isinstance(value, dict) or set(value) - {"max_concurrency", "ai", "metadata", "providers"}:
        raise ValueError("外部请求配置字段无效")
    def site(raw, default):
        if not isinstance(raw, dict) or set(raw) - {"requests_per_second", "max_concurrency"}:
            raise ValueError("外部站点配置字段无效")
        return ExternalSiteLimits(raw.get("requests_per_second", default.requests_per_second),
                                  raw.get("max_concurrency", default.max_concurrency))
    metadata = site(value.get("metadata", {}), ExternalSiteLimits())
    providers = value.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("外部请求Provider配置无效")
    return ExternalRequestSettings(value.get("max_concurrency", 4),
        site(value.get("ai", {}), ExternalSiteLimits()), metadata,
        {name: site(raw, metadata) for name, raw in providers.items()})


@dataclass(frozen=True)
class AppSettings:
    """应用运行配置。"""

    app_name: str = "MediaAI Renamer"
    version: str = "1.1.0"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/mediaai.sqlite3")
    logging: LoggingSettings = LoggingSettings()
    scan: ScanSettings = ScanSettings()
    auth: AuthSettings = AuthSettings()
    credential_key_file: Path | None = None
    external_requests: ExternalRequestSettings = field(default_factory=ExternalRequestSettings)


DEFAULT_CONFIG_PATH = Path("config/config.toml")


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """加载应用配置。

    优先读取正式配置文件 config/config.toml；文件不存在时回退到代码默认值。

    Returns:
        应用运行配置。
    """

    path = Path(config_path or os.environ.get("MEDIAAI_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
    if not path.exists():
        return AppSettings()

    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    app_config = config.get("app", {})
    paths_config = config.get("paths", {})
    logging_config = config.get("logging", {})
    scan_config = config.get("scan", {})
    auth_config = config.get("auth", {})

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
        task_timeout_seconds=int(scan_config.get("task_timeout_seconds", ScanSettings.task_timeout_seconds)),
    )
    auth = AuthSettings(
        default_admin_enabled=bool(
            auth_config.get("default_admin_enabled", AuthSettings.default_admin_enabled)
        ),
        default_admin_username=str(
            auth_config.get("default_admin_username", AuthSettings.default_admin_username)
        ),
        default_admin_display_name=str(
            auth_config.get(
                "default_admin_display_name",
                AuthSettings.default_admin_display_name,
            )
        ),
        default_admin_password=str(
            auth_config.get("default_admin_password", AuthSettings.default_admin_password)
        ),
        admin_password_reset_enabled=bool(
            auth_config.get(
                "admin_password_reset_enabled",
                AuthSettings.admin_password_reset_enabled,
            )
        ),
    )

    return AppSettings(
        app_name=str(app_config.get("name", AppSettings.app_name)),
        version=str(app_config.get("version", AppSettings.version)),
        data_dir=data_dir,
        database_path=database_path,
        logging=logging,
        scan=scan,
        auth=auth,
        external_requests=externalRequestSettingsFromDict(config.get("external_requests", {})),
        credential_key_file=(Path(str(paths_config["credential_key_file"])) if paths_config.get("credential_key_file") else None),
    )
