"""Safe rename operation API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import record_audit_event
from app.service.rename_operation_service import (
    create_rename_dry_run,
    execute_rename_operation,
    get_rename_operation,
)

router = APIRouter(prefix="/api/rename-operations", tags=["rename-operations"])


class RenameDryRunRequest(BaseModel):
    """Safe rename dry-run request."""

    rename_preview_ids: list[int]


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.post("/dry-run")
def create_dry_run(payload: RenameDryRunRequest, request: Request):
    """Create a safe rename dry-run operation."""

    try:
        return create_rename_dry_run(request.app.state.settings, payload.rename_preview_ids)
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
    current_user=Depends(require_permission("rename:execute")),
):
    """Execute a safe rename operation."""

    try:
        result = execute_rename_operation(request.app.state.settings, operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="rename.execute",
        action="execute_rename_operation",
        result="success",
        summary=f"执行重命名批次：{operation_id}",
        target_type="rename_operation",
        target_id=operation_id,
        actor=current_user,
        detail={
            "operation_id": operation_id,
            "status": result.get("status") if isinstance(result, dict) else None,
            "item_count": len(result.get("items", [])) if isinstance(result, dict) else None,
        },
        **_audit_request_context(request),
    )
    return result
