"""审计事件服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from app.core.config import AppSettings
from app.service.auth_service import AuthUser

SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
    "v4_token",
)


@dataclass(frozen=True)
class AuditEvent:
    """审计事件。"""

    id: int
    event_type: str
    actor_id: int | None
    actor_name: str | None
    target_type: str
    target_id: str | None
    action: str
    result: str
    summary: str
    detail: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: str


@dataclass(frozen=True)
class AuditEventPage:
    """审计事件分页结果。"""

    items: list[AuditEvent]
    total: int
    page: int
    page_size: int


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def sanitize_audit_detail(value: Any) -> Any:
    """递归脱敏审计详情。"""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(keyword in lowered for keyword in SENSITIVE_KEYWORDS):
                sanitized[key_text] = "******" if item not in (None, "") else item
            else:
                sanitized[key_text] = sanitize_audit_detail(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_audit_detail(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_detail(item) for item in value]
    return value


def record_audit_event(
    settings: AppSettings,
    *,
    event_type: str,
    action: str,
    result: str,
    summary: str,
    target_type: str = "system",
    target_id: str | int | None = None,
    actor: AuthUser | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditEvent:
    """写入审计事件并返回记录。"""

    now = _utc_now_text()
    resolved_actor_id = actor.id if actor is not None else actor_id
    resolved_actor_name = actor.username if actor is not None else actor_name
    detail_json = (
        json.dumps(sanitize_audit_detail(detail), ensure_ascii=False)
        if detail is not None
        else None
    )
    with closing(_connect(settings)) as connection:
        cursor = connection.execute(
            "INSERT INTO audit_events "
            "(event_type, actor_id, actor_name, target_type, target_id, action, result, "
            "summary, detail_json, ip_address, user_agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                resolved_actor_id,
                resolved_actor_name,
                target_type,
                str(target_id) if target_id is not None else None,
                action,
                result,
                summary,
                detail_json,
                ip_address,
                user_agent,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM audit_events WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _row_to_audit_event(row)


def list_audit_events(
    settings: AppSettings,
    *,
    event_type: str | None = None,
    result: str | None = None,
    actor_name: str | None = None,
    target_type: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AuditEventPage:
    """按条件查询审计事件。"""

    normalized_page = max(1, int(page))
    normalized_page_size = min(200, max(1, int(page_size)))
    where_parts: list[str] = []
    params: list[object] = []
    if event_type:
        where_parts.append("event_type = ?")
        params.append(event_type)
    if result:
        where_parts.append("result = ?")
        params.append(result)
    if actor_name:
        where_parts.append("actor_name LIKE ?")
        params.append(f"%{actor_name}%")
    if target_type:
        where_parts.append("target_type = ?")
        params.append(target_type)
    if start_at:
        where_parts.append("created_at >= ?")
        params.append(start_at)
    if end_at:
        where_parts.append("created_at <= ?")
        params.append(end_at)
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    offset = (normalized_page - 1) * normalized_page_size
    with closing(_connect(settings)) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) AS total FROM audit_events {where_sql}",
                tuple(params),
            ).fetchone()["total"]
        )
        rows = connection.execute(
            "SELECT * FROM audit_events "
            f"{where_sql} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [normalized_page_size, offset]),
        ).fetchall()
    return AuditEventPage(
        items=[_row_to_audit_event(row) for row in rows],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


def audit_event_to_dict(event: AuditEvent) -> dict[str, Any]:
    """转换审计事件为 API 响应。"""

    return {
        "id": event.id,
        "eventType": event.event_type,
        "actorId": event.actor_id,
        "actorName": event.actor_name,
        "targetType": event.target_type,
        "targetId": event.target_id,
        "action": event.action,
        "result": event.result,
        "summary": event.summary,
        "detail": event.detail,
        "ipAddress": event.ip_address,
        "userAgent": event.user_agent,
        "createdAt": event.created_at,
    }


def audit_event_page_to_dict(page: AuditEventPage) -> dict[str, Any]:
    """转换分页结果为 API 响应。"""

    return {
        "items": [audit_event_to_dict(item) for item in page.items],
        "total": page.total,
        "page": page.page,
        "pageSize": page.page_size,
    }


def _row_to_audit_event(row: sqlite3.Row) -> AuditEvent:
    detail = None
    raw_detail = row["detail_json"]
    if raw_detail:
        try:
            parsed = json.loads(str(raw_detail))
            detail = parsed if isinstance(parsed, dict) else {"value": parsed}
        except ValueError:
            detail = {"raw": str(raw_detail)}
    return AuditEvent(
        id=int(row["id"]),
        event_type=str(row["event_type"]),
        actor_id=int(row["actor_id"]) if row["actor_id"] is not None else None,
        actor_name=row["actor_name"],
        target_type=str(row["target_type"]),
        target_id=row["target_id"],
        action=str(row["action"]),
        result=str(row["result"]),
        summary=str(row["summary"]),
        detail=detail,
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        created_at=str(row["created_at"]),
    )
