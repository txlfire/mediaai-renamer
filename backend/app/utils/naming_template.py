"""Standard target filename builder."""

import re

from app.schema.media import ParsedMediaName

ILLEGAL_FILENAME_CHARS = r'[<>:"/\\|?*]'


def _normalize_title(value: str) -> str:
    value = re.sub(ILLEGAL_FILENAME_CHARS, " ", value)
    value = re.sub(r"[._\-\s]+", ".", value)
    return value.strip(".")


def build_preview_name(parsed: ParsedMediaName, extension: str) -> str:
    """Build the M2 standard preview filename."""

    normalized_extension = extension.lower()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"

    title = _normalize_title(parsed.title)
    if not title:
        return normalized_extension.lstrip(".")

    if parsed.media_type == "episode" and parsed.season and parsed.episode:
        return f"{title}.S{parsed.season:02d}E{parsed.episode:02d}{normalized_extension}"
    if parsed.media_type == "movie" and parsed.year:
        return f"{title}.{parsed.year}{normalized_extension}"
    return f"{title}{normalized_extension}"
