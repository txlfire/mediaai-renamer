"""System settings API."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.service.settings_service import (
    list_setting_values,
    test_tmdb_connection,
    update_setting_values,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    """Hot settings update payload."""

    values: dict[str, object]


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


@router.post("/tmdb/test")
def test_tmdb_settings(request: Request):
    """Validate current TMDB settings by calling TMDB."""

    try:
        return test_tmdb_connection(request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
