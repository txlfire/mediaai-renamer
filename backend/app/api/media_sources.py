"""Media source API."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.media_source_service import (
    bulk_delete_media_sources,
    create_media_source,
    delete_media_source,
    list_local_directories,
    list_media_sources,
    list_source_directories,
    set_media_source_enabled,
    test_media_source_connection,
    test_media_source_connection_payload,
    update_media_source,
)

router = APIRouter(prefix="/api/media-sources", tags=["media-sources"])


class MediaSourceCreateRequest(BaseModel):
    name: str
    path: str
    enabled: bool = True
    path_type: str = "local"
    host: str | None = None
    share_name: str | None = None
    domain: str | None = None
    username: str | None = None
    secret: str | None = None
    port: int | None = None
    remark: str | None = None
    nfs_host: str | None = None
    nfs_export: str | None = None
    nfs_version: str | None = None
    nfs_options: str | None = None
    local_mount_path: str | None = None


class MediaSourceUpdateRequest(BaseModel):
    name: str
    path: str
    enabled: bool = True
    username: str | None = None
    secret: str | None = None
    nfs_host: str | None = None
    nfs_export: str | None = None
    clear_history_on_path_change: bool = False


class MediaSourceEnabledRequest(BaseModel):
    enabled: bool


class MediaSourceBulkDeleteRequest(BaseModel):
    ids: list[int]


class MediaSourceConnectionTestRequest(BaseModel):
    path: str
    path_type: str = "local"
    host: str | None = None
    share_name: str | None = None
    domain: str | None = None
    username: str | None = None
    secret: str | None = None
    port: int | None = None
    nfs_host: str | None = None
    nfs_export: str | None = None
    nfs_version: str | None = None
    nfs_options: str | None = None
    local_mount_path: str | None = None


@router.get("")
def list_sources(request: Request):
    return list_media_sources(request.app.state.settings)


@router.get("/local-directories")
def browse_local_directories(path: str | None = None):
    try:
        return list_local_directories(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test-connection")
def test_source_connection_payload(payload: MediaSourceConnectionTestRequest):
    try:
        return test_media_source_connection_payload(
            payload.path_type,
            payload.path,
            username=payload.username,
            secret=payload.secret,
            host=payload.host,
            share_name=payload.share_name,
            domain=payload.domain,
            port=payload.port,
            nfs_host=payload.nfs_host,
            nfs_export=payload.nfs_export,
            nfs_version=payload.nfs_version,
            nfs_options=payload.nfs_options,
            local_mount_path=payload.local_mount_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{source_id}/directories")
def browse_source_directories(source_id: int, request: Request, path: str | None = None):
    try:
        return list_source_directories(request.app.state.settings, source_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/test-connection")
def test_source_connection(source_id: int, request: Request):
    try:
        return test_media_source_connection(request.app.state.settings, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_source(
    payload: MediaSourceCreateRequest,
    request: Request,
    _current_user=Depends(require_permission("source:write")),
):
    try:
        return create_media_source(
            request.app.state.settings,
            payload.name,
            payload.path,
            payload.enabled,
            path_type=payload.path_type,
            host=payload.host,
            share_name=payload.share_name,
            domain=payload.domain,
            username=payload.username,
            secret=payload.secret,
            port=payload.port,
            remark=payload.remark,
            nfs_host=payload.nfs_host,
            nfs_export=payload.nfs_export,
            nfs_version=payload.nfs_version,
            nfs_options=payload.nfs_options,
            local_mount_path=payload.local_mount_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{source_id}")
def update_source(
    source_id: int,
    payload: MediaSourceUpdateRequest,
    request: Request,
    _current_user=Depends(require_permission("source:write")),
):
    try:
        return update_media_source(
            request.app.state.settings,
            source_id,
            payload.name,
            payload.path,
            payload.enabled,
            clear_history_on_path_change=payload.clear_history_on_path_change,
            username=payload.username,
            secret=payload.secret,
            nfs_host=payload.nfs_host,
            nfs_export=payload.nfs_export,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{source_id}/enabled")
def update_source_enabled(
    source_id: int,
    payload: MediaSourceEnabledRequest,
    request: Request,
    _current_user=Depends(require_permission("source:write")),
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
def remove_source(
    source_id: int,
    request: Request,
    _current_user=Depends(require_permission("source:write")),
):
    try:
        return delete_media_source(request.app.state.settings, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bulk-delete")
def remove_sources(
    payload: MediaSourceBulkDeleteRequest,
    request: Request,
    _current_user=Depends(require_permission("source:write")),
):
    try:
        return bulk_delete_media_sources(request.app.state.settings, payload.ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
