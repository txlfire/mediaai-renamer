"""任务治理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import AuditWriteContext
from app.service.audit_intent_retention_service import getAuditIntentCapacity
from app.service.file_action_audit_service import closeFileActionAuditIntent, listFileActionAuditIntents
from app.service.task_governance_service import (
    listTaskGovernancePage,
    list_task_governance_items,
    set_task_archived,
    task_governance_item_to_dict,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskArchivePayload(BaseModel):
    archived: bool
    reason: str | None = None


class AuditIntentClosePayload(BaseModel):
    reason: str
    evidence: str


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


@router.get("/page")
def list_tasks_page_api(
    request: Request, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="updated_at"), sort_order: str = Query(default="desc"),
    task_type: str | None = Query(default=None), status: str | None = Query(default=None),
    media_source_id: int | None = Query(default=None), start_at: str | None = Query(default=None),
    end_at: str | None = Query(default=None), include_archived: bool = Query(default=False),
    _current_user=Depends(require_permission("task:manage")),
):
    try:
        result = listTaskGovernancePage(request.app.state.settings, page=page, page_size=page_size,
            sort_by=sort_by, sort_order=sort_order, task_type=task_type, status=status,
            media_source_id=media_source_id, start_at=start_at, end_at=end_at,
            include_archived=include_archived)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "items": [task_governance_item_to_dict(item) for item in result["items"]]}


@router.get("/audit-intents/capacity")
def get_audit_intent_capacity_api(
    request: Request,
    _current_user=Depends(require_permission("audit:read")),
):
    return getAuditIntentCapacity(request.app.state.settings)


@router.get("/audit-intents")
def list_audit_intents_api(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    _current_user=Depends(require_permission("audit:read")),
):
    try:
        return listFileActionAuditIntents(
            request.app.state.settings,
            page=page,
            pageSize=page_size,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audit-intents/{intent_id}/close")
def close_audit_intent_api(
    intent_id: int,
    payload: AuditIntentClosePayload,
    request: Request,
    current_user=Depends(require_permission("audit:read")),
    _task_manager=Depends(require_permission("task:manage")),
):
    try:
        return closeFileActionAuditIntent(
            request.app.state.settings,
            intent_id,
            reason=payload.reason,
            evidence=payload.evidence,
            context=AuditWriteContext(
                current_user,
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            auditContext=AuditWriteContext(
                current_user,
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task_governance_item_to_dict(item)
