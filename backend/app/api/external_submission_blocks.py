"""External submission block record API."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_permission
from app.service.audit_service import record_audit_event
from app.service.external_submission_guard import (
    list_external_submission_blocks,
    update_external_submission_block_decision,
)

router = APIRouter(prefix="/api/external-submission-blocks", tags=["external-submission-blocks"])


class UpdateExternalSubmissionBlockRequest(BaseModel):
    """External submission block user decision payload."""

    status: str
    user_decision: str | None = None
    override_reason: str | None = None


def _record_payload(record):
    return asdict(record)


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.get("")
def get_external_submission_blocks(
    request: Request,
    status: str | None = None,
    target_service: str | None = None,
    limit: int = 200,
):
    """List external submission block records."""

    result = list_external_submission_blocks(
        request.app.state.settings,
        status=status,
        target_service=target_service,
        limit=limit,
    )
    return {
        "items": [_record_payload(item) for item in result.items],
        "total": result.total,
    }


@router.patch("/{block_id}")
def update_external_submission_block(
    block_id: int,
    payload: UpdateExternalSubmissionBlockRequest,
    request: Request,
    current_user=Depends(require_permission("metadata:submit")),
):
    """Update one external submission block decision."""

    try:
        record = update_external_submission_block_decision(
            request.app.state.settings,
            block_id,
            status=payload.status,
            user_decision=payload.user_decision,
            override_reason=payload.override_reason,
            operator=current_user.username if current_user is not None else "system",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="external_submission.decision",
        action="update_external_submission_block",
        result="success",
        summary=f"处理外部提交拦截：{payload.status}",
        target_type="external_submission_block",
        target_id=block_id,
        actor=current_user,
        detail={
            "status": payload.status,
            "user_decision": payload.user_decision,
            "override_reason": payload.override_reason,
            "target_service": record.target_service,
        },
        **_audit_request_context(request),
    )
    return _record_payload(record)
