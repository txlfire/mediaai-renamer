"""Metadata scraping and matching models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataCandidate:
    """One metadata candidate returned by a scraper provider."""

    provider: str
    provider_id: str
    media_type: str
    title: str
    original_title: str
    year: int | None
    season: int | None
    episode: int | None
    overview: str


@dataclass(frozen=True)
class MetadataMatchResult:
    """Candidate with calculated match score and status."""

    candidate: MetadataCandidate
    score: int
    status: str


@dataclass(frozen=True)
class MetadataMatchSummary:
    """Metadata search and match summary for one parsed media item."""

    status: str
    message: str | None
    matches: list[MetadataMatchResult]
