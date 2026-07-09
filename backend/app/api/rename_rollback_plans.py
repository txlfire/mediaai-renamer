"""重命名回滚计划 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_authenticated_user, require_permission
from app.service.audit_service import record_audit_event
from app.service.rename_rollback_service import (
    create_rename_rollback_plan,
    dry_run_rename_rollback_plan,
    execute_rename_rollback_plan,
    get_rename_rollback_plan,
    list_rename_rollback_plans,
)

router = APIRouter(prefix="/api", tags=["rename-rollback"])


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.post("/rename-operations/{operation_id}/rollback-plan", status_code=201)
def create_rollback_plan_api(
    operation_id: int,
    request: Request,
    current_user=Depends(require_permission("rollback:execute")),
):
    """基于重命名批次创建回滚计划。"""

    try:
        plan = create_rename_rollback_plan(
            request.app.state.settings,
            operation_id,
            created_by=current_user.username if current_user else None,
        )
    except ValueError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="rename.rollback",
            action="create_rollback_plan",
            result="failed",
            summary=f"创建回滚计划失败：{operation_id}",
            target_type="rename_operation",
            target_id=operation_id,
            actor=current_user,
            detail={"operation_id": operation_id, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="rename.rollback",
        action="create_rollback_plan",
        result="success",
        summary=f"创建回滚计划：{plan.id}",
        target_type="rollback_plan",
        target_id=plan.id,
        actor=current_user,
        detail={"operation_id": operation_id, "plan_id": plan.id, "item_count": plan.item_count},
        **_audit_request_context(request),
    )
    return plan


@router.get("/rename-rollback-plans")
def list_rollback_plans_api(
    request: Request,
    operation_id: int | None = None,
    _current_user=Depends(require_authenticated_user()),
):
    """查询回滚计划列表。"""

    return list_rename_rollback_plans(request.app.state.settings, operation_id=operation_id)


@router.get("/rename-rollback-plans/{plan_id}")
def get_rollback_plan_api(
    plan_id: int,
    request: Request,
    _current_user=Depends(require_authenticated_user()),
):
    """查询回滚计划详情。"""

    try:
        return get_rename_rollback_plan(request.app.state.settings, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rename-rollback-plans/{plan_id}/dry-run")
def dry_run_rollback_plan_api(
    plan_id: int,
    request: Request,
    current_user=Depends(require_permission("rollback:execute")),
):
    """执行回滚 dry-run。"""

    try:
        plan = dry_run_rename_rollback_plan(request.app.state.settings, plan_id)
    except ValueError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="rename.rollback",
            action="dry_run_rollback_plan",
            result="failed",
            summary=f"回滚 dry-run 失败：{plan_id}",
            target_type="rollback_plan",
            target_id=plan_id,
            actor=current_user,
            detail={"plan_id": plan_id, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="rename.rollback",
        action="dry_run_rollback_plan",
        result="success",
        summary=f"回滚 dry-run：{plan_id}",
        target_type="rollback_plan",
        target_id=plan_id,
        actor=current_user,
        detail={
            "plan_id": plan_id,
            "status": plan.status,
            "executable_count": plan.executable_count,
            "conflict_count": plan.conflict_count,
        },
        **_audit_request_context(request),
    )
    return plan


@router.post("/rename-rollback-plans/{plan_id}/execute")
def execute_rollback_plan_api(
    plan_id: int,
    request: Request,
    current_user=Depends(require_permission("rollback:execute")),
):
    """执行回滚计划。"""

    try:
        plan = execute_rename_rollback_plan(request.app.state.settings, plan_id)
    except ValueError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="rename.rollback",
            action="execute_rollback_plan",
            result="failed",
            summary=f"执行回滚失败：{plan_id}",
            target_type="rollback_plan",
            target_id=plan_id,
            actor=current_user,
            detail={"plan_id": plan_id, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="rename.rollback",
        action="execute_rollback_plan",
        result="success",
        summary=f"执行回滚计划：{plan_id}",
        target_type="rollback_plan",
        target_id=plan_id,
        actor=current_user,
        detail={
            "plan_id": plan_id,
            "status": plan.status,
            "item_count": plan.item_count,
            "executable_count": plan.executable_count,
            "conflict_count": plan.conflict_count,
        },
        **_audit_request_context(request),
    )
    return plan
