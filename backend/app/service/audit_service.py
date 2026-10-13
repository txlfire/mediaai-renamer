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
from app.utils.redaction import redactUrlCredentials

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


@dataclass(frozen=True)
class AuditWriteContext:
    """显式传入服务的操作人上下文，不依赖跨线程隐式请求状态。"""

    actor: AuthUser | None = None
    ipAddress: str | None = None
    userAgent: str | None = None


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def executionAuditResult(status: str, *, succeededCount: int | None = None,
                         failedCount: int = 0, conflictCount: int = 0) -> str:
    """HTTP 调用成功不等于业务执行全部成功。"""
    if status in {"running", "dry_run", "draft", "checked"}:
        return "pending"
    if failedCount or conflictCount:
        return "partial_success" if succeededCount else "failed"
    if status in {"completed", "executed"}:
        return "success"
    if status == "partial_failed":
        return "partial_success"
    return "failed"


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
    if isinstance(value, str):
        return redactUrlCredentials(value)
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
    connection: sqlite3.Connection | None = None,
) -> AuditEvent:
    """写入安全审计；借用连接时由调用者与业务同事务提交，不降级吞错。"""

    now = _utc_now_text()
    resolved_actor_id = actor.id if actor is not None else actor_id
    resolved_actor_name = actor.username if actor is not None else actor_name
    detail_json = (
        json.dumps(sanitize_audit_detail(detail), ensure_ascii=False)
        if detail is not None
        else None
    )
    ownsConnection = connection is None
    targetConnection = connection if connection is not None else _connect(settings)
    try:
        cursor = targetConnection.execute(
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
                redactUrlCredentials(summary),
                detail_json,
                ip_address,
                user_agent,
                now,
            ),
        )
        if ownsConnection:
            targetConnection.commit()
        resultCursor = targetConnection.cursor()
        resultCursor.row_factory = sqlite3.Row
        row = resultCursor.execute(
            "SELECT * FROM audit_events WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        return _row_to_audit_event(row)
    finally:
        if ownsConnection:
            targetConnection.close()


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
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN")
        total = int(
            connection.execute(
                f"SELECT COUNT(*) AS total FROM audit_events {where_sql}",
                tuple(params),
            ).fetchone()["total"]
        )
        normalized_page = min(normalized_page, max(1, (total + normalized_page_size - 1) // normalized_page_size))
        offset = (normalized_page - 1) * normalized_page_size
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
        summary=redactUrlCredentials(str(row["summary"])),
        detail=sanitize_audit_detail(detail),
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        created_at=str(row["created_at"]),
    )
