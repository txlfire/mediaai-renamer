"""TVDB 元数据搜索客户端。"""

from __future__ import annotations

from typing import Any
from urllib import request as url_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import json
import time

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate


TVDB_API_BASE_URL = "https://api4.thetvdb.com/v4"


class TvdbClient:
    """TVDB Provider 客户端。"""

    provider = "tvdb"
    label = "TVDB"

    def __init__(
        self,
        base_url: str = TVDB_API_BASE_URL,
        api_key: str = "",
        timeout_seconds: int = 10,
        max_retries: int = 1,
        priority: int = 40,
    ):
        self.base_url = base_url.rstrip("/") or TVDB_API_BASE_URL
        self.api_key = api_key.strip()
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.priority = priority
        self._bearer_token = ""

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """搜索 TVDB 剧集候选。"""

        self._ensure_api_key()
        if parsed.media_type == "movie":
            return []
        search_payload = self._get_json(
            "/search",
            {
                "query": parsed.title,
                "type": "series",
                "limit": 10,
                "offset": 0,
            },
        )
        items = search_payload.get("data")
        if not isinstance(items, list):
            raise RuntimeError("TVDB 返回的搜索结果格式无效")
        candidates: list[MetadataCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = self._candidate_from_search_result(item, parsed)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def test_connection(self) -> bool:
        """通过固定非用户关键词验证 TVDB 接口可访问。"""

        self._ensure_api_key()
        self._get_json("/search", {"query": "TVDB", "type": "series", "limit": 1, "offset": 0})
        return True

    def _ensure_api_key(self) -> None:
        if not self.api_key:
            raise ValueError("TVDB API Key 未配置")

    def _login(self) -> str:
        if self._bearer_token:
            return self._bearer_token
        payload = self._request_json("POST", "/login", {}, {"apikey": self.api_key}, needs_auth=False)
        data = payload.get("data")
        token = str(data.get("token") if isinstance(data, dict) else "").strip()
        if not token:
            raise RuntimeError("TVDB 登录成功但未返回 Bearer Token")
        self._bearer_token = token
        return token

    def _get_json(self, path: str, query: dict[str, object]) -> dict[str, Any]:
        return self._request_json("GET", path, query, None, needs_auth=True)

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, object],
        payload: dict[str, object] | None,
        *,
        needs_auth: bool,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if needs_auth:
            headers["Authorization"] = f"Bearer {self._login()}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        for attempt in range(self.max_retries + 1):
            request = url_request.Request(url, data=body, headers=headers, method=method)
            try:
                with url_request.urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("TVDB 返回了无效的数据结构")
                return result
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise RuntimeError("TVDB 鉴权失败，请检查 API Key") from exc
                if exc.code == 429:
                    raise RuntimeError("TVDB 请求频率过高，请稍后重试") from exc
                if exc.code >= 500 and attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise RuntimeError(f"TVDB 请求失败：HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                reason = exc.reason if isinstance(exc, URLError) else str(exc)
                raise RuntimeError(f"TVDB 网络请求失败：{reason}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("TVDB 返回了无法解析的 JSON 数据") from exc

        raise RuntimeError("TVDB 请求失败")

    def _wait_before_retry(self, attempt: int) -> None:
        time.sleep(min(0.25 * (2**attempt), 2.0))

    def _candidate_from_search_result(
        self,
        item: dict[str, Any],
        parsed: ParsedMediaName,
    ) -> MetadataCandidate | None:
        series_id = str(item.get("tvdb_id") or item.get("id") or item.get("objectID") or "").strip()
        title = str(item.get("name_translated") or item.get("name") or item.get("title") or "").strip()
        if not series_id or not title:
            return None
        extended = self._series_extended(series_id)
        episode = self._episode_detail(series_id, parsed.season, parsed.episode)
        extended_title = str(extended.get("name") or "").strip()
        episode_title = str(episode.get("name") or "").strip()
        overview = str(episode.get("overview") or extended.get("overview") or item.get("overview") or "")
        year = _int_or_none(extended.get("year")) or _int_or_none(item.get("year"))
        genres = _string_list(extended.get("genres"), key="name") or _string_list(item.get("genres"))
        release_date = str(episode.get("aired") or extended.get("firstAired") or item.get("first_air_time") or "")
        raw_data = {
            "search_result": item,
            "series_extended": extended,
            "episode": episode,
            "series_title": extended_title or title,
            "episode_title": episode_title,
            "match_basis": ["剧集标题", "季号", "集号"] if episode else ["剧集标题"],
            "match_reason": (
                "TVDB 剧集候选，依据官方 series 搜索结果；"
                "已补充 season、episode、episode title。"
                if episode
                else "TVDB 剧集候选，依据官方 series 搜索结果。"
            ),
        }
        return MetadataCandidate(
            provider="TVDB",
            provider_id=series_id,
            media_type="episode",
            title=extended_title or title,
            original_title=title,
            year=year,
            season=_int_or_none(episode.get("seasonNumber")) or parsed.season,
            episode=_int_or_none(episode.get("number")) or parsed.episode,
            overview=overview,
            localized_title=extended_title or title,
            release_date=release_date,
            poster_path=str(episode.get("image") or extended.get("image") or item.get("image_url") or item.get("poster") or ""),
            original_language=str(extended.get("originalLanguage") or item.get("primary_language") or ""),
            genres=genres,
            raw_data=raw_data,
        )

    def _series_extended(self, series_id: str) -> dict[str, Any]:
        payload = self._get_json(f"/series/{series_id}/extended", {"short": "true"})
        data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        return data

    def _episode_detail(self, series_id: str, season: int | None, episode: int | None) -> dict[str, Any]:
        if season is None or episode is None:
            return {}
        payload = self._get_json(
            f"/series/{series_id}/episodes/default",
            {"page": 0, "season": season, "episodeNumber": episode},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            return {}
        for item in episodes:
            if (
                isinstance(item, dict)
                and _int_or_none(item.get("seasonNumber")) == season
                and _int_or_none(item.get("number")) == episode
            ):
                return item
        return {}


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def _string_list(value: Any, *, key: str | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if key and isinstance(item, dict):
            text = str(item.get(key) or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            result.append(text)
    return list(dict.fromkeys(result))
