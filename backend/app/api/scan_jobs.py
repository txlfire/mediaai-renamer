"""扫描任务 API。"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.service.scan_service import (
    SCAN_MODE_FULL,
    get_scan_mode_suggestion,
    list_media_files,
    list_scan_jobs,
    run_full_scan,
)

router = APIRouter(prefix="/api", tags=["scan"])


class ScanJobCreateRequest(BaseModel):
    """创建扫描任务请求。"""

    media_source_id: int
    scan_mode: str = SCAN_MODE_FULL


@router.post("/scan-jobs")
def create_scan_job(payload: ScanJobCreateRequest, request: Request):
    """创建并执行全量扫描任务。"""

    try:
        return run_full_scan(
            request.app.state.settings,
            payload.media_source_id,
            scan_mode=payload.scan_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scan-jobs")
def get_scan_jobs(request: Request, media_source_id: int | None = None):
    """查询扫描任务列表。"""

    return list_scan_jobs(request.app.state.settings, media_source_id=media_source_id)


@router.get("/scan-jobs/mode-suggestion")
def get_mode_suggestion(request: Request, media_source_id: int):
    """查询指定媒体源的推荐扫描模式。"""

    try:
        return get_scan_mode_suggestion(request.app.state.settings, media_source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/media-files")
def get_media_files(
    request: Request,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
):
    """查询扫描出的媒体文件。"""

    return list_media_files(
        request.app.state.settings,
        media_source_id=media_source_id,
        scan_job_id=scan_job_id,
    )
