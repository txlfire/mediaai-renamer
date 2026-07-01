"""TMDB metadata search client."""

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate


TMDB_API_BASE_URL = "https://api.themoviedb.org/3"


class TmdbClient:
    """Small synchronous TMDB client with a mock-friendly search interface.

    Supports V4 (Bearer token) and V3 (API key) authentication.
    When v4_token is provided, it takes priority over api_key.
    """

    def __init__(
        self,
        api_key: str = "",
        v4_token: str = "",
        language: str = "zh-CN",
        region: str = "CN",
        timeout_ms: int = 10000,
    ):
        self.api_key = api_key
        self.v4_token = v4_token
        self.language = language
        self.region = region
        self.timeout_seconds = max(1, timeout_ms / 1000)

    def _get_json(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        base_params: dict[str, object] = {
            "language": self.language,
            "region": self.region,
        }
        base_params.update(
            {k: v for k, v in params.items() if v is not None and v != ""}
        )
        headers = {
            "Accept": "application/json",
            "User-Agent": "MediaAI-Renamer/0.3",
        }

        if self.v4_token:
            headers["Authorization"] = f"Bearer {self.v4_token}"
            query = urlencode(base_params)
        else:
            base_params["api_key"] = self.api_key
            query = urlencode(base_params)

        request = Request(
            f"{TMDB_API_BASE_URL}{path}?{query}",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    def test_connection(self) -> bool:
        """Validate API key and network access with a lightweight TMDB endpoint."""

        self._get_json("/configuration", {})
        return True

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """Search TMDB candidates for movie or episode-style parsed names."""

        media_type = "tv" if parsed.media_type == "episode" else "movie"
        path = "/search/tv" if media_type == "tv" else "/search/movie"
        payload = self._get_json(
            path,
            {
                "query": parsed.title,
                "year": parsed.year if media_type == "movie" else None,
                "first_air_date_year": parsed.year if media_type == "tv" else None,
                "include_adult": "false",
            },
        )
        candidates: list[MetadataCandidate] = []
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            detail = self._detail_for_item(item, media_type)
            candidates.append(self._candidate_from_item({**item, **detail}, parsed.media_type))
        return candidates

    def _detail_for_item(self, item: dict[str, Any], media_type: str) -> dict[str, Any]:
        provider_id = item.get("id")
        if not provider_id:
            return {}
        detail_path = f"/tv/{provider_id}" if media_type == "tv" else f"/movie/{provider_id}"
        try:
            return self._get_json(
                detail_path,
                {"append_to_response": "credits,external_ids,translations"},
            )
        except Exception:
            return {}

    def _candidate_from_item(self, item: dict[str, Any], media_type: str) -> MetadataCandidate:
        title = str(item.get("title") or item.get("name") or "")
        original_title = str(item.get("original_title") or item.get("original_name") or "")
        date_value = str(item.get("release_date") or item.get("first_air_date") or "")
        credits = item.get("credits") if isinstance(item.get("credits"), dict) else {}
        cast = [
            str(person.get("name"))
            for person in credits.get("cast", [])[:8]
            if isinstance(person, dict) and person.get("name")
        ]
        directors = [
            str(person.get("name"))
            for person in credits.get("crew", [])
            if isinstance(person, dict) and person.get("job") in {"Director", "Series Director"} and person.get("name")
        ]
        external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), dict) else {}
        genres = [
            str(genre.get("name"))
            for genre in item.get("genres", [])
            if isinstance(genre, dict) and genre.get("name")
        ]
        return MetadataCandidate(
            provider="TMDB",
            provider_id=str(item.get("id") or ""),
            media_type=media_type,
            title=title,
            original_title=original_title,
            year=_extract_year(date_value),
            season=None,
            episode=None,
            overview=str(item.get("overview") or ""),
            localized_title=title,
            chinese_title=title if _looks_chinese(title) else "",
            english_title=original_title,
            release_date=date_value,
            vote_average=_float_or_none(item.get("vote_average")),
            poster_path=str(item.get("poster_path") or ""),
            original_language=str(item.get("original_language") or ""),
            genres=genres,
            cast=cast,
            directors=list(dict.fromkeys(directors)),
            tmdb_id=str(item.get("id") or ""),
            imdb_id=str(external_ids.get("imdb_id") or ""),
            raw_data=item,
        )


def _extract_year(value: str) -> int | None:
    if len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
