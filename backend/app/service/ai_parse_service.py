"""AI structured media parsing service."""

import json
from dataclasses import asdict
import re
import time
from typing import Any
from uuid import uuid4

from app.core.config import AppSettings
from app.schema.ai_parse import AiParseCandidate, AiParseResult
from app.schema.media import ParsedMediaName
from app.service.ai_provider import AiProvider, AiProviderConfig, DeepSeekProvider, OpenAiCompatibleProvider
from app.service.ai_diagnostics import AiCallContext, recordAiSummary
from app.service.external_submission_guard import check_external_submission
from app.service.settings_service import get_effective_settings
from app.service.external_request_budget_service import ExternalRequestBudget


SUPPORTED_AI_PROVIDERS = {"deepseek", "openai_compatible", "custom"}
SUPPORTED_MEDIA_TYPES = {"movie", "tv", "unknown"}


def _build_provider(effective: dict[str, object], callContext: AiCallContext | None = None, settings: AppSettings | None = None) -> AiProvider:
    provider_name = str(effective.get("ai.provider") or "deepseek")
    config = AiProviderConfig(
        provider=provider_name,
        model=str(effective.get("ai.model") or "deepseek-chat").strip(),
        api_key=str(effective.get("ai.api_key") or "").strip(),
        base_url=str(effective.get("ai.base_url") or "").strip(),
        timeout_ms=int(effective.get("ai.timeout_ms") or 30000),
        max_retries=int(effective.get("ai.max_retries") or 0),
        callContext=callContext,
        requestBudget=ExternalRequestBudget(settings, "ai") if settings is not None else None,
    )
    if provider_name == "deepseek":
        return DeepSeekProvider(config)
    return OpenAiCompatibleProvider(config)


def _build_prompt(file_name: str, file_path: str, parsed: ParsedMediaName) -> list[dict[str, str]]:
    content = (
        "你是影视文件名结构化解析助手。只返回 JSON，不要返回 Markdown 或解释文本。"
        "JSON 字段必须包含 title、media_type、year、season、episode、confidence、reason。"
        "media_type 只能是 movie、tv 或 unknown；confidence 是 0 到 100 的整数。"
        "无法确定的字段使用 null。"
        f"\n文件名: {file_name}"
        f"\n路径: {file_path}"
        f"\n本地解析类型: {parsed.media_type}"
        f"\n本地解析标题: {parsed.title}"
        f"\n本地解析年份: {parsed.year}"
        f"\n本地解析季: {parsed.season}"
        f"\n本地解析集: {parsed.episode}"
    )
    return [{"role": "user", "content": content}]


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 返回内容不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI 返回 JSON 必须是对象")
    return payload


def _optional_int(value: object, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"AI 返回字段 {field_name} 必须是整数或 null")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AI 返回字段 {field_name} 必须是整数或 null") from exc


def _candidate_from_payload(payload: dict[str, Any]) -> AiParseCandidate:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("AI 返回字段 title 不能为空")
    media_type = str(payload.get("media_type") or "unknown").strip().lower()
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise ValueError("AI 返回字段 media_type 不支持")
    confidence = _optional_int(payload.get("confidence"), "confidence")
    if confidence is None or confidence < 0 or confidence > 100:
        raise ValueError("AI 返回字段 confidence 必须在 0 到 100 之间")
    return AiParseCandidate(
        title=title,
        media_type=media_type,
        year=_optional_int(payload.get("year"), "year"),
        season=_optional_int(payload.get("season"), "season"),
        episode=_optional_int(payload.get("episode"), "episode"),
        confidence=confidence,
        reason=str(payload.get("reason") or "").strip(),
        raw_data=dict(payload),
    )


def _parseMediaWithAi(
    settings: AppSettings,
    source_module: str,
    source_record_id: int,
    file_name: str,
    file_path: str,
    parsed: ParsedMediaName,
    provider: AiProvider | None = None,
    *,
    effective: dict[str, object],
    callContext: AiCallContext,
    authorization=None,
) -> AiParseResult:
    """Parse one media file name through AI after external submission protection."""

    if not bool(effective.get("ai.enabled")):
        return AiParseResult(status="failed", message="AI 智能解析未启用", candidates=[])

    provider_name = str(effective.get("ai.provider") or "deepseek")
    if provider_name not in SUPPORTED_AI_PROVIDERS:
        return AiParseResult(status="failed", message=f"暂不支持的 AI 服务商：{provider_name}", candidates=[])

    api_key = str(effective.get("ai.api_key") or "").strip()
    if not api_key and provider is None:
        return AiParseResult(status="failed", message="未配置 AI API Key", candidates=[])

    guard = check_external_submission(
        settings,
        source_module=source_module,
        source_record_id=source_record_id,
        file_name=file_name,
        file_path=file_path,
        match_title=parsed.title,
        target_service="ai",
        authorization=authorization, parsedPayload=asdict(parsed),
    )
    if not guard.allowed:
        return AiParseResult(
            status="blocked",
            message=guard.message or "已阻止外部提交，请手动处理",
            candidates=[],
        )

    ai_provider = provider or _build_provider(effective, callContext, settings)
    response = ai_provider.complete_chat(
        _build_prompt(file_name, file_path, parsed),
        max_tokens=500,
        temperature=0,
    )
    if response.get("status") != "success":
        return AiParseResult(
            status="failed",
            message=str(response.get("message") or "AI 解析失败"),
            candidates=[],
            response_ms=response.get("response_ms") if isinstance(response.get("response_ms"), int) else None,
            error_type=str(response.get("error_type") or "unknown"),
            http_status=response.get("http_status") if isinstance(response.get("http_status"), int) else None,
        )

    try:
        payload = _extract_json_object(str(response.get("content") or ""))
        candidate = _candidate_from_payload(payload)
        candidate.raw_data.setdefault("provider_id", str(effective.get("ai.active_profile_id") or provider_name))
        candidate.raw_data.setdefault("provider_name", provider_name)
        candidate.raw_data.setdefault("model_name", str(effective.get("ai.model") or ""))
    except ValueError as exc:
        recordAiSummary(callContext, provider=provider_name, model=str(effective.get("ai.model") or ""),
                        phase="validation", status="failed", errorType="invalid_content")
        return AiParseResult(
            status="failed",
            message=str(exc),
            candidates=[],
            response_ms=response.get("response_ms") if isinstance(response.get("response_ms"), int) else None,
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else {},
        )

    return AiParseResult(
        status="success",
        message="AI 解析成功",
        candidates=[candidate],
        response_ms=response.get("response_ms") if isinstance(response.get("response_ms"), int) else None,
        usage=response.get("usage") if isinstance(response.get("usage"), dict) else {},
    )


def parse_media_with_ai(settings: AppSettings, source_module: str, source_record_id: int, file_name: str,
                        file_path: str, parsed: ParsedMediaName, provider: AiProvider | None = None, *,
                        authorization=None, effectiveSettings: dict | None = None) -> AiParseResult:
    """外发保护后解析；日志只包含关联 ID 和结果摘要，不记录文件内容。"""
    context = AiCallContext(uuid4().hex, source_module, source_record_id)
    effective = get_effective_settings(settings) if effectiveSettings is None else effectiveSettings
    providerName = str(effective.get("ai.provider") or "deepseek")
    modelName = str(effective.get("ai.model") or "")
    startedAt = time.perf_counter()
    recordAiSummary(context, provider=providerName, model=modelName, phase="parse", status="started")
    try:
        result = _parseMediaWithAi(settings, source_module, source_record_id, file_name, file_path, parsed, provider,
                                   effective=effective, callContext=context, authorization=authorization)
    except Exception as exc:
        recordAiSummary(context, provider=providerName, model=modelName, phase="parse", status="failed",
                        elapsedMs=round((time.perf_counter() - startedAt) * 1000), errorType="internal", exception=exc)
        raise
    status = "skipped" if result.status == "failed" and result.response_ms is None else result.status
    recordAiSummary(context, provider=providerName, model=modelName, phase="parse", status=status,
                    elapsedMs=round((time.perf_counter() - startedAt) * 1000), usage=result.usage)
    return result
