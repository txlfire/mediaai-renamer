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
            candidates.append(self._candidate_from_item(item, parsed.media_type))
        return candidates

    def _candidate_from_item(self, item: dict[str, Any], media_type: str) -> MetadataCandidate:
        title = str(item.get("title") or item.get("name") or "")
        original_title = str(item.get("original_title") or item.get("original_name") or "")
        date_value = str(item.get("release_date") or item.get("first_air_date") or "")
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
        )


def _extract_year(value: str) -> int | None:
    if len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None
