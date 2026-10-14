"""后台受理、状态和结构化结果查询；原业务同步API不变。"""

from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.auth import require_authenticated_user
from app.service.audit_service import AuditWriteContext
from app.service.background_job_service import acceptBackgroundJob, getBackgroundJob, listBackgroundJobs, BackgroundJobConflict

router = APIRouter(prefix="/api/background-jobs", tags=["background-jobs"])


class BackgroundJobRequest(BaseModel):
    """请求ID在提交前由客户端生成，响应不明确时只查询原ID。"""
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    kind: Literal["rename_dry_run", "rollback_dry_run", "metadata_batch", "ai_batch"]
    rename_preview_ids: list[StrictInt] = Field(default_factory=list, max_length=1000)
    plan_id: StrictInt | None = Field(default=None, gt=0)
    metadata_match_source: Literal["parsed_title", "original_file_name", "parent_folder_title"] = "parsed_title"
    mode: Literal["primary_only", "fallback", "parallel"] = "fallback"


@router.post("", status_code=202)
def accept_job(payload: BackgroundJobRequest, request: Request, user=Depends(require_authenticated_user())):
    try:
        return acceptBackgroundJob(request.app.state.settings, payload.kind, str(payload.request_id), payload.model_dump(),
            AuditWriteContext(user, request.client.host if request.client else None, request.headers.get("user-agent")))
    except BackgroundJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def recent_jobs(request: Request, user=Depends(require_authenticated_user())):
    return listBackgroundJobs(request.app.state.settings, user)


@router.get("/{request_id}")
def get_job(request_id: UUID, request: Request, user=Depends(require_authenticated_user())):
    return _get(request, str(request_id), user, False)


@router.get("/{request_id}/result")
def get_result(request_id: UUID, request: Request, user=Depends(require_authenticated_user())):
    return _get(request, str(request_id), user, True)


def _get(request, requestId, user, includeResult):
    try:
        return getBackgroundJob(request.app.state.settings, requestId, user, includeResult=includeResult)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
