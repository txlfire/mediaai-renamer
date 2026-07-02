"""AI structured parsing models."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AiParseCandidate:
    title: str
    media_type: str
    year: int | None
    season: int | None
    episode: int | None
    confidence: int
    reason: str
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AiParseResult:
    status: str
    message: str
    candidates: list[AiParseCandidate]
    response_ms: int | None = None
    usage: dict[str, object] = field(default_factory=dict)
