"""System settings API."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
    save_imdb_connection_test_result,
    save_tmdb_connection_test_result,
    test_ai_connection,
    test_imdb_connection,
    test_tmdb_channel,
    test_tmdb_connection,
    update_setting_values,
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


def _model_payload(model: BaseModel) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


@router.get("")
def get_settings(request: Request):
    """List effective system settings for the settings page."""

    return list_setting_values(request.app.state.settings)


@router.put("")
def update_settings(payload: UpdateSettingsRequest, request: Request):
    """Update hot system settings."""

    try:
        return update_setting_values(
            request.app.state.settings,
            payload.values,
            operator="admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tmdb/test-result")
def get_tmdb_test_result(request: Request):
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


@router.post("/tmdb/test")
def test_tmdb_settings(request: Request):
    """Validate current TMDB settings by calling TMDB."""

    try:
        return test_tmdb_connection(request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tmdb/test/{channel}")
def test_tmdb_settings_channel(channel: str, request: Request):
    """Validate one TMDB channel."""

    if channel not in {"v4", "v3"}:
        raise HTTPException(status_code=400, detail="Unsupported TMDB channel")
    try:
        return test_tmdb_channel(request.app.state.settings, channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tmdb/test-result")
def save_tmdb_test_result(payload: SaveTmdbTestResultRequest, request: Request):
    """Persist the latest TMDB connection test result."""

    return save_tmdb_connection_test_result(
        request.app.state.settings,
        payload.v4,
        payload.v3,
    )


@router.get("/imdb/test-result")
def get_imdb_connection_test_result(request: Request):
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


@router.post("/imdb/test")
def test_imdb_settings(request: Request):
    """Validate current IMDb reachability."""

    return test_imdb_connection(request.app.state.settings)


@router.post("/imdb/test-result")
def save_imdb_test_result(payload: SaveImdbTestResultRequest, request: Request):
    """Persist the latest IMDb connection test result."""

    return save_imdb_connection_test_result(
        request.app.state.settings,
        _model_payload(payload),
    )


@router.get("/ai/test-result")
def get_ai_connection_test_result(request: Request):
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


@router.post("/ai/test")
def test_ai_settings(request: Request):
    """Validate current AI provider settings."""

    return test_ai_connection(request.app.state.settings)


@router.get("/naming/export")
def export_naming_settings_bundle(request: Request):
    """Return the current naming template bundle for export."""

    return naming_template_bundle_to_dict(export_naming_template_bundle(request.app.state.settings))


@router.post("/naming/import")
def import_naming_settings_bundle(payload: NamingTemplateImportRequest, request: Request):
    """Validate one imported naming template bundle."""

    try:
        return naming_template_bundle_to_dict(
            validate_naming_template_bundle_text(request.app.state.settings, payload.raw_text)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/naming/test")
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


@router.post("/naming/diff")
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
