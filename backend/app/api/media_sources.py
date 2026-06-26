"""Media source API."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.service.media_source_service import (
    bulk_delete_media_sources,
    create_media_source,
    delete_media_source,
    list_local_directories,
    list_media_sources,
    set_media_source_enabled,
    update_media_source,
)

router = APIRouter(prefix="/api/media-sources", tags=["media-sources"])


class MediaSourceCreateRequest(BaseModel):
    name: str
    path: str
    enabled: bool = True


class MediaSourceUpdateRequest(BaseModel):
    name: str
    path: str
    enabled: bool = True
    clear_history_on_path_change: bool = False


class MediaSourceEnabledRequest(BaseModel):
    enabled: bool


class MediaSourceBulkDeleteRequest(BaseModel):
    ids: list[int]


@router.get("")
def list_sources(request: Request):
    return list_media_sources(request.app.state.settings)


@router.get("/local-directories")
def browse_local_directories(path: str | None = None):
    try:
        return list_local_directories(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_source(payload: MediaSourceCreateRequest, request: Request):
    try:
        return create_media_source(
            request.app.state.settings, payload.name, Path(payload.path), payload.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{source_id}")
def update_source(source_id: int, payload: MediaSourceUpdateRequest, request: Request):
    try:
        return update_media_source(
            request.app.state.settings,
            source_id,
            payload.name,
            Path(payload.path),
            payload.enabled,
            payload.clear_history_on_path_change,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{source_id}/enabled")
def update_source_enabled(
    source_id: int,
    payload: MediaSourceEnabledRequest,
    request: Request,
):
    try:
        return set_media_source_enabled(
            request.app.state.settings,
            source_id,
            payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{source_id}")
def remove_source(source_id: int, request: Request):
    try:
        return delete_media_source(request.app.state.settings, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bulk-delete")
def remove_sources(payload: MediaSourceBulkDeleteRequest, request: Request):
    try:
        return bulk_delete_media_sources(request.app.state.settings, payload.ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
