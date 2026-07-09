"""任务运行日志 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import require_authenticated_user
from app.service.operation_log_service import (
    cleanup_operation_logs,
    export_operation_logs_text,
    list_operation_logs,
    operation_log_page_to_dict,
)

router = APIRouter(prefix="/api/operation-logs", tags=["operation-logs"])


@router.get("")
def list_operation_logs_api(
    request: Request,
    task_type: str = Query(alias="task_type"),
    task_id: int = Query(alias="task_id"),
    after_id: int = Query(default=0, alias="after_id"),
    limit: int = Query(default=100, ge=1, le=500),
    _current_user=Depends(require_authenticated_user()),
):
    """按任务读取运行日志。"""

    return operation_log_page_to_dict(
        list_operation_logs(
            request.app.state.settings,
            task_type=task_type,
            task_id=task_id,
            after_id=after_id,
            limit=limit,
        )
    )


@router.get("/export")
def export_operation_logs_api(
    request: Request,
    task_type: str = Query(alias="task_type"),
    task_id: int = Query(alias="task_id"),
    _current_user=Depends(require_authenticated_user()),
):
    """导出指定任务运行日志。"""

    return Response(
        content=export_operation_logs_text(
            request.app.state.settings,
            task_type=task_type,
            task_id=task_id,
        ),
        media_type="text/plain; charset=utf-8",
    )


@router.post("/cleanup")
def cleanup_operation_logs_api(
    request: Request,
    _current_user=Depends(require_authenticated_user()),
):
    """按策略清理任务运行日志。"""

    summary = cleanup_operation_logs(request.app.state.settings)
    return {
        "deleted": summary.deleted,
        "bytes_deleted": summary.bytes_deleted,
    }
