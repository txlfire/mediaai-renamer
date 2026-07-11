"""M10 元数据源 Provider 注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import AppSettings
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.bangumi_client import BangumiClient
from app.service.metadata_provider_service import (
    MetadataProviderConfig,
    PROVIDER_BANGUMI,
    PROVIDER_DOUBAN_PROXY,
    PROVIDER_IMDB,
    PROVIDER_TMDB,
    PROVIDER_TVDB,
    get_metadata_provider_api_key,
    list_metadata_provider_configs,
)
from app.service.settings_service import get_effective_settings
from app.service.tmdb_client import TmdbClient
from app.service.tvdb_client import TvdbClient


class RegisteredMetadataProvider(Protocol):
    """统一元数据源查询接口。"""

    provider: str
    label: str
    priority: int

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """查询候选元数据。"""


@dataclass(frozen=True)
class ProviderRegistryItem:
    """注册表条目。"""

    provider: str
    label: str
    priority: int
    enabled: bool
    real_search_available: bool
    searcher: RegisteredMetadataProvider | None
    disabled_reason: str = ""


class TmdbMetadataProvider:
    """基于现有 TMDB 配置的 Provider 适配器。"""

    provider = PROVIDER_TMDB
    label = "TMDB"

    def __init__(self, settings: AppSettings, priority: int):
        self.settings = settings
        self.priority = priority

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        effective = get_effective_settings(self.settings)
        v4_token = str(effective.get("tmdb.v4_token") or "").strip()
        api_key = str(effective.get("tmdb.api_key") or "").strip()
        language = str(effective.get("tmdb.language") or "zh-CN")
        region = str(effective.get("tmdb.region") or "CN")
        timeout_ms = int(effective.get("tmdb.timeout_ms") or 10000)

        if v4_token:
            try:
                return TmdbClient(
                    v4_token=v4_token,
                    language=language,
                    region=region,
                    timeout_ms=timeout_ms,
                ).search(parsed)
            except Exception:
                if not api_key:
                    raise

        if api_key:
            return TmdbClient(
                api_key=api_key,
                language=language,
                region=region,
                timeout_ms=timeout_ms,
            ).search(parsed)

        return []


class PlaceholderMetadataProvider:
    """尚未实现真实请求的 Provider 占位适配器。"""

    def __init__(self, provider: str, label: str, priority: int):
        self.provider = provider
        self.label = label
        self.priority = priority

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        return []


PROVIDER_LABELS = {
    PROVIDER_TMDB: "TMDB",
    PROVIDER_IMDB: "IMDb",
    PROVIDER_BANGUMI: "Bangumi",
    PROVIDER_TVDB: "TVDB",
    PROVIDER_DOUBAN_PROXY: "豆瓣代理",
}


def _config_by_provider(settings: AppSettings) -> dict[str, MetadataProviderConfig]:
    return {item.provider: item for item in list_metadata_provider_configs(settings)}


def build_metadata_provider_registry(settings: AppSettings) -> list[ProviderRegistryItem]:
    """按配置构建启用状态和优先级确定的 Provider 注册表。"""

    configs = _config_by_provider(settings)
    effective = get_effective_settings(settings)
    tmdbConfig = configs.get(PROVIDER_TMDB)
    tmdbPriority = tmdbConfig.priority if tmdbConfig else 1
    tmdbEnabled = bool(effective.get("tmdb.enabled"))
    hasTmdbSecret = bool(str(effective.get("tmdb.v4_token") or "").strip() or str(effective.get("tmdb.api_key") or "").strip())

    items: list[ProviderRegistryItem] = [
        ProviderRegistryItem(
            provider=PROVIDER_TMDB,
            label=PROVIDER_LABELS[PROVIDER_TMDB],
            priority=tmdbPriority,
            enabled=tmdbEnabled and hasTmdbSecret,
            real_search_available=True,
            searcher=TmdbMetadataProvider(settings, tmdbPriority) if tmdbEnabled and hasTmdbSecret else None,
            disabled_reason="" if tmdbEnabled and hasTmdbSecret else "TMDB 未启用或未配置密钥",
        )
    ]

    for provider in (PROVIDER_IMDB, PROVIDER_BANGUMI, PROVIDER_TVDB, PROVIDER_DOUBAN_PROXY):
        config = configs.get(provider)
        if config is None:
            continue
        enabled = bool(config.enabled)
        if provider == PROVIDER_BANGUMI and enabled:
            searcher: RegisteredMetadataProvider | None = BangumiClient(
                base_url=config.base_url,
                access_token=get_metadata_provider_api_key(settings, provider),
                timeout_seconds=config.timeout_seconds,
                max_retries=config.max_retries,
                priority=config.priority,
                app_version=settings.version,
            )
            realSearchAvailable = True
        elif provider == PROVIDER_TVDB and enabled:
            searcher = TvdbClient(
                base_url=config.base_url,
                api_key=get_metadata_provider_api_key(settings, provider),
                timeout_seconds=config.timeout_seconds,
                max_retries=config.max_retries,
                priority=config.priority,
            )
            realSearchAvailable = True
        else:
            searcher = (
                PlaceholderMetadataProvider(provider, PROVIDER_LABELS[provider], config.priority)
                if enabled
                else None
            )
            realSearchAvailable = False
        items.append(
            ProviderRegistryItem(
                provider=provider,
                label=PROVIDER_LABELS[provider],
                priority=config.priority,
                enabled=enabled,
                real_search_available=realSearchAvailable,
                searcher=searcher,
                disabled_reason="" if enabled else "元数据源未启用",
            )
        )

    return sorted(items, key=lambda item: (item.priority, item.provider))
