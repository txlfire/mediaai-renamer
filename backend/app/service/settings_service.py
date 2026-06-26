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


def _parse_string(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip()
    if definition.allowed_values and parsed not in definition.allowed_values:
        raise ValueError(f"{definition.key} 不在允许范围内")
    return parsed


def _parse_value(value: object, definition: SettingDefinition) -> object:
    if definition.value_type == "bool":
        return _parse_bool(value)
    if definition.value_type == "int":
        return _parse_int(value, definition)
    if definition.value_type == "secret":
        return _parse_secret(value)
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
            raise ValueError(f"未知配置项: {key}")
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
    """Validate current effective TMDB configuration."""

    effective = get_effective_settings(settings)
    api_key = str(effective.get("tmdb.api_key") or "").strip()
    if not api_key:
        raise ValueError("TMDB 连接失败：未配置 API Key。请先填写并保存 TMDB API Key。")

    client = TmdbClient(
        api_key=api_key,
        language=str(effective.get("tmdb.language") or "zh-CN"),
        region=str(effective.get("tmdb.region") or "CN"),
        timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
    )
    try:
        client.test_connection()
    except RuntimeError as exc:
        reason = str(exc)
        if "HTTP 401" in reason:
            message = "TMDB 连接失败：API Key 无效或无权限。请检查密钥是否填写正确。"
        elif "HTTP 404" in reason:
            message = "TMDB 连接失败：接口地址不可用。请稍后重试。"
        elif "timed out" in reason.lower() or "timeout" in reason.lower():
            message = "TMDB 连接失败：请求超时。请检查网络、代理或适当调大超时时间。"
        else:
            message = f"TMDB 连接失败：{reason}。请检查网络、代理或 API Key。"
        raise ValueError(message) from exc

    return {"success": True, "message": "连接成功！信息有效！"}
