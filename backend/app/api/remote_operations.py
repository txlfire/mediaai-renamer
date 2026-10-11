"""远程文件操作查询与失败恢复 API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.auth import require_authenticated_user
from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import runAuditedFileAction
from app.service.remote_operation_recovery_service import (
    RemoteOperationRecoveryConflict,
    recover_remote_operation,
)
from app.service.remote_operation_service import (
    RemoteOperationLockConflict,
    get_remote_operation_item,
    list_remote_operation_items,
)

router = APIRouter(prefix="/api/remote-operations", tags=["remote-operations"])


@router.get("")
def list_remote_operations_api(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    media_source_id: int | None = Query(None, ge=1),
    operation_type: Literal["rename", "rollback"] | None = None,
    status: Literal[
        "pending",
        "recovering",
        "remote_applied",
        "completed",
        "failed",
        "recovery_required",
    ]
    | None = None,
    _current_user=Depends(require_authenticated_user()),
):
    """分页查询远程操作明细。"""

    return list_remote_operation_items(
        request.app.state.settings,
        page=page,
        page_size=page_size,
        media_source_id=media_source_id,
        operation_type=operation_type,
        status=status,
    )


@router.get("/{item_id}")
def get_remote_operation_api(
    item_id: int,
    request: Request,
    _current_user=Depends(require_authenticated_user()),
):
    """查询远程操作明细。"""

    try:
        return get_remote_operation_item(request.app.state.settings, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{item_id}/recover")
def recover_remote_operation_api(
    item_id: int,
    request: Request,
    response: Response,
    current_user=Depends(require_authenticated_user()),
):
    """安全恢复单个远程操作。"""

    try:
        item = get_remote_operation_item(request.app.state.settings, item_id)
        permission = "rollback:execute" if item.operation_type == "rollback" else "rename:execute"
        if permission not in current_user.permissions:
            raise HTTPException(status_code=403, detail=f"权限不足：需要 {permission}")
        result = runAuditedFileAction(
            request.app.state.settings, taskType="remote_operation_recovery", taskId=item_id,
            context=AuditWriteContext(current_user, request.client.host if request.client else None,
                                      request.headers.get("user-agent")), response=response,
            execute=lambda: recover_remote_operation(request.app.state.settings, item_id, owner=current_user.username),
        )
    except (RemoteOperationRecoveryConflict, RemoteOperationLockConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result
