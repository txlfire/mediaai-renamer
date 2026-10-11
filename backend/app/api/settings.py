"""System settings API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_authenticated_user, require_permission
from app.service.audit_service import AuditWriteContext, record_audit_event, sanitize_audit_detail
from app.service.settings_service import (
    AI_TEST_PAGE_KEY,
    TMDB_TEST_PAGE_KEY,
    ai_test_result_to_dict,
    build_ai_config_snapshot,
    build_imdb_config_snapshot,
    build_tmdb_config_snapshot,
    get_imdb_test_result,
    get_page_test_result,
    imdb_test_result_to_dict,
    list_setting_values,
    page_test_result_to_dict,
    test_ai_connection,
    test_imdb_connection,
    test_tmdb_channel,
    test_tmdb_connection,
    update_setting_values,
)
from app.service.metadata_provider_service import (
    list_metadata_provider_configs,
    metadata_provider_config_to_dict,
    test_metadata_provider_config,
    update_metadata_provider_config,
)
from app.service.naming_settings_service import (
    build_naming_template_diff,
    build_naming_template_preview,
    export_naming_template_bundle,
    naming_template_bundle_to_dict,
    naming_template_diff_to_dict,
    naming_template_preview_to_dict,
    validate_naming_template_bundle_text,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    """Hot settings update payload."""

    values: dict[str, object]


class SaveTmdbTestResultRequest(BaseModel):
    """Persist per-channel TMDB test results."""

    v4: dict[str, object]
    v3: dict[str, object]


class SaveImdbTestResultRequest(BaseModel):
    """Persist IMDb test result."""

    status: str
    message: str | None = None
    response_ms: int | None = None
    error_message: str | None = None


class NamingTemplateSampleRequest(BaseModel):
    """Sample media fields for naming template preview."""

    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    extension: str | None = None
    extra: dict[str, object] | None = None


class NamingTemplatePreviewRequest(BaseModel):
    """Naming template preview or diff request."""

    media_type: str
    template: str | None = None
    separator: str | None = None
    keep_year: bool | None = None
    sample: NamingTemplateSampleRequest


class NamingTemplateImportRequest(BaseModel):
    """Naming template import payload."""

    raw_text: str


class MetadataProviderConfigRequest(BaseModel):
    """Metadata provider configuration payload."""

    enabled: bool | None = None
    priority: int | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None


def _model_payload(model: BaseModel) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.get("")
def get_settings(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """List effective system settings for the settings page."""

    return list_setting_values(request.app.state.settings)


@router.put("")
def update_settings(
    payload: UpdateSettingsRequest,
    request: Request,
    current_user=Depends(require_permission("settings:write")),
):
    """Update hot system settings."""

    operator = current_user.username if current_user is not None else "system"
    try:
        result = update_setting_values(
            request.app.state.settings,
            payload.values,
            operator=operator,
            auditContext=AuditWriteContext(current_user, request.client.host if request.client else None, request.headers.get("user-agent")),
        )
    except ValueError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="settings.update",
            action="update_settings",
            result="failed",
            summary=f"系统设置保存失败：{exc}",
            target_type="settings",
            actor=current_user,
            detail={
                "keys": sorted(payload.values.keys()),
                "values": sanitize_audit_detail(payload.values),
                "reason": str(exc),
            },
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/tmdb/test-result")
def get_tmdb_test_result(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """Return the latest persisted TMDB connection test result."""

    result = get_page_test_result(request.app.state.settings, TMDB_TEST_PAGE_KEY)
    current_snapshot = build_tmdb_config_snapshot(request.app.state.settings)
    payload = page_test_result_to_dict(result)
    if payload is None:
        return {
            "result": None,
            "current_snapshot": current_snapshot,
            "matches_current": False,
        }
    return {
        "result": payload,
        "current_snapshot": current_snapshot,
        "matches_current": payload["config_snapshot"] == current_snapshot,
    }


@router.post("/tmdb/test", dependencies=[Depends(require_permission("settings:write"))])
def test_tmdb_settings(request: Request):
    """Validate current TMDB settings by calling TMDB."""

    try:
        return test_tmdb_connection(request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tmdb/test/{channel}", dependencies=[Depends(require_permission("settings:write"))])
def test_tmdb_settings_channel(channel: str, request: Request):
    """Validate one TMDB channel."""

    if channel not in {"v4", "v3"}:
        raise HTTPException(status_code=400, detail="Unsupported TMDB channel")
    try:
        return test_tmdb_channel(request.app.state.settings, channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tmdb/test-result", dependencies=[Depends(require_permission("settings:write"))])
def save_tmdb_test_result():
    """Reject legacy client-authored TMDB test results."""

    raise HTTPException(status_code=410, detail="客户端保存 TMDB 测试结果的接口已停用，请调用 /api/settings/tmdb/test")


@router.get("/imdb/test-result")
def get_imdb_connection_test_result(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """Return the latest persisted IMDb connection test result."""

    result = get_imdb_test_result(request.app.state.settings)
    current_snapshot = build_imdb_config_snapshot(request.app.state.settings)
    payload = imdb_test_result_to_dict(result)
    if payload is None:
        return {
            "result": None,
            "current_snapshot": current_snapshot,
            "matches_current": False,
        }
    return {
        "result": payload,
        "current_snapshot": current_snapshot,
        "matches_current": bool(payload["is_valid"]) and payload["config_snapshot"] == current_snapshot,
    }


@router.post("/imdb/test", dependencies=[Depends(require_permission("settings:write"))])
def test_imdb_settings(request: Request):
    """Validate current IMDb reachability."""

    return test_imdb_connection(request.app.state.settings)


@router.post("/imdb/test-result", dependencies=[Depends(require_permission("settings:write"))])
def save_imdb_test_result():
    """Reject legacy client-authored IMDb test results."""

    raise HTTPException(status_code=410, detail="客户端保存 IMDb 测试结果的接口已停用，请调用 /api/settings/imdb/test")


@router.get("/ai/test-result")
def get_ai_connection_test_result(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """Return the latest persisted AI connection test result."""

    result = get_page_test_result(request.app.state.settings, AI_TEST_PAGE_KEY)
    current_snapshot = build_ai_config_snapshot(request.app.state.settings)
    payload = ai_test_result_to_dict(result)
    if payload is None:
        return {
            "result": None,
            "current_snapshot": current_snapshot,
            "matches_current": False,
        }
    return {
        "result": payload,
        "current_snapshot": current_snapshot,
        "matches_current": payload["config_snapshot"] == current_snapshot,
    }


@router.get("/ai/readiness")
def get_ai_readiness(
    request: Request,
    _current_user=Depends(require_authenticated_user()),
):
    """Return the minimum AI availability state required by business pages."""

    result = get_page_test_result(request.app.state.settings, AI_TEST_PAGE_KEY)
    if result is None:
        return {"ready": False, "status": "untested", "tested_at": None, "message": "AI 连接尚未测试"}
    matches_current = result.config_snapshot == build_ai_config_snapshot(request.app.state.settings)
    status = str(result.v4.get("status") or "failed")
    if not matches_current:
        return {"ready": False, "status": "stale", "tested_at": result.tested_at, "message": "AI 配置已变化，请重新测试连接"}
    if status != "success":
        return {"ready": False, "status": "failed", "tested_at": result.tested_at, "message": "AI 连接测试未通过"}
    return {"ready": True, "status": "ready", "tested_at": result.tested_at, "message": "AI 已就绪"}


@router.post("/ai/test", dependencies=[Depends(require_permission("settings:write"))])
def test_ai_settings(request: Request):
    """Validate current AI provider settings."""

    return test_ai_connection(request.app.state.settings)


@router.get("/metadata-providers")
def get_metadata_providers(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """List M10 metadata provider configs."""

    return [
        metadata_provider_config_to_dict(item)
        for item in list_metadata_provider_configs(request.app.state.settings)
    ]


@router.put("/metadata-providers/{provider}")
def update_metadata_provider(
    provider: str,
    payload: MetadataProviderConfigRequest,
    request: Request,
    current_user=Depends(require_permission("settings:write")),
):
    """Update one metadata provider config."""

    try:
        config = update_metadata_provider_config(
            request.app.state.settings,
            provider,
            _model_payload(payload),
            auditContext=AuditWriteContext(current_user, request.client.host if request.client else None,
                                           request.headers.get("user-agent")),
        )
    except ValueError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="settings.update",
            action="update_metadata_provider",
            result="failed",
            summary=f"元数据源配置保存失败：{provider}",
            target_type="metadata_provider",
            target_id=provider,
            actor=current_user,
            detail={
                "provider": provider,
                "values": sanitize_audit_detail(_model_payload(payload)),
                "reason": str(exc),
            },
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return metadata_provider_config_to_dict(config)


@router.post("/metadata-providers/{provider}/test")
def test_metadata_provider(
    provider: str,
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """Validate provider configuration and test an available real connection."""

    try:
        return test_metadata_provider_config(request.app.state.settings, provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/naming/export")
def export_naming_settings_bundle(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """Return the current naming template bundle for export."""

    return naming_template_bundle_to_dict(export_naming_template_bundle(request.app.state.settings))


@router.post("/naming/import", dependencies=[Depends(require_permission("settings:write"))])
def import_naming_settings_bundle(payload: NamingTemplateImportRequest, request: Request):
    """Validate one imported naming template bundle."""

    try:
        return naming_template_bundle_to_dict(
            validate_naming_template_bundle_text(request.app.state.settings, payload.raw_text)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/naming/test", dependencies=[Depends(require_permission("settings:write"))])
def test_naming_template(payload: NamingTemplatePreviewRequest, request: Request):
    """Generate a filename preview for a candidate naming template."""

    try:
        return naming_template_preview_to_dict(
            build_naming_template_preview(
                request.app.state.settings,
                media_type=payload.media_type,
                sample=_model_payload(payload.sample),
                template=payload.template,
                separator=payload.separator,
                keep_year=payload.keep_year,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/naming/diff", dependencies=[Depends(require_permission("settings:write"))])
def diff_naming_template(payload: NamingTemplatePreviewRequest, request: Request):
    """Compare current and candidate naming template outputs."""

    try:
        return naming_template_diff_to_dict(
            build_naming_template_diff(
                request.app.state.settings,
                media_type=payload.media_type,
                sample=_model_payload(payload.sample),
                template=payload.template,
                separator=payload.separator,
                keep_year=payload.keep_year,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
