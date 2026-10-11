"""Pending files API."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from app.api.auth import require_permission, require_authenticated_user
from app.service.audit_service import AuditWriteContext
from app.service.pending_move_service import acceptPendingMove, findPendingMove, getPendingMove

from app.service.pending_file_service import (
    clear_pending_files,
    list_pending_files,
    listPendingFilesPage,
    move_pending_files,
    parse_pending_file_with_ai,
    remove_pending_file,
)

router = APIRouter(prefix="/api/pending-files", tags=["pending-files"])


class MovePendingFilesRequest(BaseModel):
    """Pending file move request."""

    ids: list[int]
    target_directory: str


class AcceptPendingMoveRequest(MovePendingFilesRequest):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/move-async", status_code=202)
def accept_move(payload: AcceptPendingMoveRequest, request: Request, actor=Depends(require_permission("rename:execute"))):
    try:
        return acceptPendingMove(request.app.state.settings, payload.ids, payload.target_directory, payload.request_id,
            AuditWriteContext(actor, request.client.host if request.client else None, request.headers.get("user-agent")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/moves/by-request/{request_id}")
def find_move(request_id: str, request: Request, actor=Depends(require_authenticated_user())):
    try:
        return findPendingMove(request.app.state.settings, request_id, actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/moves/{task_id}")
def get_move(task_id: int, request: Request):
    try:
        return getPendingMove(request.app.state.settings, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def get_pending_files(
    request: Request,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
):
    """List pending files."""

    return list_pending_files(
        request.app.state.settings,
        media_source_id=media_source_id,
        scan_job_id=scan_job_id,
    )


@router.get("/page")
def getPendingFilesPage(request: Request, media_source_id: int | None = None, scan_job_id: int | None = None,
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """只读分页，不改变旧列表或清理操作契约。"""
    return listPendingFilesPage(request.app.state.settings, media_source_id, scan_job_id, page=page, page_size=page_size)


@router.delete("/{pending_file_id}", dependencies=[Depends(require_permission("scan:run"))])
def remove_pending(pending_file_id: int, request: Request):
    """Remove one pending task without deleting the real file."""

    try:
        return remove_pending_file(request.app.state.settings, pending_file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pending_file_id}/ai-parse", dependencies=[Depends(require_permission("metadata:submit"))])
def parse_pending_with_ai(pending_file_id: int, request: Request):
    """Run AI structured parsing for one pending file."""

    try:
        return parse_pending_file_with_ai(request.app.state.settings, pending_file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/clear", dependencies=[Depends(require_permission("scan:run"))])
def clear_pending(
    request: Request,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
):
    """Clear matching pending tasks."""

    return {
        "removed_count": clear_pending_files(
            request.app.state.settings,
            media_source_id=media_source_id,
            scan_job_id=scan_job_id,
        )
    }


@router.post("/move")
def move_pending(payload: MovePendingFilesRequest, request: Request, response: Response, actor=Depends(require_permission("rename:execute"))):
    """Move selected pending files to a target directory."""

    try:
        return move_pending_files(
            request.app.state.settings,
            payload.ids,
            payload.target_directory,
            context=AuditWriteContext(actor, request.client.host if request.client else None, request.headers.get("user-agent")),
            response=response,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
