"""M10 多源元数据匹配编排服务。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from app.core.config import AppSettings
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchResult, MetadataMatchSummary
from app.service.metadata_matcher import MATCH_STATUS_FAILED, MATCH_STATUS_HIGH, score_metadata_candidate
from app.service.metadata_provider_registry import ProviderRegistryItem, build_metadata_provider_registry

MULTI_MATCH_MODE_PRIMARY_ONLY = "primary_only"
MULTI_MATCH_MODE_FALLBACK = "fallback"
MULTI_MATCH_MODE_PARALLEL = "parallel"
MULTI_MATCH_MODES = {
    MULTI_MATCH_MODE_PRIMARY_ONLY,
    MULTI_MATCH_MODE_FALLBACK,
    MULTI_MATCH_MODE_PARALLEL,
}


@dataclass(frozen=True)
class ProviderSearchResult:
    """单个 Provider 的执行结果。"""

    provider: str
    label: str
    status: str
    message: str
    candidate_count: int


@dataclass(frozen=True)
class MultiSourceMatchResult:
    """多源匹配结果。"""

    summary: MetadataMatchSummary
    provider_results: list[ProviderSearchResult]


def _normalize_text(value: str | None) -> str:
    """生成跨源去重用的标题片段。"""

    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower())


def _candidate_merge_key(candidate: MetadataCandidate) -> str:
    """生成多源候选去重键。"""

    external_id = candidate.tmdb_id or candidate.imdb_id
    if external_id:
        return f"{candidate.media_type}:external:{external_id}"
    if candidate.provider and candidate.provider_id:
        provider_key = f"{candidate.provider.lower()}:{candidate.provider_id}"
    else:
        provider_key = ""
    title_key = _normalize_text(candidate.chinese_title or candidate.localized_title or candidate.title or candidate.original_title)
    parts = [
        candidate.media_type,
        title_key,
        str(candidate.year or ""),
        str(candidate.season or ""),
        str(candidate.episode or ""),
    ]
    if not title_key and provider_key:
        return f"{candidate.media_type}:provider:{provider_key}"
    return ":".join(parts)


def _field_completeness(candidate: MetadataCandidate) -> int:
    """计算候选字段完整度，供多源排序和前端展示。"""

    scalar_fields = [
        candidate.title,
        candidate.original_title,
        candidate.localized_title,
        candidate.chinese_title,
        candidate.english_title,
        candidate.year,
        candidate.season,
        candidate.episode,
        candidate.overview,
        candidate.release_date,
        candidate.vote_average,
        candidate.poster_path,
        candidate.original_language,
        candidate.tmdb_id,
        candidate.imdb_id,
    ]
    list_fields = [candidate.genres, candidate.cast, candidate.directors]
    return sum(1 for value in scalar_fields if value not in (None, "", [])) + sum(1 for value in list_fields if value)


def _field_sources(candidate: MetadataCandidate) -> dict[str, str]:
    provider = candidate.provider or "unknown"
    fields: dict[str, str] = {}
    for field_name in (
        "title",
        "original_title",
        "localized_title",
        "chinese_title",
        "english_title",
        "year",
        "season",
        "episode",
        "overview",
        "release_date",
        "vote_average",
        "poster_path",
        "original_language",
        "genres",
        "cast",
        "directors",
        "tmdb_id",
        "imdb_id",
    ):
        value = getattr(candidate, field_name)
        if value not in (None, "", []):
            fields[field_name] = provider
    return fields


def _enrich_candidate(candidate: MetadataCandidate, item: ProviderRegistryItem) -> MetadataCandidate:
    mergeKey = _candidate_merge_key(candidate)
    completeness = _field_completeness(candidate)
    rawData = dict(candidate.raw_data or {})
    rawData.update(
        {
            "source_provider": item.provider,
            "source_label": item.label,
            "source_priority": item.priority,
            "field_completeness": completeness,
            "merge_key": mergeKey,
            "field_sources": _field_sources(candidate),
        }
    )
    return replace(candidate, raw_data=rawData)


def _dedupe_candidates(candidates: list[MetadataCandidate]) -> list[MetadataCandidate]:
    """按 merge_key 去重，同分时保留字段更完整、优先级更高的候选。"""

    bestByKey: dict[str, MetadataCandidate] = {}
    for candidate in candidates:
        mergeKey = str(candidate.raw_data.get("merge_key") or _candidate_merge_key(candidate))
        current = bestByKey.get(mergeKey)
        if current is None:
            bestByKey[mergeKey] = candidate
            continue
        candidateCompleteness = int(candidate.raw_data.get("field_completeness") or 0)
        currentCompleteness = int(current.raw_data.get("field_completeness") or 0)
        candidatePriority = int(candidate.raw_data.get("source_priority") or 1000)
        currentPriority = int(current.raw_data.get("source_priority") or 1000)
        if (candidateCompleteness, -candidatePriority) > (currentCompleteness, -currentPriority):
            bestByKey[mergeKey] = candidate
    return list(bestByKey.values())


def _summary_from_candidates(
    parsed: ParsedMediaName,
    candidates: list[MetadataCandidate],
    source_label: str,
) -> MetadataMatchSummary:
    candidates = _dedupe_candidates(candidates)
    matches = sorted(
        (score_metadata_candidate(parsed, candidate) for candidate in candidates),
        key=lambda item: (
            item.score,
            int(item.candidate.raw_data.get("field_completeness") or 0),
            -int(item.candidate.raw_data.get("source_priority") or 1000),
        ),
        reverse=True,
    )
    if not matches:
        return MetadataMatchSummary(
            status=MATCH_STATUS_FAILED,
            message="多源匹配未找到候选",
            matches=[],
            metadata_source=source_label,
        )
    return MetadataMatchSummary(
        status=matches[0].status,
        message=None,
        matches=matches,
        metadata_source=source_label,
    )


def _enabled_items(registry: list[ProviderRegistryItem]) -> list[ProviderRegistryItem]:
    return [item for item in registry if item.enabled and item.searcher is not None]


def _select_items(registry: list[ProviderRegistryItem], mode: str) -> list[ProviderRegistryItem]:
    enabledItems = _enabled_items(registry)
    if mode == MULTI_MATCH_MODE_PRIMARY_ONLY:
        return enabledItems[:1]
    return enabledItems


def match_multi_source_metadata_candidates(
    settings: AppSettings,
    parsed: ParsedMediaName,
    mode: str = MULTI_MATCH_MODE_FALLBACK,
    registry: list[ProviderRegistryItem] | None = None,
) -> MultiSourceMatchResult:
    """按多源策略查询、隔离失败并统一评分候选。"""

    if mode not in MULTI_MATCH_MODES:
        raise ValueError("不支持的多源匹配模式")

    registryItems = registry if registry is not None else build_metadata_provider_registry(settings)
    selectedItems = _select_items(registryItems, mode)
    providerResults: list[ProviderSearchResult] = []
    candidates: list[MetadataCandidate] = []
    sourceLabels: list[str] = []

    if not selectedItems:
        summary = MetadataMatchSummary(
            status=MATCH_STATUS_FAILED,
            message="没有可用的元数据源",
            matches=[],
            metadata_source="",
        )
        return MultiSourceMatchResult(summary=summary, provider_results=[])

    for item in selectedItems:
        if item.searcher is None:
            providerResults.append(
                ProviderSearchResult(item.provider, item.label, "skipped", item.disabled_reason or "元数据源不可用", 0)
            )
            continue
        if not item.real_search_available:
            providerResults.append(
                ProviderSearchResult(item.provider, item.label, "skipped", "Provider 真实搜索尚未接入", 0)
            )
            continue

        try:
            providerCandidates = [_enrich_candidate(candidate, item) for candidate in item.searcher.search(parsed)]
        except Exception as exc:
            providerResults.append(
                ProviderSearchResult(item.provider, item.label, "failed", f"{item.label} 搜索失败: {exc}", 0)
            )
            continue

        candidates.extend(providerCandidates)
        sourceLabels.append(item.label)
        providerResults.append(
            ProviderSearchResult(item.provider, item.label, "success", "搜索完成", len(providerCandidates))
        )

        if mode == MULTI_MATCH_MODE_FALLBACK:
            summary = _summary_from_candidates(parsed, candidates, " + ".join(sourceLabels) if sourceLabels else item.label)
            if summary.matches and summary.matches[0].status == MATCH_STATUS_HIGH:
                return MultiSourceMatchResult(summary=summary, provider_results=providerResults)

    sourceLabel = " + ".join(sourceLabels) if sourceLabels else "多源匹配"
    summary = _summary_from_candidates(parsed, candidates, sourceLabel)
    return MultiSourceMatchResult(summary=summary, provider_results=providerResults)


def provider_search_results_to_dict(results: list[ProviderSearchResult]) -> list[dict[str, object]]:
    """转换 Provider 执行结果为 API 输出。"""

    return [
        {
            "provider": item.provider,
            "label": item.label,
            "status": item.status,
            "message": item.message,
            "candidate_count": item.candidate_count,
        }
        for item in results
    ]
