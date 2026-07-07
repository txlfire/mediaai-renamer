"""AI provider abstraction and OpenAI-compatible implementations."""

from dataclasses import dataclass
import json
import time
from typing import Protocol
from urllib import request as url_request
from urllib.error import HTTPError, URLError


@dataclass(frozen=True)
class AiProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_ms: int
    max_retries: int


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
    if isinstance(error, HTTPError):
        status_code = int(error.code)
        if 500 <= status_code <= 599:
            return "server", status_code, f"HTTP {status_code}"
        return "client", status_code, f"HTTP {status_code}"
    reason = str(error)
    lowered = reason.lower()
    if any(token in lowered for token in ("timed out", "timeout", "超时")):
        return "timeout", None, reason
    if any(token in lowered for token in ("name or service", "nodename", "dns", "refused", "unreachable", "network")):
        return "network", None, reason
    return "unknown", None, reason


def ai_error_message(error_type: str, http_status: int | None, reason: str) -> str:
    if error_type == "network":
        return "AI 服务网络不可达，请检查网络连接、Base URL 或代理设置"
    if error_type == "timeout":
        return "AI 服务响应超时，请调大超时时间或稍后重试"
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
        started_at = time.perf_counter()
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        request = url_request.Request(
            join_chat_completions_url(self.config.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "MediaAI-Renamer/1.0",
            },
            method="POST",
        )
        try:
            with url_request.urlopen(request, timeout=self.config.timeout_ms / 1000) as response:
                status_code = getattr(response, "status", 200)
                if status_code >= 400:
                    raise RuntimeError(f"HTTP {status_code}")
                response.read()
            return {
                "status": "success",
                "message": "AI 连接成功",
                "response_ms": round((time.perf_counter() - started_at) * 1000),
            }
        except (HTTPError, RuntimeError, OSError, URLError) as exc:
            error_type, http_status, reason = classify_ai_error(exc)
            return {
                "status": "failed",
                "message": ai_error_message(error_type, http_status, reason),
                "response_ms": round((time.perf_counter() - started_at) * 1000),
                "error_type": error_type,
                "http_status": http_status,
                "raw_error": reason,
            }

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, object]:
        started_at = time.perf_counter()
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        request = url_request.Request(
            join_chat_completions_url(self.config.base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "MediaAI-Renamer/1.0",
            },
            method="POST",
        )
        try:
            with url_request.urlopen(request, timeout=self.config.timeout_ms / 1000) as response:
                status_code = getattr(response, "status", 200)
                if status_code >= 400:
                    raise RuntimeError(f"HTTP {status_code}")
                body = json.loads(response.read().decode("utf-8"))
            choices = body.get("choices") if isinstance(body, dict) else []
            first_choice = choices[0] if choices else {}
            message = first_choice.get("message") if isinstance(first_choice, dict) else {}
            content = message.get("content") if isinstance(message, dict) else ""
            usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
            return {
                "status": "success",
                "content": str(content or ""),
                "response_ms": round((time.perf_counter() - started_at) * 1000),
                "usage": usage,
            }
        except (HTTPError, RuntimeError, OSError, URLError, json.JSONDecodeError) as exc:
            error_type, http_status, reason = classify_ai_error(exc)
            return {
                "status": "failed",
                "message": ai_error_message(error_type, http_status, reason),
                "response_ms": round((time.perf_counter() - started_at) * 1000),
                "error_type": error_type,
                "http_status": http_status,
                "raw_error": reason,
            }


class DeepSeekProvider(OpenAiCompatibleProvider):
    """DeepSeek uses the same OpenAI-compatible chat completions protocol."""
