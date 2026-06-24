"""Rename preview API."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.service.preview_service import (
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
)

router = APIRouter(prefix="/api/rename-previews", tags=["rename-previews"])


class GenerateRenamePreviewsRequest(BaseModel):
    """Rename preview generation request."""

    media_source_id: int | None = None
    scan_job_id: int | None = None
    media_file_ids: list[int] | None = None
    overwrite_edited: bool = False


class UpdateRenamePreviewRequest(BaseModel):
    """Rename preview edit request."""

    target_name: str


@router.post("/generate")
def generate_previews(payload: GenerateRenamePreviewsRequest, request: Request):
    """Generate rename previews from scanned media files."""

    return generate_rename_previews(
        request.app.state.settings,
        media_source_id=payload.media_source_id,
        scan_job_id=payload.scan_job_id,
        media_file_ids=payload.media_file_ids,
        overwrite_edited=payload.overwrite_edited,
    )


@router.get("")
def get_previews(
    request: Request,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    status: str | None = None,
    media_type: str | None = None,
    keyword: str | None = Query(default=None),
):
    """List rename previews."""

    return list_rename_previews(
        request.app.state.settings,
        media_source_id=media_source_id,
        scan_job_id=scan_job_id,
        status=status,
        media_type=media_type,
        keyword=keyword,
    )


@router.put("/{preview_id}")
def update_preview(preview_id: int, payload: UpdateRenamePreviewRequest, request: Request):
    """Update one preview target filename."""

    try:
        return update_rename_preview(
            request.app.state.settings,
            preview_id,
            payload.target_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
