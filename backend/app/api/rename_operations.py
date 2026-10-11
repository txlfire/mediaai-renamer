"""Safe rename operation API."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import runAuditedFileAction
from app.service.rename_queue_service import acceptRename
from app.service.rename_operation_service import (
    create_rename_dry_run,
    execute_rename_operation,
    get_rename_operation,
)

router = APIRouter(prefix="/api/rename-operations", tags=["rename-operations"])


class RenameDryRunRequest(BaseModel):
    """Safe rename dry-run request."""

    rename_preview_ids: list[int]


@router.post("/dry-run")
def create_dry_run(payload: RenameDryRunRequest, request: Request,
                   current_user=Depends(require_permission("rename:execute"))):
    """保留同步干跑契约，探测后重新核对发起者权限。"""

    try:
        return create_rename_dry_run(request.app.state.settings, payload.rename_preview_ids,
            auditContext=AuditWriteContext(current_user, request.client.host if request.client else None,
                                           request.headers.get("user-agent")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{operation_id}")
def get_operation(operation_id: int, request: Request):
    """Get a safe rename operation."""

    try:
        return get_rename_operation(request.app.state.settings, operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{operation_id}/execute")
def execute_operation(
    operation_id: int,
    request: Request,
    response: Response,
    current_user=Depends(require_permission("rename:execute")),
):
    """Execute a safe rename operation."""

    try:
        current = get_rename_operation(request.app.state.settings, operation_id)
        if current.status != "dry_run":
            return current
        result = runAuditedFileAction(
            request.app.state.settings, taskType="rename_operation", taskId=operation_id,
            context=AuditWriteContext(current_user, request.client.host if request.client else None, request.headers.get("user-agent")),
            execute=lambda: execute_rename_operation(request.app.state.settings, operation_id), response=response,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/{operation_id}/execute-async", status_code=202)
def accept_operation(operation_id: int, request: Request,
                     current_user=Depends(require_permission("rename:execute"))):
    """可靠受理后立即返回原批次，状态未知时查询此ID而不重新发起动作。"""
    try:
        return acceptRename(request.app.state.settings, operation_id,
            AuditWriteContext(current_user, request.client.host if request.client else None, request.headers.get("user-agent")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
