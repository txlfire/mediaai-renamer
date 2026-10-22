"""任务运行日志 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.auth import require_permission
from app.service.operation_log_service import (
    cleanup_operation_logs,
    export_operation_logs_text,
    list_operation_logs,
    operation_log_page_to_dict,
)

router = APIRouter(prefix="/api/operation-logs", tags=["operation-logs"])


def _authorizeDirectoryLogs(request, taskType, taskId, actorId):
    if taskType in {"cloud_directory_execution","cloud_quarantine_run"}:
        from app.service.cloud_directory_log_service import requireDirectoryLogOwner
        from app.service.cloud_quarantine_log_service import requireQuarantineLogOwner
        from app.service.cloud_directory_plan_service import CloudDirectoryError
        from app.service.cloud_inventory_service import CloudInventoryError
        try:
            owner = requireQuarantineLogOwner if taskType == "cloud_quarantine_run" else requireDirectoryLogOwner
            owner(request.app.state.settings, taskId, actorId)
        except CloudDirectoryError as exc:
            raise HTTPException(status_code=403 if exc.code == "permission_denied" else 404, detail="无权读取或日志任务不存在") from None
        except CloudInventoryError:
            raise HTTPException(status_code=409, detail="日志暂不可用，请稍后核对") from None


@router.get("")
def list_operation_logs_api(
    request: Request,
    task_type: str = Query(alias="task_type"),
    task_id: int = Query(alias="task_id"),
    after_id: int = Query(default=0, alias="after_id"),
    limit: int = Query(default=100, ge=1, le=500),
    _current_user=Depends(require_permission("audit:read")),
):
    """按任务读取运行日志。"""
    _authorizeDirectoryLogs(request, task_type, task_id, _current_user.id)
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
    _current_user=Depends(require_permission("audit:read")),
):
    """导出指定任务运行日志。"""
    _authorizeDirectoryLogs(request, task_type, task_id, _current_user.id)
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
    _current_user=Depends(require_permission("settings:write")),
):
    """按策略清理任务运行日志。"""

    summary = cleanup_operation_logs(request.app.state.settings)
    return {
        "deleted": summary.deleted,
        "bytes_deleted": summary.bytes_deleted,
        "budget_exhausted": summary.budget_exhausted,
    }
