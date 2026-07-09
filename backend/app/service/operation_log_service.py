"""任务运行日志服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.service.audit_service import sanitize_audit_detail
from app.service.settings_service import get_effective_settings

TASK_TYPE_SCAN_JOB = "scan_job"
TASK_TYPE_RENAME_OPERATION = "rename_operation"
TASK_TYPE_ROLLBACK_PLAN = "rollback_plan"
LEVEL_DEBUG = "debug"
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"
LEVEL_SUCCESS = "success"
PROTECTED_LEVELS = (LEVEL_WARNING, LEVEL_ERROR, LEVEL_SUCCESS)

logger = get_logger(__name__)


@dataclass(frozen=True)
class OperationLogItem:
    """单条任务运行日志。"""

    id: int
    task_type: str
    task_id: int
    level: str
    stage: str
    message: str
    progress_current: int | None
    progress_total: int | None
    detail: dict[str, Any] | list[Any] | None
    approx_bytes: int
    created_at: str


@dataclass(frozen=True)
class OperationLogPage:
    """任务运行日志轮询结果。"""

    items: list[OperationLogItem]
    latest_id: int
    total: int
    log_available: bool
    cleared: bool
    running: bool
    message: str | None


@dataclass(frozen=True)
class OperationLogCleanupSummary:
    """任务运行日志清理结果。"""

    deleted: int
    bytes_deleted: int


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _setting_int(values: dict[str, object], key: str, default: int) -> int:
    try:
        return int(values.get(key) or default)
    except (TypeError, ValueError):
        return default


def _row_to_operation_log(row: sqlite3.Row) -> OperationLogItem:
    detail_json = row["detail_json"]
    detail: dict[str, Any] | list[Any] | None = None
    if detail_json:
        try:
            parsed = json.loads(str(detail_json))
            if isinstance(parsed, (dict, list)):
                detail = parsed
        except json.JSONDecodeError:
            detail = {"raw": str(detail_json)}
    return OperationLogItem(
        id=int(row["id"]),
        task_type=str(row["task_type"]),
        task_id=int(row["task_id"]),
        level=str(row["level"]),
        stage=str(row["stage"]),
        message=str(row["message"]),
        progress_current=int(row["progress_current"]) if row["progress_current"] is not None else None,
        progress_total=int(row["progress_total"]) if row["progress_total"] is not None else None,
        detail=detail,
        approx_bytes=int(row["approx_bytes"]),
        created_at=str(row["created_at"]),
    )


def operation_log_to_dict(item: OperationLogItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "task_type": item.task_type,
        "task_id": item.task_id,
        "level": item.level,
        "stage": item.stage,
        "message": item.message,
        "progress_current": item.progress_current,
        "progress_total": item.progress_total,
        "detail": item.detail,
        "approx_bytes": item.approx_bytes,
        "created_at": item.created_at,
    }


def operation_log_page_to_dict(page: OperationLogPage) -> dict[str, Any]:
    return {
        "items": [operation_log_to_dict(item) for item in page.items],
        "latest_id": page.latest_id,
        "total": page.total,
        "log_available": page.log_available,
        "cleared": page.cleared,
        "running": page.running,
        "message": page.message,
    }


def _task_exists(connection: sqlite3.Connection, task_type: str, task_id: int) -> bool:
    if task_type == TASK_TYPE_SCAN_JOB:
        table = "scan_jobs"
    elif task_type == TASK_TYPE_RENAME_OPERATION:
        table = "rename_operations"
    elif task_type == TASK_TYPE_ROLLBACK_PLAN:
        table = "rename_rollback_plans"
    else:
        return False
    row = connection.execute(f"SELECT 1 FROM {table} WHERE id = ?", (task_id,)).fetchone()
    return row is not None


def _task_running(connection: sqlite3.Connection, task_type: str, task_id: int) -> bool:
    if task_type == TASK_TYPE_SCAN_JOB:
        row = connection.execute("SELECT status FROM scan_jobs WHERE id = ?", (task_id,)).fetchone()
        return bool(row and row["status"] == "running")
    return False


def record_operation_log(
    settings: AppSettings,
    *,
    task_type: str,
    task_id: int,
    level: str,
    stage: str,
    message: str,
    progress_current: int | None = None,
    progress_total: int | None = None,
    detail: dict[str, Any] | list[Any] | None = None,
    connection: sqlite3.Connection | None = None,
) -> OperationLogItem | None:
    """写入任务运行日志；失败时只记录应用日志，不中断主流程。"""

    now = _utc_now_text()
    detail_json = (
        json.dumps(sanitize_audit_detail(detail), ensure_ascii=False, separators=(",", ":"))
        if detail is not None
        else None
    )
    approx_bytes = len(message.encode("utf-8")) + (len(detail_json.encode("utf-8")) if detail_json else 0)
    owns_connection = connection is None
    target_connection = connection or _connect(settings)
    try:
        cursor = target_connection.execute(
            "INSERT INTO operation_logs "
            "(task_type, task_id, level, stage, message, progress_current, progress_total, "
            "detail_json, approx_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_type,
                task_id,
                level,
                stage,
                message,
                progress_current,
                progress_total,
                detail_json,
                approx_bytes,
                now,
            ),
        )
        if owns_connection:
            target_connection.commit()
        row = target_connection.execute(
            "SELECT * FROM operation_logs WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        item = _row_to_operation_log(row)
        if owns_connection:
            cleanup_operation_logs(settings)
        return item
    except Exception as exc:  # noqa: BLE001 - 运行日志不能影响主业务。
        if owns_connection:
            target_connection.rollback()
        logger.warning("写入任务运行日志失败: %s", exc)
        return None
    finally:
        if owns_connection:
            target_connection.close()


def list_operation_logs(
    settings: AppSettings,
    *,
    task_type: str,
    task_id: int,
    after_id: int = 0,
    limit: int = 100,
) -> OperationLogPage:
    """按任务查询运行日志，支持增量轮询。"""

    safe_limit = max(1, min(int(limit), 500))
    with closing(_connect(settings)) as connection:
        total_row = connection.execute(
            "SELECT COUNT(*) AS total, COALESCE(MAX(id), 0) AS latest_id "
            "FROM operation_logs WHERE task_type = ? AND task_id = ?",
            (task_type, task_id),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM operation_logs "
            "WHERE task_type = ? AND task_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (task_type, task_id, max(0, int(after_id)), safe_limit),
        ).fetchall()
        total = int(total_row["total"]) if total_row else 0
        latest_id = int(total_row["latest_id"]) if total_row else 0
        exists = _task_exists(connection, task_type, task_id)
        running = _task_running(connection, task_type, task_id)

    message: str | None = None
    cleared = False
    if total == 0:
        if exists:
            cleared = True
            message = "该任务暂无运行日志，或日志已按保留策略清理。"
        else:
            message = "任务不存在或日志不可用。"

    return OperationLogPage(
        items=[_row_to_operation_log(row) for row in rows],
        latest_id=latest_id,
        total=total,
        log_available=total > 0,
        cleared=cleared,
        running=running,
        message=message,
    )


def export_operation_logs_text(settings: AppSettings, *, task_type: str, task_id: int) -> str:
    """导出指定任务的运行日志文本。"""

    with closing(_connect(settings)) as connection:
        rows = connection.execute(
            "SELECT * FROM operation_logs WHERE task_type = ? AND task_id = ? ORDER BY id",
            (task_type, task_id),
        ).fetchall()
    if not rows:
        return "该任务暂无运行日志，或日志已按保留策略清理。\n"
    lines = []
    for row in rows:
        item = _row_to_operation_log(row)
        lines.append(f"[{item.created_at}] [{item.level.upper()}] [{item.stage}] {item.message}")
    return "\n".join(lines) + "\n"


def _delete_selected_rows(
    connection: sqlite3.Connection,
    where_sql: str,
    params: tuple[object, ...],
    *,
    bytes_to_delete: int,
    batch_size: int,
) -> tuple[int, int]:
    rows = connection.execute(
        "SELECT id, approx_bytes FROM operation_logs "
        f"WHERE {where_sql} ORDER BY id ASC LIMIT ?",
        (*params, batch_size),
    ).fetchall()
    selected_ids: list[int] = []
    selected_bytes = 0
    for row in rows:
        selected_ids.append(int(row["id"]))
        selected_bytes += int(row["approx_bytes"])
        if selected_bytes >= bytes_to_delete:
            break
    if not selected_ids:
        return 0, 0
    placeholders = ",".join("?" for _ in selected_ids)
    connection.execute(
        f"DELETE FROM operation_logs WHERE id IN ({placeholders})",
        tuple(selected_ids),
    )
    return len(selected_ids), selected_bytes


def _trim_by_size(
    connection: sqlite3.Connection,
    *,
    where_sql: str,
    params: tuple[object, ...],
    max_bytes: int,
    batch_size: int,
) -> tuple[int, int]:
    deleted = 0
    bytes_deleted = 0
    while True:
        row = connection.execute(
            f"SELECT COALESCE(SUM(approx_bytes), 0) AS total_bytes FROM operation_logs WHERE {where_sql}",
            params,
        ).fetchone()
        total_bytes = int(row["total_bytes"]) if row else 0
        if total_bytes <= max_bytes:
            break
        excess = total_bytes - max_bytes
        count, size = _delete_selected_rows(
            connection,
            f"{where_sql} AND level NOT IN (?, ?, ?)",
            (*params, *PROTECTED_LEVELS),
            bytes_to_delete=excess,
            batch_size=batch_size,
        )
        if count == 0:
            count, size = _delete_selected_rows(
                connection,
                where_sql,
                params,
                bytes_to_delete=excess,
                batch_size=batch_size,
            )
        if count == 0:
            break
        deleted += count
        bytes_deleted += size
    return deleted, bytes_deleted


def cleanup_operation_logs(settings: AppSettings) -> OperationLogCleanupSummary:
    """按保留天数、全局大小和单任务大小清理运行日志。"""

    effective_settings = get_effective_settings(settings)
    retention_days = _setting_int(effective_settings, "logging.retention_days", 30)
    max_total_bytes = _setting_int(effective_settings, "operation_logs.max_total_mb", 128) * 1024 * 1024
    max_task_bytes = _setting_int(effective_settings, "operation_logs.max_task_mb", 16) * 1024 * 1024
    batch_size = _setting_int(effective_settings, "operation_logs.cleanup_batch_size", 1000)
    deleted = 0
    bytes_deleted = 0

    with closing(_connect(settings)) as connection:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        old_rows = connection.execute(
            "SELECT id, approx_bytes FROM operation_logs WHERE created_at < ? LIMIT ?",
            (cutoff, batch_size),
        ).fetchall()
        if old_rows:
            old_ids = [int(row["id"]) for row in old_rows]
            placeholders = ",".join("?" for _ in old_ids)
            connection.execute(f"DELETE FROM operation_logs WHERE id IN ({placeholders})", tuple(old_ids))
            deleted += len(old_ids)
            bytes_deleted += sum(int(row["approx_bytes"]) for row in old_rows)

        total_deleted, total_bytes = _trim_by_size(
            connection,
            where_sql="1 = 1",
            params=(),
            max_bytes=max_total_bytes,
            batch_size=batch_size,
        )
        deleted += total_deleted
        bytes_deleted += total_bytes

        task_rows = connection.execute(
            "SELECT task_type, task_id FROM operation_logs "
            "GROUP BY task_type, task_id HAVING COALESCE(SUM(approx_bytes), 0) > ?",
            (max_task_bytes,),
        ).fetchall()
        for row in task_rows:
            task_deleted, task_bytes = _trim_by_size(
                connection,
                where_sql="task_type = ? AND task_id = ?",
                params=(str(row["task_type"]), int(row["task_id"])),
                max_bytes=max_task_bytes,
                batch_size=batch_size,
            )
            deleted += task_deleted
            bytes_deleted += task_bytes

        connection.commit()

    return OperationLogCleanupSummary(deleted=deleted, bytes_deleted=bytes_deleted)
