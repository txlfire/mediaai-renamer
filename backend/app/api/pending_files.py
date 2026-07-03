"""Pending files API."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.service.pending_file_service import (
    clear_pending_files,
    list_pending_files,
    move_pending_files,
    parse_pending_file_with_ai,
    remove_pending_file,
)

router = APIRouter(prefix="/api/pending-files", tags=["pending-files"])


class MovePendingFilesRequest(BaseModel):
    """Pending file move request."""

    ids: list[int]
    target_directory: str


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


@router.delete("/{pending_file_id}")
def remove_pending(pending_file_id: int, request: Request):
    """Remove one pending task without deleting the real file."""

    try:
        return remove_pending_file(request.app.state.settings, pending_file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{pending_file_id}/ai-parse")
def parse_pending_with_ai(pending_file_id: int, request: Request):
    """Run AI structured parsing for one pending file."""

    try:
        return parse_pending_file_with_ai(request.app.state.settings, pending_file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/clear")
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
def move_pending(payload: MovePendingFilesRequest, request: Request):
    """Move selected pending files to a target directory."""

    try:
        return move_pending_files(
            request.app.state.settings,
            payload.ids,
            payload.target_directory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
