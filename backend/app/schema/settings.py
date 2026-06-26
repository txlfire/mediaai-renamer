"""System hot settings data models."""

from dataclasses import dataclass


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
