"""任务治理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import record_audit_event
from app.service.task_governance_service import (
    list_task_governance_items,
    set_task_archived,
    task_governance_item_to_dict,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskArchivePayload(BaseModel):
    archived: bool
    reason: str | None = None


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.get("")
def list_tasks_api(
    request: Request,
    task_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    media_source_id: int | None = Query(default=None),
    start_at: str | None = Query(default=None),
    end_at: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    _current_user=Depends(require_permission("task:manage")),
):
    """统一查询扫描、重命名和回滚任务。"""

    try:
        items = list_task_governance_items(
            request.app.state.settings,
            task_type=task_type,
            status=status,
            media_source_id=media_source_id,
            start_at=start_at,
            end_at=end_at,
            include_archived=include_archived,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [task_governance_item_to_dict(item) for item in items]}


@router.patch("/{task_type}/{task_id}/archive")
def archive_task_api(
    task_type: str,
    task_id: int,
    payload: TaskArchivePayload,
    request: Request,
    current_user=Depends(require_permission("task:manage")),
):
    """归档或恢复任务治理展示，不删除真实任务和文件。"""

    try:
        item = set_task_archived(
            request.app.state.settings,
            task_type=task_type,
            task_id=task_id,
            archived=payload.archived,
            archived_by=current_user.username if current_user else None,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    action = "archive_task" if payload.archived else "restore_task"
    record_audit_event(
        request.app.state.settings,
        event_type="task.governance",
        action=action,
        result="success",
        summary=("归档任务" if payload.archived else "恢复任务") + f"：{task_type}#{task_id}",
        target_type=task_type,
        target_id=str(task_id),
        actor=current_user,
        detail={
            "task_type": task_type,
            "task_id": task_id,
            "archived": payload.archived,
            "reason": payload.reason,
        },
        **_audit_request_context(request),
    )
    return task_governance_item_to_dict(item)
