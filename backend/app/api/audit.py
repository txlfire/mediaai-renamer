"""审计日志 API。"""

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_permission
from app.service.audit_service import audit_event_page_to_dict, list_audit_events

router = APIRouter(prefix="/api/audit-events", tags=["audit"])


@router.get("")
def list_audit_events_api(
    request: Request,
    event_type: str | None = None,
    result: str | None = None,
    actor_name: str | None = None,
    target_type: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    page: int = 1,
    page_size: int = 50,
    _current_user=Depends(require_permission("audit:read")),
):
    """查询审计日志。"""

    return audit_event_page_to_dict(
        list_audit_events(
            request.app.state.settings,
            event_type=event_type,
            result=result,
            actor_name=actor_name,
            target_type=target_type,
            start_at=start_at,
            end_at=end_at,
            page=page,
            page_size=page_size,
        )
    )
