"""Local media filename parser."""

from pathlib import Path
import re

from app.schema.media import ParsedMediaName

NOISE_TOKENS = {
    "720p",
    "1080p",
    "2160p",
    "web",
    "webdl",
    "web-dl",
    "dl",
    "bluray",
    "bdrip",
    "hdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "h.264",
    "h.265",
    "aac",
    "ddp",
    "flac",
    "truehd",
    "dts",
    "atmos",
}

TECHNICAL_GROUP_TOKENS = {
    "720p",
    "1080p",
    "2160p",
    "web",
    "webdl",
    "web-dl",
    "bluray",
    "bdrip",
    "hdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "h.264",
    "h.265",
    "aac",
    "ddp",
    "flac",
    "truehd",
    "dts",
    "atmos",
}

YEAR_PATTERN = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

EPISODE_PATTERNS = [
    re.compile(r"(?P<title>.*?)[ ._\-]*[sS](?P<season>\d{1,2})[eE](?P<episode>\d{1,3})"),
    re.compile(
        r"(?P<title>.*?)[ ._\-]*Season[ ._\-]*(?P<season>\d{1,2})"
        r"[ ._\-]*Episode[ ._\-]*(?P<episode>\d{1,3})",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<title>.*?)[ ._\-]*第(?P<season>\d{1,2})季[ ._\-]*第(?P<episode>\d{1,3})集"),
    re.compile(r"(?P<title>.*?)[ ._\-]*第(?P<episode>\d{1,3})集"),
    re.compile(r"(?P<title>.*?)[ ._\-]*[eE](?P<episode>\d{1,3})"),
]


def _clean_title(value: str) -> str:
    value = re.sub(r"[\[\(（【].*?[\]\)）】]", " ", value)
    value = re.sub(r"[._\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    tokens: list[str] = []
    normalized_noise = {item.replace(".", "").replace("-", "") for item in NOISE_TOKENS}
    for token in value.split(" "):
        normalized = token.lower().replace(".", "").replace("-", "")
        if normalized in normalized_noise:
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def _strip_technical_groups(value: str) -> str:
    """删除只描述清晰度、压制源、编码或音频格式的括号组。"""

    normalized_noise = {item.replace(".", "").replace("-", "") for item in TECHNICAL_GROUP_TOKENS}

    def replace_group(match: re.Match[str]) -> str:
        content = match.group(1)
        tokens = [item for item in re.split(r"[\s._\-]+", content) if item]
        if not tokens:
            return " "
        has_technical_token = False
        has_title_like_token = False
        for token in tokens:
            normalized = token.lower().replace(".", "").replace("-", "")
            if normalized in normalized_noise or re.fullmatch(r"\d{3,4}p?", normalized):
                has_technical_token = True
                continue
            if re.fullmatch(r"\d+bit", normalized) or re.fullmatch(r"\d+ch", normalized):
                has_technical_token = True
                continue
            has_title_like_token = True
        return " " if has_technical_token and not has_title_like_token else match.group(0)

    return re.sub(r"[\[\(（【]([^\]\)）】]+)[\]\)）】]", replace_group, value)


def parse_media_filename(file_name: str) -> ParsedMediaName:
    """Parse local filename into basic media naming fields."""

    stem = _strip_technical_groups(Path(file_name).stem)

    for pattern in EPISODE_PATTERNS:
        match = pattern.search(stem)
        if match is None:
            continue

        title = _clean_title(match.group("title"))
        if not title:
            return ParsedMediaName("unknown", "", None, None, None, "无法识别标题")

        season_text = match.groupdict().get("season")
        return ParsedMediaName(
            media_type="episode",
            title=title,
            year=None,
            season=int(season_text) if season_text else 1,
            episode=int(match.group("episode")),
        )

    year_match = YEAR_PATTERN.search(stem)
    if year_match is not None:
        title = _clean_title(stem[: year_match.start()])
        year = int(year_match.group(1))
        if not title:
            return ParsedMediaName("unknown", "", year, None, None, "无法识别标题")
        return ParsedMediaName("movie", title, year, None, None)

    title = _clean_title(stem)
    if not title:
        return ParsedMediaName("unknown", "", None, None, None, "无法识别标题")
    return ParsedMediaName("unknown", title, None, None, None, "缺少年份或季集信息")
