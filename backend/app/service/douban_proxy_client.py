"""豆瓣代理元数据搜索客户端。"""

from __future__ import annotations

from typing import Any
from urllib import request as url_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import json
import time

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate


class DoubanProxyClient:
    """通过用户自建代理查询豆瓣候选。"""

    provider = "douban_proxy"
    label = "豆瓣代理"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout_seconds: int = 10,
        max_retries: int = 1,
        priority: int = 50,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.priority = priority

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """按统一代理协议搜索豆瓣候选。"""

        self._ensure_base_url()
        query: dict[str, object] = {
            "query": parsed.title,
            "media_type": parsed.media_type,
            "limit": 10,
        }
        if parsed.year is not None:
            query["year"] = parsed.year
        if parsed.season is not None:
            query["season"] = parsed.season
        if parsed.episode is not None:
            query["episode"] = parsed.episode

        payload = self._get_json("/search", query)
        items = _candidate_items(payload)
        candidates: list[MetadataCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = self._candidate_from_item(item, parsed)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def test_connection(self) -> bool:
        """使用固定非用户关键词验证代理可访问。"""

        self._ensure_base_url()
        self._get_json("/search", {"query": "douban", "media_type": "movie", "limit": 1})
        return True

    def _ensure_base_url(self) -> None:
        if not self.base_url:
            raise ValueError("豆瓣代理 Base URL 未配置")

    def _get_json(self, path: str, query: dict[str, object]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(self.max_retries + 1):
            request = url_request.Request(url, headers=headers, method="GET")
            try:
                with url_request.urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("豆瓣代理返回了无效的数据结构")
                return result
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise RuntimeError("豆瓣代理鉴权失败，请检查 API Key") from exc
                if exc.code == 429:
                    raise RuntimeError("豆瓣代理请求频率过高，请稍后重试") from exc
                if exc.code >= 500 and attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise RuntimeError(f"豆瓣代理请求失败：HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                reason = exc.reason if isinstance(exc, URLError) else str(exc)
                raise RuntimeError(f"豆瓣代理网络请求失败：{reason}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("豆瓣代理返回了无法解析的 JSON 数据") from exc

        raise RuntimeError("豆瓣代理请求失败")

    def _wait_before_retry(self, attempt: int) -> None:
        time.sleep(min(0.25 * (2**attempt), 2.0))

    def _candidate_from_item(
        self,
        item: dict[str, Any],
        parsed: ParsedMediaName,
    ) -> MetadataCandidate | None:
        provider_id = str(item.get("id") or item.get("douban_id") or item.get("subject_id") or "").strip()
        title = str(
            item.get("title")
            or item.get("chinese_title")
            or item.get("localized_title")
            or item.get("name")
            or ""
        ).strip()
        original_title = str(item.get("original_title") or item.get("originalTitle") or "").strip()
        if not provider_id or not title:
            return None
        media_type = str(item.get("media_type") or item.get("type") or parsed.media_type).strip()
        if media_type == "tv":
            media_type = "episode"
        raw_data = dict(item)
        raw_data.update(
            {
                "match_basis": _match_basis(item, parsed),
                "match_reason": "豆瓣代理候选，依据用户配置代理返回的结构化数据。",
            }
        )
        return MetadataCandidate(
            provider="豆瓣代理",
            provider_id=provider_id,
            media_type=media_type or parsed.media_type,
            title=title,
            original_title=original_title,
            year=_int_or_none(item.get("year")) or _extract_year(str(item.get("release_date") or item.get("date") or "")),
            season=_int_or_none(item.get("season")) or parsed.season,
            episode=_int_or_none(item.get("episode")) or parsed.episode,
            overview=str(item.get("overview") or item.get("summary") or item.get("description") or ""),
            localized_title=str(item.get("localized_title") or title),
            chinese_title=str(item.get("chinese_title") or title),
            english_title=str(item.get("english_title") or item.get("englishTitle") or ""),
            release_date=str(item.get("release_date") or item.get("date") or ""),
            vote_average=_float_or_none(item.get("vote_average") or item.get("rating") or item.get("score")),
            poster_path=str(item.get("poster_path") or item.get("poster") or item.get("image") or ""),
            original_language=str(item.get("original_language") or item.get("language") or ""),
            genres=_string_list(item.get("genres")),
            cast=_string_list(item.get("cast")),
            directors=_string_list(item.get("directors")),
            tmdb_id=str(item.get("tmdb_id") or ""),
            imdb_id=str(item.get("imdb_id") or item.get("imdb") or ""),
            raw_data=raw_data,
        )


def _candidate_items(payload: dict[str, Any]) -> list[Any]:
    for key in ("items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise RuntimeError("豆瓣代理返回的候选列表格式无效")


def _match_basis(item: dict[str, Any], parsed: ParsedMediaName) -> list[str]:
    values = ["标题"]
    if item.get("year") or parsed.year is not None:
        values.append("年份")
    if item.get("season") or parsed.season is not None:
        values.append("季号")
    if item.get("episode") or parsed.episode is not None:
        values.append("集号")
    return values


def _extract_year(value: str) -> int | None:
    if len(value) < 4:
        return None
    return _int_or_none(value[:4])


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("name") or item.get("title") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            result.append(text)
    return list(dict.fromkeys(result))
