"""Media source API."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import AuditWriteContext
from app.service.media_source_service import (
    bulk_delete_media_sources,
    create_media_source,
    delete_media_source,
    list_local_directories,
    list_media_sources,
    listMediaSourcesPage,
    get_media_source,
    list_source_directories,
    set_media_source_enabled,
    test_media_source_connection,
    test_media_source_connection_payload,
    update_media_source,
    verifyWebDavConditionalMove,
)
from app.service.remote_operation_service import RemoteOperationLockConflict

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


def _sourceAuditContext(request: Request, actor) -> AuditWriteContext:
    return AuditWriteContext(actor, request.client.host if request.client else None, request.headers.get("user-agent"))


@router.get("")
def list_sources(request: Request):
    return list_media_sources(request.app.state.settings)


@router.get("/page")
def list_sources_page(request: Request, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200), q: str = Query(default="", max_length=200),
    enabled_only: bool = Query(default=False)):
    return listMediaSourcesPage(request.app.state.settings, page=page, pageSize=page_size,
                               query=q, enabledOnly=enabled_only)


@router.get("/local-directories", dependencies=[Depends(require_permission("source:write"))])
def browse_local_directories(path: str | None = None):
    try:
        return list_local_directories(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test-connection", dependencies=[Depends(require_permission("source:write"))])
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


@router.get("/{source_id}")
def get_source(source_id: int, request: Request):
    try:
        return get_media_source(request.app.state.settings, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="媒体源不存在或已退役") from exc


@router.get("/{source_id}/directories", dependencies=[Depends(require_permission("source:write"))])
def browse_source_directories(source_id: int, request: Request, path: str | None = None):
    try:
        return list_source_directories(request.app.state.settings, source_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/test-connection", dependencies=[Depends(require_permission("source:write"))])
def test_source_connection(source_id: int, request: Request):
    try:
        return test_media_source_connection(request.app.state.settings, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/verify-conditional-move")
def verify_source_conditional_move(
    source_id: int,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        return verifyWebDavConditionalMove(
            request.app.state.settings,
            source_id,
            auditContext=_sourceAuditContext(request, current_user),
        )
    except RemoteOperationLockConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_source(
    payload: MediaSourceCreateRequest,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        result = create_media_source(
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
            auditContext=_sourceAuditContext(request, current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.put("/{source_id}")
def update_source(
    source_id: int,
    payload: MediaSourceUpdateRequest,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        result = update_media_source(
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
            auditContext=_sourceAuditContext(request, current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.patch("/{source_id}/enabled")
def update_source_enabled(
    source_id: int,
    payload: MediaSourceEnabledRequest,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        result = set_media_source_enabled(
            request.app.state.settings,
            source_id,
            payload.enabled,
            auditContext=_sourceAuditContext(request, current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.delete("/{source_id}")
def remove_source(
    source_id: int,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        result = delete_media_source(request.app.state.settings, source_id, auditContext=_sourceAuditContext(request, current_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/bulk-delete")
def remove_sources(
    payload: MediaSourceBulkDeleteRequest,
    request: Request,
    current_user=Depends(require_permission("source:write")),
):
    try:
        result = bulk_delete_media_sources(request.app.state.settings, payload.ids, auditContext=_sourceAuditContext(request, current_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
