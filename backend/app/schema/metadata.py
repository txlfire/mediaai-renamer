"""Metadata scraping and matching models."""

from dataclasses import dataclass, field
from typing import Any


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
    localized_title: str = ""
    chinese_title: str = ""
    english_title: str = ""
    release_date: str = ""
    vote_average: float | None = None
    poster_path: str = ""
    original_language: str = ""
    genres: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    tmdb_id: str = ""
    imdb_id: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


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
    metadata_source: str = ""
