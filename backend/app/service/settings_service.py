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

from app.core.config import AppSettings
from app.schema.settings import PageTestResult, SettingValue
from app.service.ai_provider import AiProvider, AiProviderConfig, DeepSeekProvider, OpenAiCompatibleProvider
from app.service.tmdb_client import TmdbClient


TMDB_TEST_PAGE_KEY = "settings.tmdb"
AI_TEST_PAGE_KEY = "settings.ai"
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
AI_TEST_SNAPSHOT_KEYS = (
    "ai.enabled",
    "ai.active_profile_id",
    "ai.provider_profiles",
    "ai.provider",
    "ai.model",
    "ai.api_key",
    "ai.base_url",
    "ai.timeout_ms",
    "ai.max_retries",
)
INTERNAL_SETTING_KEYS = {
    "naming.movie_template_version",
    "naming.movie_template_updated_at",
    "naming.episode_template_version",
    "naming.episode_template_updated_at",
}
SUPPORTED_AI_PROVIDERS = ("deepseek", "openai_compatible", "custom")
DEFAULT_MEDIA_RISK_SENSITIVE_WORDS = [
    "情色",
    "色情",
    "成人",
    "成人向",
    "三级片",
    "限制级",
    "裸露",
    "性爱",
    "激情",
    "AV",
    "av",
    "FBI WARNING",
    "fbi warning",
    "FBIWARNING",
    "ABP-",
    "IPX-",
    "SSNI-",
    "MIDE-",
    "STARS-",
    "SONE-",
    "HMN-",
    "MEYD-",
    "JUQ-",
    "DASS-",
    "ADN-",
    "ATID-",
    "SHKD-",
    "RBD-",
    "NTR-",
    "MIRD-",
    "PPPD-",
    "EBOD-",
    "MKV-",
    "FC2-",
    "HEYZO-",
    "Carib-",
    "Tokyo-Hot-",
    "n0760-",
    "1pondo-",
    "暴力",
    "血腥",
    "强奸",
    "性侵",
    "虐杀",
    "屠杀",
    "凶杀",
    "谋杀",
    "肢解",
    "酷刑",
    "枪战",
    "毒品",
    "吸毒",
    "自杀",
]


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
    "ai.enabled": SettingDefinition(
        key="ai.enabled",
        category="ai",
        default=False,
        value_type="bool",
        description="Enable AI intelligent parsing",
        env_var="AI_ENABLED",
    ),
    "ai.active_profile_id": SettingDefinition(
        key="ai.active_profile_id",
        category="ai",
        default="default",
        value_type="required_string",
        description="Active AI provider profile id",
    ),
    "ai.provider_profiles": SettingDefinition(
        key="ai.provider_profiles",
        category="ai",
        default=[],
        value_type="ai_profiles",
        description="Saved AI provider profiles",
    ),
    "ai.provider": SettingDefinition(
        key="ai.provider",
        category="ai",
        default="deepseek",
        value_type="string",
        description="AI provider",
        env_var="AI_PROVIDER",
        allowed_values=SUPPORTED_AI_PROVIDERS,
    ),
    "ai.model": SettingDefinition(
        key="ai.model",
        category="ai",
        default="deepseek-chat",
        value_type="required_string",
        description="AI model name",
        env_var="AI_MODEL",
    ),
    "ai.api_key": SettingDefinition(
        key="ai.api_key",
        category="ai",
        default="",
        value_type="secret",
        description="AI provider API key",
        sensitive=True,
        env_var="AI_API_KEY",
    ),
    "ai.base_url": SettingDefinition(
        key="ai.base_url",
        category="ai",
        default="https://api.deepseek.com",
        value_type="url",
        description="AI provider base URL",
        env_var="AI_BASE_URL",
    ),
    "ai.timeout_ms": SettingDefinition(
        key="ai.timeout_ms",
        category="ai",
        default=30000,
        value_type="int",
        description="AI request timeout in milliseconds",
        env_var="AI_TIMEOUT_MS",
        min_value=5000,
        max_value=120000,
    ),
    "ai.max_retries": SettingDefinition(
        key="ai.max_retries",
        category="ai",
        default=2,
        value_type="int",
        description="AI request max retry count",
        env_var="AI_MAX_RETRIES",
        min_value=0,
        max_value=10,
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
    "naming.movie_template_version": SettingDefinition(
        key="naming.movie_template_version",
        category="naming",
        default=1,
        value_type="int",
        description="Movie naming template version",
        min_value=1,
        max_value=1000000,
    ),
    "naming.movie_template_updated_at": SettingDefinition(
        key="naming.movie_template_updated_at",
        category="naming",
        default="",
        value_type="string",
        description="Movie naming template updated time",
    ),
    "naming.episode_template": SettingDefinition(
        key="naming.episode_template",
        category="naming",
        default="{title}.{year}.S{season:02d}E{episode:02d}",
        value_type="template",
        description="Episode naming template",
    ),
    "naming.episode_template_version": SettingDefinition(
        key="naming.episode_template_version",
        category="naming",
        default=1,
        value_type="int",
        description="Episode naming template version",
        min_value=1,
        max_value=1000000,
    ),
    "naming.episode_template_updated_at": SettingDefinition(
        key="naming.episode_template_updated_at",
        category="naming",
        default="",
        value_type="string",
        description="Episode naming template updated time",
    ),
    "naming.title_recognition_mode": SettingDefinition(
        key="naming.title_recognition_mode",
        category="naming",
        default="parent_folder_fallback",
        value_type="string",
        description="Title recognition mode for rename preview generation",
        allowed_values=("file_name_first", "parent_folder_fallback", "parent_folder_first", "manual_only"),
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
    "privacy.custom_sensitive_words": SettingDefinition(
        key="privacy.custom_sensitive_words",
        category="privacy",
        default=[],
        value_type="json_list",
        description="Custom sensitive words for external submission protection",
    ),
    "privacy.default_sensitive_words_enabled": SettingDefinition(
        key="privacy.default_sensitive_words_enabled",
        category="privacy",
        default=True,
        value_type="bool",
        description="Enable default media risk sensitive words",
    ),
    "privacy.default_sensitive_words": SettingDefinition(
        key="privacy.default_sensitive_words",
        category="privacy",
        default=DEFAULT_MEDIA_RISK_SENSITIVE_WORDS,
        value_type="json_list",
        description="Default media risk sensitive words",
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
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    return hashlib.sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()


def _secret_fingerprint(value: object) -> dict[str, object]:
    secret = str(value or "").strip()
    if not secret:
        return {"configured": False, "fingerprint": ""}
    return {
        "configured": True,
        "fingerprint": hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16],
    }


def _mask_ai_profile(profile: dict[str, object]) -> dict[str, object]:
    masked = dict(profile)
    secret = str(profile.get("api_key") or "").strip()
    masked["api_key"] = _mask_secret(secret)
    masked["has_secret"] = bool(secret)
    return masked


def _active_ai_profile(effective: dict[str, object]) -> dict[str, object] | None:
    profiles = effective.get("ai.provider_profiles")
    if not isinstance(profiles, list) or not profiles:
        return None
    active_profile_id = str(effective.get("ai.active_profile_id") or "").strip()
    for profile in profiles:
        if isinstance(profile, dict) and str(profile.get("id") or "").strip() == active_profile_id:
            return profile
    for profile in profiles:
        if isinstance(profile, dict) and bool(profile.get("enabled", True)):
            return profile
    for profile in profiles:
        if isinstance(profile, dict):
            return profile
    return None


def _tmdb_config_snapshot(effective: dict[str, object]) -> dict[str, object]:
    return {key: effective.get(key) for key in TMDB_TEST_SNAPSHOT_KEYS}


def _imdb_config_snapshot(effective: dict[str, object]) -> dict[str, object]:
    return {key: effective.get(key) for key in IMDB_TEST_SNAPSHOT_KEYS}


def _ai_config_snapshot(effective: dict[str, object]) -> dict[str, object]:
    snapshot = {key: effective.get(key) for key in AI_TEST_SNAPSHOT_KEYS}
    snapshot["ai.api_key"] = _secret_fingerprint(effective.get("ai.api_key"))
    profiles = effective.get("ai.provider_profiles")
    if isinstance(profiles, list):
        snapshot["ai.provider_profiles"] = [
            {
                **{key: value for key, value in profile.items() if key != "api_key"},
                "api_key": _secret_fingerprint(profile.get("api_key")),
            }
            for profile in profiles
            if isinstance(profile, dict)
        ]
    return snapshot


def build_tmdb_config_snapshot(settings: AppSettings) -> dict[str, object]:
    """Return the effective TMDB settings snapshot used by connection tests."""

    return _tmdb_config_snapshot(get_effective_settings(settings))


def build_imdb_config_snapshot(settings: AppSettings) -> dict[str, object]:
    """Return the effective IMDb settings snapshot used by connection tests."""

    return _imdb_config_snapshot(get_effective_settings(settings))


def build_ai_config_snapshot(settings: AppSettings) -> dict[str, object]:
    """Return the effective AI settings snapshot used by connection tests."""

    return _ai_config_snapshot(get_effective_settings(settings))


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


def ai_test_result_to_dict(result: PageTestResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    payload = dict(result.v4)
    payload.update(
        {
            "page_key": result.page_key,
            "config_snapshot": result.config_snapshot,
            "config_hash": result.config_hash,
            "effective": result.effective,
            "tested_at": result.tested_at,
            "updated_at": result.updated_at,
        }
    )
    return payload


def save_ai_connection_test_result(
    settings: AppSettings,
    result: dict[str, object],
) -> dict[str, object]:
    effective = get_effective_settings(settings)
    snapshot = _ai_config_snapshot(effective)
    status = str(result.get("status") or "failed")
    normalized = {
        "status": status if status in {"success", "failed"} else "failed",
        "message": str(result.get("message") or ""),
        "response_ms": result.get("response_ms"),
        "error_type": result.get("error_type"),
        "http_status": result.get("http_status"),
        "raw_error": result.get("raw_error"),
        "provider": str(effective.get("ai.provider") or "deepseek"),
        "model": str(effective.get("ai.model") or "deepseek-chat"),
        "base_url": str(effective.get("ai.base_url") or ""),
        "active_profile_id": str(effective.get("ai.active_profile_id") or ""),
        "active_profile_name": str(effective.get("ai.active_profile_name") or ""),
    }
    saved = _save_page_test_result(
        settings,
        AI_TEST_PAGE_KEY,
        snapshot,
        {
            "v4": normalized,
            "v3": {},
            "effective": "ai" if normalized["status"] == "success" else "none",
        },
    )
    converted = ai_test_result_to_dict(saved)
    if converted is None:
        raise RuntimeError("Failed to persist AI test result.")
    return converted


def test_ai_connection(settings: AppSettings, provider: AiProvider | None = None) -> dict[str, object]:
    """Test the configured AI provider with a minimal chat completion request."""

    effective = get_effective_settings(settings)
    provider_name = str(effective.get("ai.provider") or "deepseek")
    model = str(effective.get("ai.model") or "deepseek-chat").strip()
    api_key = str(effective.get("ai.api_key") or "").strip()
    base_url = str(effective.get("ai.base_url") or "").strip()

    if provider_name not in SUPPORTED_AI_PROVIDERS:
        return save_ai_connection_test_result(
            settings,
            {
                "status": "failed",
                "message": f"暂不支持的 AI 服务商：{provider_name}",
                "response_ms": 0,
                "error_type": "client",
            },
        )
    if not api_key:
        return save_ai_connection_test_result(
            settings,
            {
                "status": "failed",
                "message": "未配置 AI API Key",
                "response_ms": 0,
                "error_type": "client",
            },
        )

    config = AiProviderConfig(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_ms=int(effective.get("ai.timeout_ms") or 30000),
        max_retries=int(effective.get("ai.max_retries") or 0),
    )
    ai_provider = provider or (DeepSeekProvider(config) if provider_name == "deepseek" else OpenAiCompatibleProvider(config))
    return save_ai_connection_test_result(settings, ai_provider.test_connection())


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


def _parse_required_string(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip()
    if not parsed:
        raise ValueError(f"{definition.key} 不能为空")
    return parsed


def _parse_url(value: object, definition: SettingDefinition) -> str:
    parsed = str(value).strip().rstrip("/")
    if not parsed:
        raise ValueError(f"{definition.key} 不能为空")
    if not re.match(r"^https?://[^\s]+$", parsed):
        raise ValueError(f"{definition.key} 必须为 http 或 https 地址")
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


def _split_sensitive_words_text(value: str) -> list[str]:
    normalized = re.sub(r"\r\n|\r|\n", "||", value)
    return [word.strip() for word in normalized.split("||") if word.strip()]


def _parse_json_list(value: object, definition: SettingDefinition) -> list[str]:
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            parsed = []
        elif stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{definition.key} 必须为 JSON 数组") from exc
        else:
            parsed = _split_sensitive_words_text(stripped)
    else:
        raise ValueError(f"{definition.key} 必须为 JSON 数组")

    if not isinstance(parsed, list):
        raise ValueError(f"{definition.key} 必须为 JSON 数组")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            raise ValueError(f"{definition.key} 仅支持文本列表")
        word = item.strip()
        if not word:
            raise ValueError(f"{definition.key} 不允许空敏感词")
        lowered = word.casefold()
        if lowered not in seen:
            normalized.append(word)
            seen.add(lowered)
    return normalized


def _parse_ai_profiles(
    value: object,
    definition: SettingDefinition,
    existing_profiles: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raw_profiles = []
        else:
            try:
                raw_profiles = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{definition.key} 必须为 JSON 数组") from exc
    else:
        raw_profiles = value

    if not isinstance(raw_profiles, list):
        raise ValueError(f"{definition.key} 必须为 JSON 数组")

    timeout_definition = SettingDefinition(
        key=f"{definition.key}.timeout_ms",
        category="ai",
        default=30000,
        value_type="int",
        description="AI profile timeout",
        min_value=5000,
        max_value=120000,
    )
    retry_definition = SettingDefinition(
        key=f"{definition.key}.max_retries",
        category="ai",
        default=2,
        value_type="int",
        description="AI profile max retries",
        min_value=0,
        max_value=10,
    )
    normalized_profiles: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    existing_by_id = {
        str(profile.get("id") or ""): profile
        for profile in (existing_profiles or [])
        if isinstance(profile, dict)
    }
    for item in raw_profiles:
        if not isinstance(item, dict):
            raise ValueError(f"{definition.key} 仅支持对象数组")
        profile_id = _parse_required_string(item.get("id", ""), SettingDefinition(
            key=f"{definition.key}.id",
            category="ai",
            default="",
            value_type="required_string",
            description="AI profile id",
        ))
        if profile_id in seen_ids:
            raise ValueError(f"{definition.key} 存在重复 profile id")
        provider = _parse_string(
            item.get("provider", ""),
            SettingDefinition(
                key=f"{definition.key}.provider",
                category="ai",
                default="deepseek",
                value_type="string",
                description="AI provider",
                allowed_values=SUPPORTED_AI_PROVIDERS,
            ),
        )
        model = _parse_required_string(item.get("model", ""), SettingDefinition(
            key=f"{definition.key}.model",
            category="ai",
            default="",
            value_type="required_string",
            description="AI profile model",
        ))
        existing_secret = str(existing_by_id.get(profile_id, {}).get("api_key") or "")
        raw_secret = item.get("api_key", "")
        secret_text = str(raw_secret or "").strip()
        if not secret_text or secret_text.startswith("********"):
            secret_text = existing_secret
        normalized_profiles.append(
            {
                "id": profile_id,
                "name": str(item.get("name") or profile_id).strip() or profile_id,
                "provider": provider,
                "model": model,
                "api_key": _parse_secret(secret_text),
                "base_url": _parse_url(item.get("base_url", ""), SettingDefinition(
                    key=f"{definition.key}.base_url",
                    category="ai",
                    default="",
                    value_type="url",
                    description="AI profile base URL",
                )),
                "timeout_ms": _parse_int(item.get("timeout_ms", 30000), timeout_definition),
                "max_retries": _parse_int(item.get("max_retries", 2), retry_definition),
                "enabled": _parse_bool(item.get("enabled", True)),
            }
        )
        seen_ids.add(profile_id)
    return normalized_profiles


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
    if definition.value_type == "json_list":
        return _parse_json_list(value, definition)
    if definition.value_type == "ai_profiles":
        return _parse_ai_profiles(value, definition)
    if definition.value_type == "required_string":
        return _parse_required_string(value, definition)
    if definition.value_type == "url":
        return _parse_url(value, definition)
    return _parse_string(value, definition)


def validate_setting_value(
    key: str,
    value: object,
    current_effective: dict[str, object] | None = None,
) -> object:
    """Validate one setting value without persisting it."""

    definition = SETTING_DEFINITIONS.get(key)
    if definition is None:
        raise ValueError(f"未知配置项 {key}")
    if key == "ai.provider_profiles":
        existing_profiles = None
        if current_effective is not None and isinstance(current_effective.get("ai.provider_profiles"), list):
            existing_profiles = current_effective.get("ai.provider_profiles")
        return _parse_ai_profiles(value, definition, existing_profiles=existing_profiles)
    return _parse_value(value, definition)


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
    active_profile = _active_ai_profile(effective)
    if active_profile is not None:
        effective["ai.active_profile_name"] = active_profile.get("name")
        effective["ai.provider"] = active_profile.get("provider", effective.get("ai.provider"))
        effective["ai.model"] = active_profile.get("model", effective.get("ai.model"))
        effective["ai.api_key"] = active_profile.get("api_key", effective.get("ai.api_key"))
        effective["ai.base_url"] = active_profile.get("base_url", effective.get("ai.base_url"))
        effective["ai.timeout_ms"] = active_profile.get("timeout_ms", effective.get("ai.timeout_ms"))
        effective["ai.max_retries"] = active_profile.get("max_retries", effective.get("ai.max_retries"))
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
                value=(
                    [_mask_ai_profile(profile) for profile in value if isinstance(profile, dict)]
                    if definition.key == "ai.provider_profiles" and isinstance(value, list)
                    else (_mask_secret(value) if definition.sensitive else value)
                ),
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

    current_effective = get_effective_settings(settings)
    parsed_values: dict[str, tuple[SettingDefinition, object]] = {}
    for key, value in values.items():
        if key in INTERNAL_SETTING_KEYS:
            raise ValueError(f"{key} 为系统维护字段，不允许直接修改")
        definition = SETTING_DEFINITIONS.get(key)
        if definition is None:
            raise ValueError(f"未知配置项 {key}")
        if key == "ai.provider_profiles":
            parsed_value = _parse_ai_profiles(
                value,
                definition,
                existing_profiles=current_effective.get("ai.provider_profiles") if isinstance(current_effective.get("ai.provider_profiles"), list) else None,
            )
        else:
            parsed_value = _parse_value(value, definition)
        parsed_values[key] = (definition, parsed_value)

    now = _utc_now()
    _apply_naming_template_metadata_updates(current_effective, parsed_values, now)
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
        if any(key in AI_TEST_SNAPSHOT_KEYS for key in parsed_values):
            connection.execute(
                "DELETE FROM page_test_results WHERE page_key = ?",
                (AI_TEST_PAGE_KEY,),
            )
        if any(key in IMDB_TEST_SNAPSHOT_KEYS for key in parsed_values):
            connection.execute("UPDATE imdb_test_result SET is_valid = 0 WHERE id = 1")
        connection.commit()

    return list_setting_values(settings)


def _apply_naming_template_metadata_updates(
    current_effective: dict[str, object],
    parsed_values: dict[str, tuple[SettingDefinition, object]],
    now: str,
) -> None:
    template_pairs = (
        (
            "naming.movie_template",
            "naming.movie_template_version",
            "naming.movie_template_updated_at",
        ),
        (
            "naming.episode_template",
            "naming.episode_template_version",
            "naming.episode_template_updated_at",
        ),
    )
    for template_key, version_key, updated_at_key in template_pairs:
        if template_key not in parsed_values:
            continue
        current_template = str(current_effective.get(template_key) or "")
        current_version = int(current_effective.get(version_key) or 1)
        current_updated_at = str(current_effective.get(updated_at_key) or "")
        next_template = str(parsed_values[template_key][1] or "")
        if next_template == current_template:
            next_version = current_version
            next_updated_at = current_updated_at
        else:
            next_version = current_version + 1
            next_updated_at = now
        parsed_values[version_key] = (SETTING_DEFINITIONS[version_key], next_version)
        parsed_values[updated_at_key] = (SETTING_DEFINITIONS[updated_at_key], next_updated_at)


def test_tmdb_connection(settings: AppSettings) -> dict[str, object]:
    """Test V4 token and V3 API key separately, return per-channel results."""

    return save_tmdb_connection_test_result(
        settings,
        test_tmdb_channel(settings, "v4"),
        test_tmdb_channel(settings, "v3"),
    )
