"""AI structured media parsing service."""

import json
import re
from typing import Any

from app.core.config import AppSettings
from app.schema.ai_parse import AiParseCandidate, AiParseResult
from app.schema.media import ParsedMediaName
from app.service.ai_provider import AiProvider, AiProviderConfig, DeepSeekProvider, OpenAiCompatibleProvider
from app.service.external_submission_guard import check_external_submission
from app.service.settings_service import get_effective_settings


SUPPORTED_AI_PROVIDERS = {"deepseek", "openai_compatible", "custom"}
SUPPORTED_MEDIA_TYPES = {"movie", "tv", "unknown"}


def _build_provider(effective: dict[str, object]) -> AiProvider:
    provider_name = str(effective.get("ai.provider") or "deepseek")
    config = AiProviderConfig(
        provider=provider_name,
        model=str(effective.get("ai.model") or "deepseek-chat").strip(),
        api_key=str(effective.get("ai.api_key") or "").strip(),
        base_url=str(effective.get("ai.base_url") or "").strip(),
        timeout_ms=int(effective.get("ai.timeout_ms") or 30000),
        max_retries=int(effective.get("ai.max_retries") or 0),
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


def parse_media_with_ai(
    settings: AppSettings,
    source_module: str,
    source_record_id: int,
    file_name: str,
    file_path: str,
    parsed: ParsedMediaName,
    provider: AiProvider | None = None,
) -> AiParseResult:
    """Parse one media file name through AI after external submission protection."""

    effective = get_effective_settings(settings)
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
    )
    if not guard.allowed:
        return AiParseResult(
            status="blocked",
            message=guard.message or "已阻止外部提交，请手动处理",
            candidates=[],
        )

    ai_provider = provider or _build_provider(effective)
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
        )

    try:
        payload = _extract_json_object(str(response.get("content") or ""))
        candidate = _candidate_from_payload(payload)
        candidate.raw_data.setdefault("provider_id", str(effective.get("ai.active_profile_id") or provider_name))
        candidate.raw_data.setdefault("provider_name", provider_name)
        candidate.raw_data.setdefault("model_name", str(effective.get("ai.model") or ""))
    except ValueError as exc:
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
