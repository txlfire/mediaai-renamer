"""External submission block record API."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import require_permission
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
    return _record_payload(record)
