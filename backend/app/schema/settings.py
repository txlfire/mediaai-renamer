"""System hot settings data models."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingValue:
    """One system setting value for API and UI display."""

    key: str
    category: str
    value: object
    description: str
    sensitive: bool
    source: str
    updated_at: str | None


@dataclass(frozen=True)
class PageTestResult:
    """Last persisted page-level test result."""

    page_key: str
    config_snapshot: dict[str, Any]
    config_hash: str
    v4: dict[str, Any]
    v3: dict[str, Any]
    effective: str
    tested_at: str
    updated_at: str
