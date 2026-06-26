"""Metadata search orchestration service."""

from typing import Protocol

from app.core.config import AppSettings
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchSummary
from app.service.metadata_matcher import MATCH_STATUS_FAILED, score_metadata_candidate
from app.service.settings_service import get_effective_settings
from app.service.tmdb_client import TmdbClient


class MetadataProvider(Protocol):
    """Search provider interface used by metadata matching service."""

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """Search metadata candidates for one parsed media name."""


def _failed(message: str) -> MetadataMatchSummary:
    return MetadataMatchSummary(status=MATCH_STATUS_FAILED, message=message, matches=[])


def match_metadata_candidates(
    settings: AppSettings,
    parsed: ParsedMediaName,
    provider: MetadataProvider | None = None,
) -> MetadataMatchSummary:
    """Search candidates and return them sorted by weighted match score."""

    effective = get_effective_settings(settings)
    if not effective.get("tmdb.enabled"):
        return _failed("TMDB 未启用")

    api_key = str(effective.get("tmdb.api_key") or "").strip()
    if not api_key:
        return _failed("未配置 TMDB API Key")

    active_provider = provider or TmdbClient(
        api_key=api_key,
        language=str(effective.get("tmdb.language") or "zh-CN"),
        region=str(effective.get("tmdb.region") or "CN"),
        timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
    )

    try:
        candidates = active_provider.search(parsed)
    except Exception as exc:  # pragma: no cover - defensive degradation
        return _failed(f"TMDB 搜索失败: {exc}")

    matches = sorted(
        (score_metadata_candidate(parsed, candidate) for candidate in candidates),
        key=lambda item: item.score,
        reverse=True,
    )
    if not matches:
        return _failed("未找到匹配的 TMDB 候选")

    return MetadataMatchSummary(status=matches[0].status, message=None, matches=matches)
