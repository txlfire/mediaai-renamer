"""AI 服务商适配；调用摘要不保存请求正文，外部格式错误统一分类。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import time
from typing import Protocol
from urllib import request as url_request
from urllib.error import HTTPError, URLError
from uuid import uuid4

from app.service.ai_diagnostics import AiCallContext, recordAiSummary
from app.service.external_request_budget_service import openExternalRequest, ExternalBudgetWaitError, ExternalBudgetUnavailable

MAX_AI_RESPONSE_BYTES = 4 * 1024 * 1024


class InvalidAiResponseError(ValueError):
    """外部响应编码、结构或大小不符合约定，不携带响应正文。"""


def _retryAfterSeconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if value.strip().isdigit():
            if len(value.strip()) > 10:
                return float("inf")
            return float(int(value.strip()))
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(frozen=True)
class AiProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_ms: int
    max_retries: int
    callContext: AiCallContext | None = None
    requestBudget: object | None = None


class AiProvider(Protocol):
    def test_connection(self) -> dict[str, object]:
        """Validate provider availability with a minimal request."""

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, object]:
        """Run one non-streaming chat completion request."""


def join_chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def classify_ai_error(error: BaseException) -> tuple[str, int | None, str]:
    if isinstance(error, ExternalBudgetWaitError):
        return "budget_wait", None, "外部请求等待预算已耗尽，本次未发送请求"
    if isinstance(error, ExternalBudgetUnavailable):
        return "budget_unavailable", None, "外部请求预算不可用，本次未发送请求"
    if isinstance(error, TimeoutError) or isinstance(error, URLError) and isinstance(error.reason, TimeoutError):
        return "timeout", None, "响应超时"
    if isinstance(error, (InvalidAiResponseError, UnicodeDecodeError, json.JSONDecodeError)):
        return "invalid_response", None, "AI 服务响应格式无效"
    if isinstance(error, HTTPError):
        status_code = int(error.code)
        if 500 <= status_code <= 599:
            return "server", status_code, f"HTTP {status_code}"
        return "client", status_code, f"HTTP {status_code}"
    reason = str(error)
    lowered = reason.lower()
    if any(token in lowered for token in ("timed out", "timeout", "超时")):
        return "timeout", None, "响应超时"
    if any(token in lowered for token in ("name or service", "nodename", "dns", "refused", "unreachable", "network")):
        return "network", None, "网络连接失败"
    return "unknown", None, "外部请求失败"


def ai_error_message(error_type: str, http_status: int | None, reason: str) -> str:
    if error_type in {"budget_wait", "budget_unavailable"}:
        return reason
    if error_type == "invalid_response":
        return "AI 服务响应格式无效，请检查接口地址、模型或服务端响应"
    if error_type == "network":
        return "AI 服务网络不可达，请检查网络连接、Base URL 或代理设置"
    if error_type == "timeout":
        return "AI 服务响应超时，结果可能未确定；请先核查，避免重复计费"
    if error_type == "server":
        return "AI 服务暂时不可用，请稍后重试"
    if error_type == "client" and http_status in {401, 403}:
        return "AI 鉴权失败，请检查 API Key 是否正确"
    if error_type == "client" and http_status == 404:
        return "AI 接口地址不存在，请检查 Base URL 是否正确"
    if error_type == "client" and http_status:
        return f"AI 请求失败（{http_status}），请检查模型名、API Key 或 Base URL"
    return f"AI 连接失败：{reason}"


class OpenAiCompatibleProvider:
    def __init__(self, config: AiProviderConfig):
        self.config = config

    def test_connection(self) -> dict[str, object]:
        result = self._completeChat([{"role": "user", "content": "ping"}], 1, 0)
        if result["status"] == "success":
            return {"status": "success", "message": "AI 连接成功", "response_ms": result["response_ms"]}
        return result

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, object]:
        return self._completeChat(messages, max_tokens, temperature)

    def _completeChat(self, messages: list[dict[str, str]], maxTokens: int, temperature: float) -> dict[str, object]:
        startedAt = time.perf_counter()
        deadline = startedAt + self.config.timeout_ms / 1000
        context = self.config.callContext or AiCallContext(uuid4().hex)
        retries = max(0, min(10, self.config.max_retries))
        for attempt in range(1, retries + 2):
            result = self._completeChatOnce(messages, maxTokens, temperature, deadline=deadline,
                                            context=context, attempt=attempt)
            result["attempts"] = attempt
            result["response_ms"] = round((time.perf_counter() - startedAt) * 1000)
            # POST只有明确未获服务的限流响应可重试；网络/超时/5xx不猜测是否已计费。
            if result.get("http_status") != 429 or attempt > retries:
                return result
            waitSeconds = result.get("retry_after_seconds")
            if not isinstance(waitSeconds, (int, float)):
                waitSeconds = min(4.0, 0.5 * 2 ** (attempt - 1))
            remaining = deadline - time.perf_counter()
            if waitSeconds >= remaining or remaining <= 0:
                result["budget_exhausted"] = True
                return result
            recordAiSummary(context, provider=self.config.provider, model=self.config.model, phase="retry",
                            status="waiting", httpStatus=429, attempt=attempt)
            time.sleep(waitSeconds)
            if time.perf_counter() >= deadline:
                result["budget_exhausted"] = True
                result["response_ms"] = round((time.perf_counter() - startedAt) * 1000)
                return result
        raise RuntimeError("AI 重试状态异常")

    def _completeChatOnce(self, messages: list[dict[str, str]], maxTokens: int, temperature: float, *,
                          deadline: float, context: AiCallContext, attempt: int) -> dict[str, object]:
        started_at = time.perf_counter()
        recordAiSummary(context, provider=self.config.provider, model=self.config.model, phase="transport", status="started", attempt=attempt)
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": maxTokens,
            "temperature": temperature,
            "stream": False,
        }
        status_code = None
        try:
            request = url_request.Request(
                join_chat_completions_url(self.config.base_url), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json",
                         "User-Agent": "MediaAI-Renamer"}, method="POST",
            )
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("AI 总预算已耗尽")
            with openExternalRequest(self.config.requestBudget, request, remaining, url_request.urlopen) as response:
                status_code = getattr(response, "status", 200)
                if status_code >= 400:
                    raise HTTPError(request.full_url, status_code, "AI HTTP failure", {}, None)
                rawBody = response.read(MAX_AI_RESPONSE_BYTES + 1)
                if time.perf_counter() >= deadline:
                    raise TimeoutError("AI 总预算已耗尽")
                if len(rawBody) > MAX_AI_RESPONSE_BYTES:
                    raise InvalidAiResponseError("响应超出大小上限")
                body = json.loads(rawBody.decode("utf-8"))
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
                raise InvalidAiResponseError("响应缺少 choices 数组")
            firstChoice = body["choices"][0]
            message = firstChoice.get("message") if isinstance(firstChoice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise InvalidAiResponseError("响应内容为空或类型无效")
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            elapsedMs = round((time.perf_counter() - started_at) * 1000)
            recordAiSummary(context, provider=self.config.provider, model=self.config.model, phase="transport", status="success",
                            elapsedMs=elapsedMs, httpStatus=status_code, usage=usage, attempt=attempt)
            return {
                "status": "success",
                "content": content,
                "response_ms": elapsedMs,
                "usage": usage,
            }
        except (HTTPError, OSError, URLError, InvalidAiResponseError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            error_type, http_status, reason = classify_ai_error(exc)
            retryAfter = _retryAfterSeconds(exc.headers.get("Retry-After")) if isinstance(exc, HTTPError) and exc.headers else None
            if isinstance(exc, HTTPError):
                try:
                    exc.close()
                except Exception:  # noqa: BLE001 - 关闭失败不能覆盖已分类的主请求错误。
                    pass
            elapsedMs = round((time.perf_counter() - started_at) * 1000)
            recordAiSummary(context, provider=self.config.provider, model=self.config.model, phase="transport", status="failed",
                            elapsedMs=elapsedMs, httpStatus=http_status or status_code, errorType=error_type, attempt=attempt)
            return {
                "status": "failed",
                "message": ai_error_message(error_type, http_status, reason),
                "response_ms": round((time.perf_counter() - started_at) * 1000),
                "error_type": error_type,
                "http_status": http_status,
                "raw_error": reason,
                "retry_after_seconds": retryAfter,
            }
        except Exception as exc:
            recordAiSummary(context, provider=self.config.provider, model=self.config.model, phase="transport", status="failed",
                            elapsedMs=round((time.perf_counter() - started_at) * 1000), errorType="internal", exception=exc, attempt=attempt)
            raise


class DeepSeekProvider(OpenAiCompatibleProvider):
    """DeepSeek uses the same OpenAI-compatible chat completions protocol."""
