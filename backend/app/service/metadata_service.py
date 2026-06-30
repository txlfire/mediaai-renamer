"""Metadata search orchestration service."""

from typing import Protocol

from app.core.logger import get_logger
from app.core.config import AppSettings
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchSummary
from app.service.metadata_matcher import MATCH_STATUS_FAILED, score_metadata_candidate
from app.service.settings_service import get_effective_settings
from app.service.tmdb_client import TmdbClient

logger = get_logger(__name__)


class MetadataProvider(Protocol):
    """Search provider interface used by metadata matching service."""

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """Search metadata candidates for one parsed media name."""


def _failed(message: str) -> MetadataMatchSummary:
    return MetadataMatchSummary(status=MATCH_STATUS_FAILED, message=message, matches=[])


def _build_summary(
    parsed: ParsedMediaName,
    candidates: list[MetadataCandidate],
    source_label: str,
) -> MetadataMatchSummary:
    matches = sorted(
        (score_metadata_candidate(parsed, candidate) for candidate in candidates),
        key=lambda item: item.score,
        reverse=True,
    )
    if not matches:
        return MetadataMatchSummary(
            status=MATCH_STATUS_FAILED,
            message="未找到匹配的 TMDB 候选",
            matches=[],
            metadata_source=source_label,
        )
    return MetadataMatchSummary(
        status=matches[0].status,
        message=None,
        matches=matches,
        metadata_source=source_label,
    )


def _build_supplemented_summary(
    settings: dict[str, object],
    parsed: ParsedMediaName,
    tmdb_candidates: list[MetadataCandidate],
    source_label: str,
    imdb_provider: MetadataProvider | None,
) -> MetadataMatchSummary:
    if not settings.get("imdb.enabled") or imdb_provider is None:
        return _build_summary(parsed, tmdb_candidates, source_label)

    try:
        imdb_candidates = imdb_provider.search(parsed)
    except Exception as exc:
        logger.warning("IMDb 补充刮削失败，继续使用 TMDB 结果: %s", exc)
        return _build_summary(parsed, tmdb_candidates, source_label)

    if settings.get("imdb.priority") == "imdb_first":
        candidates = [*imdb_candidates, *tmdb_candidates]
    else:
        candidates = [*tmdb_candidates, *imdb_candidates]
    return _build_summary(parsed, candidates, "TMDB + IMDb")


def match_metadata_candidates(
    settings: AppSettings,
    parsed: ParsedMediaName,
    provider: MetadataProvider | None = None,
    imdb_provider: MetadataProvider | None = None,
) -> MetadataMatchSummary:
    """Search candidates and return them sorted by weighted match score."""

    effective = get_effective_settings(settings)
    if not effective.get("tmdb.enabled"):
        return _failed("TMDB 未启用")

    v4_token = str(effective.get("tmdb.v4_token") or "").strip()
    api_key = str(effective.get("tmdb.api_key") or "").strip()

    if not v4_token and not api_key:
        return _failed("未配置 TMDB V4 令牌且无 V3 API 密钥")

    if provider is not None:
        try:
            candidates = provider.search(parsed)
        except Exception as exc:
            return _failed(f"TMDB 搜索失败: {exc}")
        return _build_supplemented_summary(effective, parsed, candidates, "custom", imdb_provider)

    candidates = []

    if v4_token:
        v4_client = TmdbClient(
            v4_token=v4_token,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
        )
        try:
            candidates = v4_client.search(parsed)
            return _build_supplemented_summary(effective, parsed, candidates, "TMDB (V4)", imdb_provider)
        except Exception as exc:
            logger.warning("V4 请求失败，准备降级至 V3: %s", exc)

    if api_key:
        v3_client = TmdbClient(
            api_key=api_key,
            language=str(effective.get("tmdb.language") or "zh-CN"),
            region=str(effective.get("tmdb.region") or "CN"),
            timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000),
        )
        try:
            candidates = v3_client.search(parsed)
            channel = "TMDB (V3)" if v4_token else "TMDB (V3-only)"
            return _build_supplemented_summary(effective, parsed, candidates, channel, imdb_provider)
        except Exception as exc:
            logger.error("V3 搜索也失败: %s", exc)
            return _failed(f"TMDB 搜索失败: {exc}")

    return _failed("未配置 TMDB V4 令牌且无 V3 API 密钥")
