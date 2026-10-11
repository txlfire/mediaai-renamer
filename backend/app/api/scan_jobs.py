"""扫描任务 API。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from typing import Literal
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import AuditWriteContext
from app.service.scan_queue_service import acceptScan, getScan
from app.service.scan_service import (
    SCAN_MODE_FULL,
    get_scan_mode_suggestion,
    list_media_files,
    listMediaFilesPage,
    list_scan_jobs,
    listScanJobsPage,
)

router = APIRouter(prefix="/api", tags=["scan"])


class ScanJobCreateRequest(BaseModel):
    """创建扫描任务请求。"""

    media_source_id: int
    scan_mode: str = SCAN_MODE_FULL


@router.post("/scan-jobs", status_code=202)
def create_scan_job(
    payload: ScanJobCreateRequest,
    request: Request,
    _current_user=Depends(require_permission("scan:run")),
):
    """兼容受理入口；只创建 queued 任务，实际扫描由后台进程执行。"""

    try:
        return acceptScan(
            request.app.state.settings,
            payload.media_source_id,
            payload.scan_mode,
            AuditWriteContext(_current_user, request.client.host if request.client else None,
                              request.headers.get("user-agent")),
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


@router.get("/media-files/page")
def get_media_files_page(request: Request, media_source_id: int | None = Query(None, ge=1),
    scan_job_id: int | None = Query(None, ge=1), page: int = Query(1, ge=1, le=1_000_000),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: Literal["id", "file_name", "extension", "file_size", "modified_at", "created_at", "scan_job_id", "media_source_id", "file_path"] = "modified_at",
    sort_order: Literal["asc", "desc"] = "desc"):
    """分页、排序和计数在后端完成，旧/media-files仍返回列表。"""
    return listMediaFilesPage(request.app.state.settings, media_source_id, scan_job_id,
        page=page, pageSize=page_size, sortBy=sort_by, sortOrder=sort_order)


@router.post("/scan-jobs/async", status_code=202)
def accept_scan_job(payload: ScanJobCreateRequest, request: Request,
                    currentUser=Depends(require_permission("scan:run"))):
    """持久化受理扫描；返回queued任务，不等待网络或媒体目录访问。"""
    try:
        return acceptScan(request.app.state.settings, payload.media_source_id, payload.scan_mode,
            AuditWriteContext(currentUser, request.client.host if request.client else None,
                              request.headers.get("user-agent")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scan-jobs/page")
def get_scan_jobs_page(request: Request, media_source_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1, le=1_000_000), page_size: int = Query(50, ge=1, le=200),
    sort_by: Literal["id", "scan_mode", "scanned_count", "video_count", "new_count", "changed_count",
                     "skipped_count", "missing_count", "warning_count", "batch_size", "batch_interval_seconds",
                     "started_at", "ended_at", "created_at"] = "id",
    sort_order: Literal["asc", "desc"] = "desc", active_only: bool = False):
    """分页读取任务，active_only独立查询活跃任务；旧列表接口保持兼容。"""
    return listScanJobsPage(request.app.state.settings, media_source_id, page=page, pageSize=page_size,
                            sortBy=sort_by, sortOrder=sort_order, activeOnly=active_only)


@router.get("/scan-jobs/{jobId}")
def get_scan_job(jobId: int, request: Request):
    """读取单个任务的真实执行状态和已提交进度。"""
    try:
        return getScan(request.app.state.settings, jobId)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
