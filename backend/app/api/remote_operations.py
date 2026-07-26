"""远程文件操作查询与失败恢复 API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_authenticated_user, require_permission
from app.service.audit_service import record_audit_event
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


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


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
    current_user=Depends(require_permission("rename:execute")),
):
    """安全恢复单个远程操作。"""

    try:
        result = recover_remote_operation(
            request.app.state.settings,
            item_id,
            owner=current_user.username if current_user else None,
        )
    except (RemoteOperationRecoveryConflict, RemoteOperationLockConflict) as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="remote_operation.recover",
            action="recover_remote_operation",
            result="failed",
            summary=f"远程操作需要人工恢复：{item_id}",
            target_type="remote_operation",
            target_id=item_id,
            actor=current_user,
            detail={"item_id": item_id, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record_audit_event(
        request.app.state.settings,
        event_type="remote_operation.recover",
        action="recover_remote_operation",
        result="success",
        summary=f"恢复远程操作：{item_id}",
        target_type="remote_operation",
        target_id=item_id,
        actor=current_user,
        detail={"item_id": item_id, "action": result.action, "status": result.item.status},
        **_audit_request_context(request),
    )
    return result
