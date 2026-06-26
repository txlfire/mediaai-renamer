"""Metadata candidate scoring."""

from difflib import SequenceMatcher
import re

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchResult

MATCH_STATUS_HIGH = "high_confidence"
MATCH_STATUS_LOW = "low_confidence"
MATCH_STATUS_FAILED = "failed"


def _normalize_title(value: str) -> str:
    value = re.sub(r"[._\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _similarity(left: str, right: str) -> float:
    left_normalized = _normalize_title(left)
    right_normalized = _normalize_title(right)
    if not left_normalized or not right_normalized:
        return 0
    if left_normalized == right_normalized:
        return 1
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _title_score(local: ParsedMediaName, candidate: MetadataCandidate) -> int:
    best_similarity = max(
        _similarity(local.title, candidate.title),
        _similarity(local.title, candidate.original_title),
    )
    if best_similarity < 0.3:
        return 0
    return int(best_similarity * 60)


def _year_score(local: ParsedMediaName, candidate: MetadataCandidate) -> int:
    if local.year is None or candidate.year is None:
        return 0
    difference = abs(local.year - candidate.year)
    if difference == 0:
        return 20
    if difference == 1:
        return 15
    return 0


def _episode_score(local: ParsedMediaName, candidate: MetadataCandidate) -> int:
    if local.media_type != "episode":
        return 10
    return 10 if local.season == candidate.season and local.episode == candidate.episode else 0


def _status_for_score(score: int) -> str:
    if score >= 85:
        return MATCH_STATUS_HIGH
    if score >= 30:
        return MATCH_STATUS_LOW
    return MATCH_STATUS_FAILED


def score_metadata_candidate(
    local: ParsedMediaName,
    candidate: MetadataCandidate,
) -> MetadataMatchResult:
    """Calculate the global M4 weighted score for one metadata candidate."""

    if local.media_type != candidate.media_type:
        return MetadataMatchResult(candidate=candidate, score=0, status=MATCH_STATUS_FAILED)

    total = (
        _title_score(local, candidate)
        + _year_score(local, candidate)
        + 10
        + _episode_score(local, candidate)
    )
    score = max(0, min(100, total))
    return MetadataMatchResult(candidate=candidate, score=score, status=_status_for_score(score))
