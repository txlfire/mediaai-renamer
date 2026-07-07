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


@dataclass(frozen=True)
class NamingTemplateMetadata:
    """One naming template metadata snapshot."""

    media_type: str
    template_key: str
    template_value: str
    template_version: int
    template_updated_at: str


@dataclass(frozen=True)
class NamingTemplatePreviewResult:
    """Generated filename preview for a naming template test."""

    media_type: str
    generated_name: str
    template_version: int
    template_updated_at: str


@dataclass(frozen=True)
class NamingTemplateDiffResult:
    """Diff result between current and candidate naming templates."""

    media_type: str
    current_generated_name: str
    candidate_generated_name: str
    changed: bool
    template_version: int
    template_updated_at: str


@dataclass(frozen=True)
class NamingTemplateBundle:
    """Naming template import/export bundle."""

    schema_version: int
    movie_template: str
    episode_template: str
    separator: str
    keep_year: bool
