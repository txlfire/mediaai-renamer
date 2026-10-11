"""TMDB metadata search client."""

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import traceback
from pathlib import Path

from app.core.logger import get_logger

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.external_request_budget_service import openExternalRequest, clientRequestDeadline, ExternalBudgetWaitError, ExternalBudgetUnavailable


TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
MAX_TMDB_RESPONSE_BYTES = 4 * 1024 * 1024


class InvalidTmdbResponseError(RuntimeError):
    """外部 TMDB 响应格式无效，不携带原始正文。"""


def recordTmdbDiagnostic(exc: Exception, *, channel: str, phase: str, providerId: int | None = None) -> dict:
    cause = exc.__cause__ or exc
    status = cause.code if isinstance(cause, HTTPError) else None
    errorType = "internal"
    if isinstance(exc, ExternalBudgetWaitError):
        errorType = "budget_wait"
    elif isinstance(exc, ExternalBudgetUnavailable):
        errorType = "budget_unavailable"
    elif isinstance(exc, InvalidTmdbResponseError):
        errorType = "invalid_response"
    elif status == 429:
        errorType = "rate_limit"
    elif status in {401, 403}:
        errorType = "authentication"
    elif status and status >= 500:
        errorType = "server"
    elif isinstance(cause, TimeoutError):
        errorType = "timeout"
    elif isinstance(cause, URLError):
        errorType = "timeout" if isinstance(cause.reason, TimeoutError) else "network"
    elif isinstance(exc, (RuntimeError, OSError)):
        errorType = "request"
    diagnostic = {"code": "tmdb_detail_unavailable" if phase == "detail" else "tmdb_auth_fallback",
                  "phase": phase, "channel": channel, "provider_id": providerId,
                  "error_type": errorType, "http_status": status, "exception_type": type(exc).__name__}
    if errorType in {"budget_wait", "budget_unavailable"}:
        diagnostic["submission_executed"] = False
    if errorType == "internal":
        diagnostic["frames"] = [{"module": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                                for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
    try:
        get_logger(__name__).warning("TMDB诊断 %s", json.dumps(diagnostic, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - 详情降级的辅助诊断不覆盖主结果。
        pass
    return diagnostic


class TmdbClient:
    """使用 /3 API；V4 表示优先使用读取令牌的 Bearer 认证，不代表 /4 端点。"""

    def __init__(
        self,
        api_key: str = "",
        v4_token: str = "",
        language: str = "zh-CN",
        region: str = "CN",
        timeout_ms: int = 10000,
        requestBudget=None,
    ):
        self.api_key = api_key
        self.v4_token = v4_token
        self.language = language
        self.region = region
        self.timeout_seconds = max(1, timeout_ms / 1000)
        self.requestBudget = requestBudget

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
            "User-Agent": "MediaAI-Renamer",
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
            with openExternalRequest(self.requestBudget, request, self.timeout_seconds, urlopen) as response:
                body = response.read(MAX_TMDB_RESPONSE_BYTES + 1)
                if len(body) > MAX_TMDB_RESPONSE_BYTES:
                    raise InvalidTmdbResponseError("TMDB 响应超出大小上限")
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise InvalidTmdbResponseError("TMDB 响应不是对象")
                return payload
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidTmdbResponseError("TMDB 服务响应格式无效") from exc
        except HTTPError as exc:
            try:
                exc.close()
            except Exception:  # noqa: BLE001 - 保留请求错误，不回显正文或覆盖主因。
                pass
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError("TMDB 网络请求失败") from exc

    @clientRequestDeadline
    def test_connection(self) -> bool:
        """Validate API key and network access with a lightweight TMDB endpoint."""

        self._get_json("/configuration", {})
        return True

    @clientRequestDeadline
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
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise InvalidTmdbResponseError("TMDB results 字段不是数组")
        for item in results:
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
        except Exception as exc:
            diagnostic = recordTmdbDiagnostic(exc, channel="v4" if self.v4_token else "v3", phase="detail",
                                              providerId=provider_id if isinstance(provider_id, int) else None)
            return {"metadata_diagnostics": [diagnostic]}

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
