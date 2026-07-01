"""Standard target filename builder."""

import json
import re

from app.schema.media import ParsedMediaName

ILLEGAL_FILENAME_CHARS = r'[<>:"/\\|?*]'
TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)(?::[^}]*)?\}")


def _normalize_title(value: str) -> str:
    value = re.sub(ILLEGAL_FILENAME_CHARS, " ", value)
    value = re.sub(r"[._\-\s]+", ".", value)
    return value.strip(".")


def build_preview_name(parsed: ParsedMediaName, extension: str) -> str:
    """Build the M2 standard preview filename."""
    return build_preview_name_with_settings(parsed, extension, {})


def build_preview_name_with_settings(
    parsed: ParsedMediaName,
    extension: str,
    settings: dict[str, object],
) -> str:
    """Build a target filename from the latest effective naming settings."""

    normalized_extension = extension.lower()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"

    title = _normalize_title(parsed.title)
    if not title:
        return normalized_extension.lstrip(".")

    template_key = "naming.episode_template" if parsed.media_type == "episode" else "naming.movie_template"
    template = str(settings.get(template_key) or "")
    separator = str(settings.get("naming.separator") or ".")
    keep_year = bool(settings.get("naming.keep_year", True))
    if template.startswith("["):
        return _build_from_builder_template(parsed, normalized_extension, template, separator, keep_year)

    if parsed.media_type == "episode" and parsed.season and parsed.episode:
        fallback = f"{title}.S{parsed.season:02d}E{parsed.episode:02d}"
    elif parsed.media_type == "movie" and parsed.year and keep_year:
        fallback = f"{title}.{parsed.year}"
    else:
        fallback = title
    return f"{fallback}{normalized_extension}"


def _build_from_builder_template(
    parsed: ParsedMediaName,
    extension: str,
    template: str,
    separator: str,
    keep_year: bool,
) -> str:
    try:
        items = json.loads(template)
    except json.JSONDecodeError:
        return build_preview_name(parsed, extension)
    if not isinstance(items, list):
        return build_preview_name(parsed, extension)

    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        variable = str(item.get("variable") or item.get("key") or "")
        value = _value_for_variable(parsed, variable, keep_year)
        if value:
            parts.append(_normalize_title(value))
    base = separator.join(part for part in parts if part)
    return f"{base or _normalize_title(parsed.title)}{extension}"


def template_variables_for_media_type(media_type: str, settings: dict[str, object]) -> set[str]:
    """Return variables used by the active naming builder template."""

    template_key = "naming.episode_template" if media_type == "episode" else "naming.movie_template"
    template = str(settings.get(template_key) or "")
    if template.startswith("["):
        try:
            items = json.loads(template)
        except json.JSONDecodeError:
            return set()
        if not isinstance(items, list):
            return set()
        variables: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            variable = str(item.get("variable") or item.get("key") or "").split(":")[0]
            if variable:
                variables.add(variable)
        return variables

    return {match.group(1).split(":")[0] for match in TEMPLATE_VARIABLE_PATTERN.finditer(template)}


def _value_for_variable(parsed: ParsedMediaName, variable: str, keep_year: bool) -> str:
    if variable in {"title", "original_title"}:
        return parsed.title
    if variable == "year" and parsed.year and keep_year:
        return str(parsed.year)
    if variable == "season" and parsed.season is not None:
        return f"S{parsed.season:02d}"
    if variable == "episode" and parsed.episode is not None:
        return f"E{parsed.episode:02d}"
    if variable in {"season_episode", "seasonEpisode"} and parsed.season is not None and parsed.episode is not None:
        return f"S{parsed.season:02d}E{parsed.episode:02d}"
    return ""
