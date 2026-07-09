"""任务治理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_permission
from app.service.task_governance_service import (
    list_task_governance_items,
    task_governance_item_to_dict,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks_api(
    request: Request,
    task_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    media_source_id: int | None = Query(default=None),
    start_at: str | None = Query(default=None),
    end_at: str | None = Query(default=None),
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
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [task_governance_item_to_dict(item) for item in items]}
