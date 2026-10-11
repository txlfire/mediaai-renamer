"""External submission block record API."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.service.audit_service import AuditWriteContext
from app.service.external_submission_guard import (
    list_external_submission_blocks,
    update_external_submission_block_decision,
)
from app.service.external_submission_authorization_service import (
    prepareExternalSubmission, submitExternalSubmission, getExternalSubmissionOutcome, getLatestExternalSubmissionOutcome,
)

router = APIRouter(prefix="/api/external-submission-blocks", tags=["external-submission-blocks"])


class UpdateExternalSubmissionBlockRequest(BaseModel):
    """External submission block user decision payload."""

    status: str
    user_decision: str | None = None
    override_reason: str | None = None


class SubmitExternalSubmissionRequest(BaseModel):
    authorization_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]+$")
    override_reason: str = Field(min_length=1, max_length=1000)


def _context(request, user):
    return AuditWriteContext(user, request.client.host if request.client else None, request.headers.get("user-agent"))


def _callAuthorization(operation, *args):
    try:
        return operation(*args)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{block_id}/prepare")
def prepare_external_submission(block_id: int, request: Request,
                                current_user=Depends(require_permission("metadata:submit"))):
    return _callAuthorization(prepareExternalSubmission, request.app.state.settings, block_id, _context(request, current_user))


@router.post("/{block_id}/submit")
def submit_external_submission(block_id: int, payload: SubmitExternalSubmissionRequest, request: Request,
                               response: Response, current_user=Depends(require_permission("metadata:submit"))):
    result = _callAuthorization(submitExternalSubmission, request.app.state.settings, block_id,
        payload.authorization_id, payload.override_reason, _context(request, current_user))
    if result["audit_pending"]:
        response.headers["X-MediaAI-Audit-State"] = "pending"
    return result


@router.get("/{block_id}/authorizations/{authorization_id}")
def get_external_submission_outcome(block_id: int, authorization_id: str, request: Request,
                                    current_user=Depends(require_permission("metadata:submit"))):
    return _callAuthorization(getExternalSubmissionOutcome, request.app.state.settings, block_id,
                              authorization_id, _context(request, current_user))


@router.get("/{block_id}/outcome")
def get_latest_external_submission_outcome(block_id: int, request: Request,
                                           current_user=Depends(require_permission("metadata:submit"))):
    return _callAuthorization(getLatestExternalSubmissionOutcome, request.app.state.settings, block_id,
                              _context(request, current_user))


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
            auditContext=AuditWriteContext(current_user, request.client.host if request.client else None,
                                           request.headers.get("user-agent")),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _record_payload(record)
