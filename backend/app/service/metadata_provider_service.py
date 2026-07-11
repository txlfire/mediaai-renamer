"""多站点元数据源配置服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from time import perf_counter
from urllib.parse import urlparse

from app.core.config import AppSettings
from app.service.bangumi_client import BANGUMI_API_BASE_URL, BangumiClient
from app.service.media_source_secret import decrypt_secret, encrypt_secret, has_secret
from app.service.tvdb_client import TVDB_API_BASE_URL, TvdbClient

PROVIDER_TMDB = "tmdb"
PROVIDER_IMDB = "imdb"
PROVIDER_BANGUMI = "bangumi"
PROVIDER_TVDB = "tvdb"
PROVIDER_DOUBAN_PROXY = "douban_proxy"
SUPPORTED_METADATA_PROVIDERS = {
    PROVIDER_TMDB,
    PROVIDER_IMDB,
    PROVIDER_BANGUMI,
    PROVIDER_TVDB,
    PROVIDER_DOUBAN_PROXY,
}


@dataclass(frozen=True)
class MetadataProviderConfig:
    """元数据源配置。"""

    id: int
    provider: str
    enabled: bool
    priority: int
    base_url: str
    has_api_key: bool
    timeout_seconds: int
    max_retries: int
    created_at: str
    updated_at: str


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_config(row: sqlite3.Row) -> MetadataProviderConfig:
    return MetadataProviderConfig(
        id=int(row["id"]),
        provider=str(row["provider"]),
        enabled=bool(row["enabled"]),
        priority=int(row["priority"]),
        base_url=str(row["base_url"] or ""),
        has_api_key=has_secret(row["api_key_encrypted"]),
        timeout_seconds=int(row["timeout_seconds"]),
        max_retries=int(row["max_retries"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def metadata_provider_config_to_dict(config: MetadataProviderConfig) -> dict[str, object]:
    return {
        "id": config.id,
        "provider": config.provider,
        "enabled": config.enabled,
        "priority": config.priority,
        "base_url": config.base_url,
        "has_api_key": config.has_api_key,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def _validate_provider(provider: str) -> str:
    value = provider.strip()
    if value not in SUPPORTED_METADATA_PROVIDERS:
        raise ValueError("不支持的元数据源")
    return value


def _validate_base_url(provider: str, base_url: object) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if provider == PROVIDER_DOUBAN_PROXY and not value:
        return ""
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("元数据源 Base URL 必须是有效的 http 或 https 地址")
    if provider == PROVIDER_BANGUMI:
        isOfficialUrl = (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "api.bgm.tv"
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        if not isOfficialUrl:
            raise ValueError(f"Bangumi Base URL 仅支持官方地址 {BANGUMI_API_BASE_URL}")
        return BANGUMI_API_BASE_URL
    if provider == PROVIDER_TVDB:
        isOfficialUrl = (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "api4.thetvdb.com"
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
            and parsed.path.rstrip("/") == "/v4"
            and not parsed.query
            and not parsed.fragment
        )
        if not isOfficialUrl:
            raise ValueError(f"TVDB Base URL 仅支持官方地址 {TVDB_API_BASE_URL}")
        return TVDB_API_BASE_URL
    return value


def _validate_int(value: object, field_name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是数字") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name}必须在 {minimum}-{maximum} 之间")
    return parsed


def list_metadata_provider_configs(settings: AppSettings) -> list[MetadataProviderConfig]:
    """返回元数据源配置列表，敏感值只返回是否已配置。"""

    with closing(_connect(settings)) as connection:
        rows = connection.execute(
            "SELECT * FROM metadata_provider_configs ORDER BY priority ASC, provider ASC"
        ).fetchall()
    return [_row_to_config(row) for row in rows]


def get_metadata_provider_config(settings: AppSettings, provider: str) -> MetadataProviderConfig:
    """读取单个元数据源配置。"""

    provider = _validate_provider(provider)
    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT * FROM metadata_provider_configs WHERE provider = ?",
            (provider,),
        ).fetchone()
    if row is None:
        raise ValueError("元数据源配置不存在")
    return _row_to_config(row)


def get_metadata_provider_api_key(settings: AppSettings, provider: str) -> str:
    """仅供后端 Provider 使用，读取并解密元数据源密钥。"""

    provider = _validate_provider(provider)
    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT api_key_encrypted FROM metadata_provider_configs WHERE provider = ?",
            (provider,),
        ).fetchone()
    if row is None:
        raise ValueError("元数据源配置不存在")
    return decrypt_secret(settings, row["api_key_encrypted"]) or ""


def update_metadata_provider_config(
    settings: AppSettings,
    provider: str,
    payload: dict[str, object],
) -> MetadataProviderConfig:
    """保存元数据源配置；空 API Key 表示保留原密钥。"""

    provider = _validate_provider(provider)
    current = get_metadata_provider_config(settings, provider)
    enabled = bool(payload.get("enabled", current.enabled))
    priority = _validate_int(payload.get("priority", current.priority), "优先级", 0, 1000)
    base_url = _validate_base_url(provider, payload.get("base_url", current.base_url))
    timeout_seconds = _validate_int(payload.get("timeout_seconds", current.timeout_seconds), "超时时间", 3, 60)
    max_retries = _validate_int(payload.get("max_retries", current.max_retries), "最大重试次数", 0, 5)
    api_key = str(payload.get("api_key") or "").strip()
    clearApiKey = bool(payload.get("clear_api_key", False))
    if clearApiKey:
        encrypted_api_key = ""
    elif api_key and not api_key.startswith("********"):
        encrypted_api_key = encrypt_secret(settings, api_key)
    else:
        encrypted_api_key = None
    now = _utc_now_text()
    with closing(_connect(settings)) as connection:
        connection.execute(
            "INSERT INTO metadata_provider_configs "
            "(provider, enabled, priority, base_url, api_key_encrypted, timeout_seconds, "
            "max_retries, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET "
            "enabled = excluded.enabled, "
            "priority = excluded.priority, "
            "base_url = excluded.base_url, "
            "api_key_encrypted = CASE "
            "WHEN excluded.api_key_encrypted IS NULL THEN metadata_provider_configs.api_key_encrypted "
            "ELSE excluded.api_key_encrypted END, "
            "timeout_seconds = excluded.timeout_seconds, "
            "max_retries = excluded.max_retries, "
            "updated_at = excluded.updated_at",
            (
                provider,
                1 if enabled else 0,
                priority,
                base_url,
                encrypted_api_key,
                timeout_seconds,
                max_retries,
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM metadata_provider_configs WHERE provider = ?",
            (provider,),
        ).fetchone()
    return _row_to_config(row)


def test_metadata_provider_config(settings: AppSettings, provider: str) -> dict[str, object]:
    """执行 Provider 配置测试；已接入的 Provider 同时验证真实连接。"""

    config = get_metadata_provider_config(settings, provider)
    if config.provider == PROVIDER_BANGUMI:
        startedAt = perf_counter()
        client = BangumiClient(
            base_url=config.base_url,
            access_token=get_metadata_provider_api_key(settings, provider),
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            priority=config.priority,
            app_version=settings.version,
        )
        try:
            client.test_connection()
            status = "success"
            message = "Bangumi 连接成功"
        except Exception as exc:
            status = "failed"
            message = str(exc) or "Bangumi 连接失败，请检查网络或 Access Token"
        return {
            "provider": config.provider,
            "status": status,
            "message": message,
            "response_ms": int((perf_counter() - startedAt) * 1000),
            "checked_at": _utc_now_text(),
        }
    if config.provider == PROVIDER_TVDB:
        startedAt = perf_counter()
        client = TvdbClient(
            base_url=config.base_url,
            api_key=get_metadata_provider_api_key(settings, provider),
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            priority=config.priority,
        )
        try:
            client.test_connection()
            status = "success"
            message = "TVDB 连接成功"
        except Exception as exc:
            status = "failed"
            message = str(exc) or "TVDB 连接失败，请检查网络或 API Key"
        return {
            "provider": config.provider,
            "status": status,
            "message": message,
            "response_ms": int((perf_counter() - startedAt) * 1000),
            "checked_at": _utc_now_text(),
        }
    if config.provider == PROVIDER_DOUBAN_PROXY and not config.base_url:
        return {
            "provider": config.provider,
            "status": "failed",
            "message": "豆瓣代理未配置 Base URL，未发起连接测试",
            "response_ms": None,
            "checked_at": _utc_now_text(),
        }
    if config.base_url:
        _validate_base_url(config.provider, config.base_url)
    return {
        "provider": config.provider,
        "status": "success",
        "message": "元数据源配置校验通过；真实连接将在对应 Provider 接入后执行",
        "response_ms": 0,
        "checked_at": _utc_now_text(),
    }
