"""System hot settings service."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import sqlite3

from app.core.config import AppSettings
from app.schema.settings import SettingValue
from app.service.tmdb_client import TmdbClient


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    category: str
    default: object
    value_type: str
    description: str
    sensitive: bool = False
    env_var: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    allowed_values: tuple[str, ...] = ()


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "tmdb.v4_token": SettingDefinition(
        key="tmdb.v4_token",
        category="tmdb",
        default="",
        value_type="token",
        description="TMDB V4 Access Token",
        sensitive=True,
        env_var="TMDB_V4_TOKEN",
    ),
    "tmdb.api_key": SettingDefinition(
        key="tmdb.api_key",
        category="tmdb",
        default="",
        value_type="secret",
        description="TMDB API Key",
        sensitive=True,
        env_var="TMDB_API_KEY",
    ),
    "tmdb.language": SettingDefinition(
        key="tmdb.language",
        category="tmdb",
        default="zh-CN",
        value_type="string",
        description="TMDB request language",
        env_var="TMDB_LANGUAGE",
        allowed_values=("zh-CN", "en-US"),
    ),
    "tmdb.region": SettingDefinition(
        key="tmdb.region",
        category="tmdb",
        default="CN",
        value_type="string",
        description="TMDB request region",
        env_var="TMDB_REGION",
        allowed_values=("CN", "US", "HK", "TW", "JP", "KR"),
    ),
    "tmdb.timeout_ms": SettingDefinition(
        key="tmdb.timeout_ms",
        category="tmdb",
        default=10000,
        value_type="int",
        description="TMDB request timeout in milliseconds",
        env_var="TMDB_TIMEOUT_MS",
        min_value=1000,
        max_value=30000,
    ),
    "tmdb.enabled": SettingDefinition(
        key="tmdb.enabled",
        category="tmdb",
        default=False,
        value_type="bool",
        description="Enable TMDB metadata scraping",
        env_var="TMDB_ENABLED",
    ),
    "tmdb.priority": SettingDefinition(
        key="tmdb.priority",
        category="tmdb",
        default=1,
        value_type="int",
        description="TMDB scraper priority",
        env_var="TMDB_PRIORITY",
        min_value=0,
        max_value=100,
    ),
    "scan.minimum_file_size": SettingDefinition(
        key="scan.minimum_file_size",
        category="scan",
        default=0,
        value_type="int",
        description="Minimum media file size in bytes",
        env_var="SCAN_MINIMUM_FILE_SIZE",
        min_value=0,
        max_value=10_000_000_000,
    ),
    "scan.batch_size": SettingDefinition(
        key="scan.batch_size",
        category="scan",
        default=100,
        value_type="int",
        description="Scan batch size",
        min_value=1,
        max_value=10000,
    ),
    "scan.batch_interval_seconds": SettingDefinition(
        key="scan.batch_interval_seconds",
        category="scan",
        default=1,
        value_type="int",
        description="Interval seconds between scan batches",
        min_value=0,
        max_value=3600,
    ),
    "scan.skip_hidden_files": SettingDefinition(
        key="scan.skip_hidden_files",
        category="scan",
        default=True,
        value_type="bool",
        description="Skip hidden files when scanning",
    ),
    "scan.recursive": SettingDefinition(
        key="scan.recursive",
        category="scan",
        default=True,
        value_type="bool",
        description="Scan subdirectories recursively",
    ),
    "scan.validate_path_before_scan": SettingDefinition(
        key="scan.validate_path_before_scan",
        category="scan",
        default=True,
        value_type="bool",
        description="Validate media source path before scan",
    ),
    "naming.movie_template": SettingDefinition(
        key="naming.movie_template",
        category="naming",
        default="{title}.{year}",
        value_type="template",
        description="Movie naming template",
    ),
    "naming.episode_template": SettingDefinition(
        key="naming.episode_template",
        category="naming",
        default="{title}.{year}.S{season:02d}E{episode:02d}",
        value_type="template",
        description="Episode naming template",
    ),
    "naming.separator": SettingDefinition(
        key="naming.separator",
        category="naming",
        default=".",
        value_type="separator",
        description="Filename separator",
    ),
    "naming.keep_year": SettingDefinition(
        key="naming.keep_year",
        category="naming",
        default=True,
        value_type="bool",
        description="Keep year in generated names",
    ),
    "naming.clean_illegal_chars": SettingDefinition(
        key="naming.clean_illegal_chars",
        category="naming",
        default=True,
        value_type="bool",
        description="Clean illegal filename characters",
    ),
    "naming.text_truncate_bytes": SettingDefinition(
        key="naming.text_truncate_bytes",
        category="naming",
        default=50,
        value_type="int",
        description="Text truncation bytes for lists",
        min_value=1,
        max_value=1000,
    ),
    "naming.path_truncate_bytes": SettingDefinition(
        key="naming.path_truncate_bytes",
        category="naming",
        default=80,
        value_type="int",
        description="Path truncation bytes for lists",
        min_value=1,
        max_value=2000,
    ),
    "operations.log_retention_days": SettingDefinition(
        key="operations.log_retention_days",
        category="operations",
        default=30,
        value_type="int",
        description="Log retention days",
        min_value=0,
        max_value=3650,
    ),
    "operations.log_default_limit": SettingDefinition(
        key="operations.log_default_limit",
        category="operations",
        default=200,
        value_type="int",
        description="Default operation log item limit",
        min_value=1,
        max_value=10000,
    ),
    "operations.force_dry_run": SettingDefinition(
        key="operations.force_dry_run",
        category="operations",
        default=True,
        value_type="bool",
        description="Force dry-run before rename",
    ),
    "operations.require_second_confirmation": SettingDefinition(
        key="operations.require_second_confirmation",
        category="operations",
        default=True,
        value_type="bool",
        description="Require second confirmation before execution",
    ),
    "operations.persist_failure_detail": SettingDefinition(
        key="operations.persist_failure_detail",
        category="operations",
        default=True,
        value_type="bool",
        description="Persist failure detail",
    ),
    "operations.batch_limit": SettingDefinition(
        key="operations.batch_limit",
        category="operations",
        default=200,
        value_type="int",
        description="Batch operation item limit",
        min_value=1,
        max_value=10000,
    ),
    "shared.default_path_type": SettingDefinition(
        key="shared.default_path_type",
        category="shared",
        default="local",
        value_type="string",
        description="Default media source path type",
        allowed_values=("local", "unc", "mounted_nfs"),
    ),
    "shared.connection_timeout_seconds": SettingDefinition(
        key="shared.connection_timeout_seconds",
        category="shared",
        default=5,
        value_type="int",
        description="Shared directory connection timeout seconds",
        min_value=1,
        max_value=300,
    ),
    "shared.directory_browse_limit": SettingDefinition(
        key="shared.directory_browse_limit",
        category="shared",
        default=500,
        value_type="int",
        description="Directory browse max item count",
        min_value=1,
        max_value=10000,
    ),
    "shared.force_scan_connection_test": SettingDefinition(
        key="shared.force_scan_connection_test",
        category="shared",
        default=True,
        value_type="bool",
        description="Force connection test before scan",
    ),
    "shared.force_rename_write_test": SettingDefinition(
        key="shared.force_rename_write_test",
        category="shared",
        default=True,
        value_type="bool",
        description="Force write permission test before rename",
    ),
    "shared.nfs_operation_timeout_seconds": SettingDefinition(
        key="shared.nfs_operation_timeout_seconds",
        category="shared",
        default=30,
        value_type="int",
        description="NFS operation timeout seconds",
        min_value=1,
        max_value=3600,
    ),
    "shared.nfs_retry_count": SettingDefinition(
        key="shared.nfs_retry_count",
        category="shared",
        default=3,
        value_type="int",
        description="NFS retry count",
        min_value=0,
        max_value=20,
    ),
    "shared.prefer_nfsv4": SettingDefinition(
        key="shared.prefer_nfsv4",
        category="shared",
        default=True,
        value_type="bool",
        description="Prefer NFSv4",
    ),
    "shared.mount_check_interval_seconds": SettingDefinition(
        key="shared.mount_check_interval_seconds",
        category="shared",
        default=60,
        value_type="int",
        description="NFS mount check interval seconds",
        min_value=1,
        max_value=86400,
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError("开关参数仅支持 true 或 false")


def _parse_int(value: object, definition: SettingDefinition) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{definition.key} 必须为整数")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{definition.key} 必须为整数") from exc
    if str(value).strip() != str(parsed):
        raise ValueError(f"{definition.key} 必须为整数")
    if definition.min_value is not None and parsed < definition.min_value:
        raise ValueError(f"{definition.key} 不能小于 {definition.min_value}")
    if definition.max_value is not None and parsed > definition.max_value:
        raise ValueError(f"{definition.key} 不能大于 {definition.max_value}")
    return parsed


def _parse_secret(value: object) -> str:
    parsed = str(value).strip()
    if parsed and re.search(r"[^\w.\-]", parsed):
        raise ValueError("密钥包含非法字符")
    return parsed


def _parse_token(value: object) -> str:
    """JWT 宽松校验，仅做长度检查，不限制字符格式。"""
    parsed = str(value).strip()
    if parsed and len(parsed) > 1024:
        raise ValueError("令牌长度超出限制")
    return parsed


def _parse_string(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip()
    if definition.allowed_values and parsed not in definition.allowed_values:
        raise ValueError(f"{definition.key} 不在允许范围内")
    return parsed


def _parse_template(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip()
    if not parsed:
        raise ValueError(f"{definition.key} 不能为空")
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", parsed):
        raise ValueError(f"{definition.key} 必须包含有效文本或变量")
    return parsed


def _parse_separator(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip()
    if not parsed:
        raise ValueError(f"{definition.key} 不能为空")
    if any(char in parsed for char in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
        raise ValueError(f"{definition.key} 包含非法文件名字符")
    return parsed


def _parse_value(value: object, definition: SettingDefinition) -> object:
    if definition.value_type == "bool":
        return _parse_bool(value)
    if definition.value_type == "int":
        return _parse_int(value, definition)
    if definition.value_type == "secret":
        return _parse_secret(value)
    if definition.value_type == "token":
        return _parse_token(value)
    if definition.value_type == "template":
        return _parse_template(value, definition)
    if definition.value_type == "separator":
        return _parse_separator(value, definition)
    return _parse_string(value, definition)


def _load_database_values(settings: AppSettings) -> dict[str, tuple[str, str | None]]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT key, value, updated_at FROM system_settings"
        ).fetchall()
    return {str(row["key"]): (str(row["value"]), row["updated_at"]) for row in rows}


def _effective_value(
    definition: SettingDefinition,
    database_values: dict[str, tuple[str, str | None]],
) -> tuple[object, str, str | None]:
    if definition.env_var:
        env_value = os.environ.get(definition.env_var)
        if env_value not in (None, ""):
            return _parse_value(env_value, definition), "environment", None

    if definition.key in database_values:
        raw_value, updated_at = database_values[definition.key]
        return _parse_value(raw_value, definition), "database", updated_at

    return definition.default, "default", None


def get_effective_settings(settings: AppSettings) -> dict[str, object]:
    """Return effective settings using environment > database > defaults."""

    database_values = _load_database_values(settings)
    effective: dict[str, object] = {}
    for definition in SETTING_DEFINITIONS.values():
        try:
            value, _, _ = _effective_value(definition, database_values)
        except ValueError:
            value = definition.default
        effective[definition.key] = value
    return effective


def _mask_secret(value: object) -> str:
    text = str(value)
    if not text:
        return ""
    suffix = text[-4:] if len(text) >= 4 else text
    return f"********{suffix}"


def list_setting_values(settings: AppSettings) -> list[SettingValue]:
    """List all known settings with sensitive values masked."""

    database_values = _load_database_values(settings)
    values: list[SettingValue] = []
    for definition in SETTING_DEFINITIONS.values():
        try:
            value, source, updated_at = _effective_value(definition, database_values)
        except ValueError:
            value, source, updated_at = definition.default, "default", None
        values.append(
            SettingValue(
                key=definition.key,
                category=definition.category,
                value=_mask_secret(value) if definition.sensitive else value,
                description=definition.description,
                sensitive=definition.sensitive,
                source=source,
                updated_at=updated_at,
            )
        )
    return values


def update_setting_values(
    settings: AppSettings,
    values: dict[str, object],
    operator: str = "admin",
) -> list[SettingValue]:
    """Validate and persist hot settings."""

    parsed_values: dict[str, tuple[SettingDefinition, object]] = {}
    for key, value in values.items():
        definition = SETTING_DEFINITIONS.get(key)
        if definition is None:
            raise ValueError(f"未知配置项 {key}")
        parsed_values[key] = (definition, _parse_value(value, definition))

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        for key, (definition, parsed_value) in parsed_values.items():
            connection.execute(
                "INSERT INTO system_settings "
                "(key, category, value, description, sensitive, operator, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "category = excluded.category, "
                "value = excluded.value, "
                "description = excluded.description, "
                "sensitive = excluded.sensitive, "
                "operator = excluded.operator, "
                "updated_at = excluded.updated_at",
                (
                    key,
                    definition.category,
                    _serialize_value(parsed_value),
                    definition.description,
                    1 if definition.sensitive else 0,
                    operator,
                    now,
                    now,
                ),
            )
        connection.commit()

    return list_setting_values(settings)


def test_tmdb_connection(settings: AppSettings) -> dict[str, object]:
    """Test V4 token and V3 API key separately, return per-channel results."""

    effective = get_effective_settings(settings)
    v4_token = str(effective.get("tmdb.v4_token") or "").strip()
    api_key = str(effective.get("tmdb.api_key") or "").strip()

    v4_result: dict[str, object] = {"status": "skipped", "message": "未配置 V4 令牌"}
    v3_result: dict[str, object] = {"status": "skipped", "message": "未配置 V3 API 密钥"}
    effective_channel = "none"

    # Test V4
    if v4_token:
        client = TmdbClient(
            v4_token=v4_token,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
        )
        try:
            client.test_connection()
            v4_result = {"status": "success", "message": "V4 连接成功"}
            effective_channel = "v4"
        except RuntimeError as exc:
            reason = str(exc)
            if "HTTP 401" in reason:
                v4_result = {"status": "failed", "message": "V4 鉴权失败：令牌无效或无权访问"}
            else:
                v4_result = {"status": "failed", "message": f"V4 连接失败：{reason}"}

    # Test V3
    if api_key:
        client = TmdbClient(
            api_key=api_key,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
        )
        try:
            client.test_connection()
            v3_result = {"status": "success", "message": "V3 连接成功"}
            if effective_channel == "none":
                effective_channel = "v3"
        except RuntimeError as exc:
            reason = str(exc)
            if "HTTP 401" in reason:
                v3_result = {"status": "failed", "message": "V3 鉴权失败：API Key 无效或无权访问"}
            else:
                v3_result = {"status": "failed", "message": f"V3 连接失败：{reason}"}

    # If V4 failed but V3 succeeded, effective channel is V3
    if v4_result["status"] == "failed" and v3_result["status"] == "success":
        effective_channel = "v3"

    return {
        "v4": v4_result,
        "v3": v3_result,
        "effective": effective_channel,
    }
