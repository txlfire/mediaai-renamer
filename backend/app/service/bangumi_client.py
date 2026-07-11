"""Bangumi 元数据搜索客户端。"""

from typing import Any
from urllib import request as url_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import json
import time

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate


BANGUMI_API_BASE_URL = "https://api.bgm.tv"
BANGUMI_PROJECT_URL = "https://github.com/txlfire/mediaai-renamer"


class BangumiClient:
    """Bangumi Provider 客户端。"""

    provider = "bangumi"
    label = "Bangumi"

    def __init__(
        self,
        base_url: str = BANGUMI_API_BASE_URL,
        access_token: str = "",
        timeout_seconds: int = 10,
        max_retries: int = 1,
        priority: int = 30,
        app_version: str = "0.10.3",
    ):
        self.base_url = base_url.rstrip("/") or BANGUMI_API_BASE_URL
        self.access_token = access_token
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.priority = priority
        self.app_version = app_version

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        """搜索 Bangumi 候选。"""

        payload = self._post_json(
            "/v0/search/subjects",
            {"limit": 10, "offset": 0},
            {
                "keyword": parsed.title,
                "sort": "match",
                "filter": {"type": [2], "nsfw": False},
            },
        )
        items = payload.get("data")
        if not isinstance(items, list):
            raise RuntimeError("Bangumi 返回的候选列表格式无效")
        candidates: list[MetadataCandidate] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != 2 or item.get("nsfw") is True:
                continue
            candidate = self._candidate_from_subject(item, parsed.media_type)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def test_connection(self) -> bool:
        """通过最小动画搜索验证 Bangumi 接口可访问。"""

        self._post_json(
            "/v0/search/subjects",
            {"limit": 1, "offset": 0},
            {
                "keyword": "Bangumi",
                "sort": "match",
                "filter": {"type": [2], "nsfw": False},
            },
        )
        return True

    def _post_json(
        self,
        path: str,
        query: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, Any]:
        requestUrl = f"{self.base_url}{path}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": (
                f"txlfire/MediaAI-Renamer/{self.app_version} "
                f"({BANGUMI_PROJECT_URL})"
            ),
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = url_request.Request(
            requestUrl,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with url_request.urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("Bangumi 返回了无效的数据结构")
                return result
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise RuntimeError("Bangumi 鉴权失败，请检查 Access Token") from exc
                if exc.code == 429:
                    raise RuntimeError("Bangumi 请求频率过高，请稍后重试") from exc
                if exc.code >= 500 and attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise RuntimeError(f"Bangumi 请求失败：HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                reason = exc.reason if isinstance(exc, URLError) else str(exc)
                raise RuntimeError(f"Bangumi 网络请求失败：{reason}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Bangumi 返回了无法解析的 JSON 数据") from exc

        raise RuntimeError("Bangumi 请求失败")

    def _wait_before_retry(self, attempt: int) -> None:
        time.sleep(min(0.25 * (2**attempt), 2.0))

    def _candidate_from_subject(
        self,
        item: dict[str, Any],
        media_type: str,
    ) -> MetadataCandidate | None:
        providerId = str(item.get("id") or "").strip()
        originalTitle = str(item.get("name") or "").strip()
        chineseTitle = str(item.get("name_cn") or "").strip()
        title = chineseTitle or originalTitle
        if not providerId or not title:
            return None

        dateValue = str(item.get("date") or "").strip()
        images = item.get("images") if isinstance(item.get("images"), dict) else {}
        rating = item.get("rating") if isinstance(item.get("rating"), dict) else {}
        genres = _subject_genres(item)
        matchBasis = []
        if chineseTitle:
            matchBasis.append("中文标题")
        if originalTitle:
            matchBasis.append("原始标题")
        rawData = dict(item)
        rawData.update(
            {
                "match_basis": matchBasis,
                "match_reason": (
                    "Bangumi 动画候选，依据站点匹配排序；"
                    f"可比对字段：{'、'.join(matchBasis)}。"
                ),
            }
        )
        return MetadataCandidate(
            provider="Bangumi",
            provider_id=providerId,
            media_type=media_type,
            title=title,
            original_title=originalTitle,
            year=_extract_year(dateValue),
            season=None,
            episode=None,
            overview=str(item.get("summary") or ""),
            localized_title=title,
            chinese_title=chineseTitle,
            english_title="",
            release_date=dateValue,
            vote_average=_float_or_none(rating.get("score")),
            poster_path=str(images.get("common") or images.get("large") or ""),
            original_language="",
            genres=genres,
            raw_data=rawData,
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


def _subject_genres(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    metaTags = item.get("meta_tags")
    if isinstance(metaTags, list):
        values.extend(str(tag).strip() for tag in metaTags if str(tag).strip())
    tags = item.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                value = str(tag.get("name") or "").strip()
            else:
                value = str(tag or "").strip()
            if value:
                values.append(value)
    return list(dict.fromkeys(values))
