"""Rename preview API."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.schema.metadata import MetadataCandidate
from app.service.preview_service import (
    apply_metadata_candidate,
    generate_rename_previews,
    list_metadata_candidates,
    list_rename_previews,
    match_all_unmatched_metadata,
    match_rename_preview_metadata,
    match_rename_previews_metadata,
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


class ApplyMetadataCandidateRequest(BaseModel):
    """Manual metadata candidate selection request."""

    candidate: MetadataCandidate
    score: int


class BatchMetadataMatchRequest(BaseModel):
    """Batch metadata match request."""

    rename_preview_ids: list[int]


class AllMetadataMatchRequest(BaseModel):
    """Match all currently scoped unmatched previews."""

    media_source_id: int | None = None
    scan_job_id: int | None = None


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


@router.post("/{preview_id}/metadata-match")
def match_preview_metadata(preview_id: int, request: Request):
    """Run TMDB metadata matching for one preview."""

    try:
        return match_rename_preview_metadata(request.app.state.settings, preview_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/metadata-match")
def match_previews_metadata(payload: BatchMetadataMatchRequest, request: Request):
    """Run TMDB metadata matching for selected previews."""

    return match_rename_previews_metadata(
        request.app.state.settings,
        payload.rename_preview_ids,
    )


@router.post("/metadata-match/all")
def match_all_metadata(payload: AllMetadataMatchRequest, request: Request):
    """Run TMDB metadata matching for all unmatched previews in current scope."""

    return match_all_unmatched_metadata(
        request.app.state.settings,
        media_source_id=payload.media_source_id,
        scan_job_id=payload.scan_job_id,
    )


@router.get("/{preview_id}/metadata-candidates")
def get_preview_metadata_candidates(preview_id: int, request: Request):
    """List sorted metadata candidates for one preview."""

    try:
        return list_metadata_candidates(request.app.state.settings, preview_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{preview_id}/metadata-candidate")
def select_preview_metadata_candidate(
    preview_id: int,
    payload: ApplyMetadataCandidateRequest,
    request: Request,
):
    """Apply a manually selected metadata candidate."""

    try:
        return apply_metadata_candidate(
            request.app.state.settings,
            preview_id,
            payload.candidate,
            payload.score,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
