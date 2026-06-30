"""System hot settings service."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import time
from urllib import request as url_request
from urllib.error import URLError

from app.core.config import AppSettings
from app.schema.settings import PageTestResult, SettingValue
from app.service.tmdb_client import TmdbClient


TMDB_TEST_PAGE_KEY = "settings.tmdb"
TMDB_TEST_SNAPSHOT_KEYS = (
    "tmdb.v4_token",
    "tmdb.api_key",
    "tmdb.language",
    "tmdb.region",
    "tmdb.timeout_ms",
    "tmdb.enabled",
    "tmdb.priority",
    "scan.minimum_file_size",
)
IMDB_TEST_SNAPSHOT_KEYS = (
    "imdb.enabled",
    "imdb.priority",
    "imdb.timeout_ms",
)


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
        default=15000,
        value_type="int",
        description="TMDB request timeout in milliseconds",
        env_var="TMDB_TIMEOUT_MS",
        min_value=10000,
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
    "imdb.enabled": SettingDefinition(
        key="imdb.enabled",
        category="imdb",
        default=False,
        value_type="bool",
        description="Enable IMDb supplemental scraping",
    ),
    "imdb.priority": SettingDefinition(
        key="imdb.priority",
        category="imdb",
        default="tmdb_first",
        value_type="string",
        description="IMDb supplemental data priority",
        allowed_values=("tmdb_first", "imdb_first"),
    ),
    "imdb.timeout_ms": SettingDefinition(
        key="imdb.timeout_ms",
        category="imdb",
        default=10000,
        value_type="int",
        description="IMDb request timeout in milliseconds",
        min_value=5000,
        max_value=30000,
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
    "logging.retention_days": SettingDefinition(
        key="logging.retention_days",
        category="logging",
        default=30,
        value_type="int",
        description="Log retention days",
        min_value=1,
        max_value=3650,
    ),
    "logging.level": SettingDefinition(
        key="logging.level",
        category="logging",
        default="INFO",
        value_type="string",
        description="Runtime log level",
        allowed_values=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    "logging.path": SettingDefinition(
        key="logging.path",
        category="logging",
        default="logs",
        value_type="string",
        description="Log storage path",
    ),
    "logging.archive_after_days": SettingDefinition(
        key="logging.archive_after_days",
        category="logging",
        default=7,
        value_type="int",
        description="Archive logs older than days",
        min_value=1,
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


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    return hashlib.sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()


def _tmdb_config_snapshot(effective: dict[str, object]) -> dict[str, object]:
    return {key: effective.get(key) for key in TMDB_TEST_SNAPSHOT_KEYS}


def _imdb_config_snapshot(effective: dict[str, object]) -> dict[str, object]:
    return {key: effective.get(key) for key in IMDB_TEST_SNAPSHOT_KEYS}


def build_tmdb_config_snapshot(settings: AppSettings) -> dict[str, object]:
    """Return the effective TMDB settings snapshot used by connection tests."""

    return _tmdb_config_snapshot(get_effective_settings(settings))


def build_imdb_config_snapshot(settings: AppSettings) -> dict[str, object]:
    """Return the effective IMDb settings snapshot used by connection tests."""

    return _imdb_config_snapshot(get_effective_settings(settings))


def _classify_tmdb_error(reason: str) -> tuple[str, int | None]:
    status_match = re.search(r"HTTP\s+(\d{3})", reason)
    if status_match:
        status_code = int(status_match.group(1))
        if 500 <= status_code <= 599:
            return "server", status_code
        return "client", status_code
    lowered = reason.lower()
    if any(token in lowered for token in ("timed out", "timeout", "??")):
        return "timeout", None
    if any(token in lowered for token in ("name or service", "nodename", "dns", "refused", "unreachable", "network")):
        return "network", None
    return "unknown", None


def _tmdb_error_message(channel: str, error_type: str, http_status: int | None, reason: str) -> str:
    if error_type == "network":
        return f"{channel} 网络不可达，请检查网络连接或代理设置"
    if error_type in ("server", "timeout"):
        return f"{channel} TMDB 服务繁忙或响应超时，请稍后重试"
    if error_type == "client" and http_status:
        return f"{channel} 请求失败（{http_status}），请检查 API 密钥是否正确"
    return f"{channel} 连接失败：{reason}"


def _test_tmdb_channel(channel: str, effective: dict[str, object]) -> dict[str, object]:
    timeout_ms = int(effective.get("tmdb.timeout_ms") or 15000)
    started_at = time.perf_counter()
    if channel == "v4":
        token = str(effective.get("tmdb.v4_token") or "").strip()
        if not token:
            return {"status": "skipped", "message": "未配置 V4 令牌", "response_ms": None}
        client = TmdbClient(
            v4_token=token,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=timeout_ms,
        )
        label = "V4"
    elif channel == "v3":
        api_key = str(effective.get("tmdb.api_key") or "").strip()
        if not api_key:
            return {"status": "skipped", "message": "未配置 V3 API 密钥", "response_ms": None}
        client = TmdbClient(
            api_key=api_key,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=timeout_ms,
        )
        label = "V3"
    else:
        raise ValueError(f"Unknown TMDB channel: {channel}")

    try:
        client.test_connection()
        return {
            "status": "success",
            "message": f"{label} 连接成功",
            "response_ms": round((time.perf_counter() - started_at) * 1000),
        }
    except RuntimeError as exc:
        response_ms = round((time.perf_counter() - started_at) * 1000)
        reason = str(exc)
        error_type, http_status = _classify_tmdb_error(reason)
        return {
            "status": "failed",
            "message": _tmdb_error_message(label, error_type, http_status, reason),
            "response_ms": response_ms,
            "error_type": error_type,
            "http_status": http_status,
            "raw_error": reason,
        }


def _page_test_result_from_row(row: sqlite3.Row) -> PageTestResult:
    return PageTestResult(
        page_key=str(row["page_key"]),
        config_snapshot=json.loads(str(row["config_snapshot"])),
        config_hash=str(row["config_hash"]),
        v4=json.loads(str(row["v4_result"])),
        v3=json.loads(str(row["v3_result"])),
        effective=str(row["effective_channel"]),
        tested_at=str(row["tested_at"]),
        updated_at=str(row["updated_at"]),
    )


def get_page_test_result(settings: AppSettings, page_key: str) -> PageTestResult | None:
    """Return the latest test result for a page."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT page_key, config_snapshot, config_hash, v4_result, v3_result, "
            "effective_channel, tested_at, updated_at "
            "FROM page_test_results WHERE page_key = ?",
            (page_key,),
        ).fetchone()
    return _page_test_result_from_row(row) if row else None


def delete_page_test_result(settings: AppSettings, page_key: str) -> None:
    """Delete the latest test result for a page."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("DELETE FROM page_test_results WHERE page_key = ?", (page_key,))
        connection.commit()


def _save_page_test_result(
    settings: AppSettings,
    page_key: str,
    snapshot: dict[str, object],
    result: dict[str, object],
) -> PageTestResult:
    now = _utc_now()
    v4 = result.get("v4") if isinstance(result.get("v4"), dict) else {}
    v3 = result.get("v3") if isinstance(result.get("v3"), dict) else {}
    effective = str(result.get("effective") or "none")
    config_hash = _snapshot_hash(snapshot)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute(
            "INSERT INTO page_test_results "
            "(page_key, config_snapshot, config_hash, v4_result, v3_result, "
            "effective_channel, tested_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(page_key) DO UPDATE SET "
            "config_snapshot = excluded.config_snapshot, "
            "config_hash = excluded.config_hash, "
            "v4_result = excluded.v4_result, "
            "v3_result = excluded.v3_result, "
            "effective_channel = excluded.effective_channel, "
            "tested_at = excluded.tested_at, "
            "updated_at = excluded.updated_at",
            (
                page_key,
                _stable_json(snapshot),
                config_hash,
                _stable_json(v4),
                _stable_json(v3),
                effective,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    saved = get_page_test_result(settings, page_key)
    if saved is None:
        raise RuntimeError("Failed to persist page test result.")
    return saved


def save_tmdb_connection_test_result(
    settings: AppSettings,
    v4_result: dict[str, object],
    v3_result: dict[str, object],
) -> dict[str, object]:
    effective = get_effective_settings(settings)
    snapshot = _tmdb_config_snapshot(effective)
    effective_channel = "none"
    if v4_result.get("status") == "success":
        effective_channel = "v4"
    elif v3_result.get("status") == "success":
        effective_channel = "v3"
    result: dict[str, object] = {
        "v4": v4_result,
        "v3": v3_result,
        "effective": effective_channel,
    }
    saved = _save_page_test_result(settings, TMDB_TEST_PAGE_KEY, snapshot, result)
    result["tested_at"] = saved.tested_at
    result["config_snapshot"] = saved.config_snapshot
    result["config_hash"] = saved.config_hash
    return result


def test_tmdb_channel(settings: AppSettings, channel: str) -> dict[str, object]:
    """Test one TMDB channel for frontend progress reporting."""

    return _test_tmdb_channel(channel, get_effective_settings(settings))


def page_test_result_to_dict(result: PageTestResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "page_key": result.page_key,
        "config_snapshot": result.config_snapshot,
        "config_hash": result.config_hash,
        "v4": result.v4,
        "v3": result.v3,
        "effective": result.effective,
        "tested_at": result.tested_at,
        "updated_at": result.updated_at,
    }


def get_imdb_test_result(settings: AppSettings) -> dict[str, object] | None:
    """Return the latest persisted IMDb test result."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, connection_status, response_time, error_message, "
            "config_snapshot, test_time, is_valid "
            "FROM imdb_test_result WHERE id = 1",
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "connection_status": str(row["connection_status"]),
        "response_time": row["response_time"],
        "error_message": row["error_message"],
        "config_snapshot": json.loads(str(row["config_snapshot"])),
        "test_time": str(row["test_time"]),
        "is_valid": bool(row["is_valid"]),
    }


def save_imdb_connection_test_result(
    settings: AppSettings,
    result: dict[str, object],
) -> dict[str, object]:
    """Persist the latest IMDb connection test result."""

    effective = get_effective_settings(settings)
    snapshot = _imdb_config_snapshot(effective)
    now = _utc_now()
    status = str(result.get("status") or result.get("connection_status") or "failed")
    if status not in {"success", "failed"}:
        status = "failed"
    response_time = result.get("response_ms", result.get("response_time"))
    error_message = None if status == "success" else str(result.get("message") or result.get("error_message") or "")

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute(
            "INSERT INTO imdb_test_result "
            "(id, connection_status, response_time, error_message, config_snapshot, test_time, is_valid) "
            "VALUES (1, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(id) DO UPDATE SET "
            "connection_status = excluded.connection_status, "
            "response_time = excluded.response_time, "
            "error_message = excluded.error_message, "
            "config_snapshot = excluded.config_snapshot, "
            "test_time = excluded.test_time, "
            "is_valid = 1",
            (
                status,
                int(response_time) if response_time is not None else None,
                error_message,
                _stable_json(snapshot),
                now,
            ),
        )
        connection.commit()

    saved = get_imdb_test_result(settings)
    if saved is None:
        raise RuntimeError("Failed to persist IMDb test result.")
    return saved


def imdb_test_result_to_dict(result: dict[str, object] | None) -> dict[str, object] | None:
    if result is None:
        return None
    return dict(result)


def test_imdb_connection(settings: AppSettings) -> dict[str, object]:
    """Test IMDb reachability for frontend progress reporting."""

    effective = get_effective_settings(settings)
    timeout_seconds = int(effective.get("imdb.timeout_ms") or 10000) / 1000
    started_at = time.perf_counter()
    try:
        req = url_request.Request(
            "https://www.imdb.com/",
            headers={"User-Agent": "MediaAI-Renamer/1.0"},
            method="GET",
        )
        with url_request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", 200)
            if status_code >= 400:
                raise RuntimeError(f"HTTP {status_code}")
        return {
            "status": "success",
            "message": "IMDb连接成功",
            "response_ms": round((time.perf_counter() - started_at) * 1000),
        }
    except (RuntimeError, OSError, URLError) as exc:
        return {
            "status": "failed",
            "message": "IMDb连接失败请检查网络或代理设置",
            "response_ms": round((time.perf_counter() - started_at) * 1000),
            "error_message": str(exc),
        }


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
    if parsed.startswith("["):
        try:
            template_items = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{definition.key} JSON format is invalid") from exc
        if not isinstance(template_items, list) or not template_items:
            raise ValueError(f"{definition.key} must include naming elements")
        for item in template_items:
            if not isinstance(item, dict) or not (item.get("variable") or item.get("key")):
                raise ValueError(f"{definition.key} naming element format is invalid")
        return parsed
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
        if any(key in TMDB_TEST_SNAPSHOT_KEYS for key in parsed_values):
            connection.execute(
                "DELETE FROM page_test_results WHERE page_key = ?",
                (TMDB_TEST_PAGE_KEY,),
            )
        if any(key in IMDB_TEST_SNAPSHOT_KEYS for key in parsed_values):
            connection.execute("UPDATE imdb_test_result SET is_valid = 0 WHERE id = 1")
        connection.commit()

    return list_setting_values(settings)


def test_tmdb_connection(settings: AppSettings) -> dict[str, object]:
    """Test V4 token and V3 API key separately, return per-channel results."""

    return save_tmdb_connection_test_result(
        settings,
        test_tmdb_channel(settings, "v4"),
        test_tmdb_channel(settings, "v3"),
    )
