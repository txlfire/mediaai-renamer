"""Safe rename operation API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_permission
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
    _current_user=Depends(require_permission("rename:execute")),
):
    """Execute a safe rename operation."""

    try:
        return execute_rename_operation(request.app.state.settings, operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
