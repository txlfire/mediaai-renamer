"""任务治理聚合服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any

from app.core.config import AppSettings

TASK_TYPE_SCAN_JOB = "scan_job"
TASK_TYPE_RENAME_OPERATION = "rename_operation"
TASK_TYPE_ROLLBACK_PLAN = "rollback_plan"
TASK_TYPES = {TASK_TYPE_SCAN_JOB, TASK_TYPE_RENAME_OPERATION, TASK_TYPE_ROLLBACK_PLAN}


@dataclass(frozen=True)
class TaskGovernanceItem:
    """任务治理聚合项。"""

    id: str
    task_type: str
    task_id: int
    status: str
    title: str
    summary: str
    media_source_id: int | None
    media_source_name: str | None
    related_id: int | None
    related_type: str | None
    total_count: int
    success_count: int
    failed_count: int
    warning_count: int
    created_at: str
    updated_at: str
    log_task_type: str
    log_task_id: int
    target_route: str | None
    target_query: dict[str, str]
    archived: bool
    archived_at: str | None
    archived_by: str | None
    archive_reason: str | None


def task_governance_item_to_dict(item: TaskGovernanceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "task_type": item.task_type,
        "task_id": item.task_id,
        "status": item.status,
        "title": item.title,
        "summary": item.summary,
        "media_source_id": item.media_source_id,
        "media_source_name": item.media_source_name,
        "related_id": item.related_id,
        "related_type": item.related_type,
        "total_count": item.total_count,
        "success_count": item.success_count,
        "failed_count": item.failed_count,
        "warning_count": item.warning_count,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "log_task_type": item.log_task_type,
        "log_task_id": item.log_task_id,
        "target_route": item.target_route,
        "target_query": item.target_query,
        "archived": item.archived,
        "archived_at": item.archived_at,
        "archived_by": item.archived_by,
        "archive_reason": item.archive_reason,
    }


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _archive_map(connection: sqlite3.Connection) -> dict[tuple[str, int], sqlite3.Row]:
    rows = connection.execute("SELECT * FROM task_archives").fetchall()
    return {(str(row["task_type"]), int(row["task_id"])): row for row in rows}


def _with_archive_state(
    item: TaskGovernanceItem,
    archive_row: sqlite3.Row | None,
) -> TaskGovernanceItem:
    if archive_row is None:
        return item
    return TaskGovernanceItem(
        **{
            **item.__dict__,
            "archived": True,
            "archived_at": str(archive_row["archived_at"]),
            "archived_by": archive_row["archived_by"],
            "archive_reason": archive_row["reason"],
        }
    )


def _matches_filters(
    item: TaskGovernanceItem,
    *,
    task_type: str | None,
    status: str | None,
    media_source_id: int | None,
    start_at: str | None,
    end_at: str | None,
    include_archived: bool,
) -> bool:
    if task_type and item.task_type != task_type:
        return False
    if status and item.status != status:
        return False
    if media_source_id is not None and item.media_source_id != media_source_id:
        return False
    if start_at and item.created_at < start_at:
        return False
    if end_at and item.created_at > end_at:
        return False
    if item.archived and not include_archived:
        return False
    return True


def _scan_job_items(connection: sqlite3.Connection) -> list[TaskGovernanceItem]:
    rows = connection.execute(
        "SELECT sj.id, sj.media_source_id, ms.name AS media_source_name, sj.status, "
        "sj.scanned_count, sj.video_count, sj.warning_count, sj.error_message, "
        "sj.created_at, COALESCE(sj.ended_at, sj.started_at, sj.created_at) AS updated_at "
        "FROM scan_jobs sj "
        "LEFT JOIN media_sources ms ON ms.id = sj.media_source_id"
    ).fetchall()
    items: list[TaskGovernanceItem] = []
    for row in rows:
        status = str(row["status"])
        total_count = int(row["scanned_count"] or 0)
        success_count = int(row["video_count"] or 0)
        warning_count = int(row["warning_count"] or 0)
        failed_count = 1 if status in {"failed", "interrupted"} else 0
        media_source_id = int(row["media_source_id"]) if row["media_source_id"] is not None else None
        task_id = int(row["id"])
        items.append(
            TaskGovernanceItem(
                id=f"{TASK_TYPE_SCAN_JOB}:{task_id}",
                task_type=TASK_TYPE_SCAN_JOB,
                task_id=task_id,
                status=status,
                title=f"扫描任务 #{task_id}",
                summary=str(row["error_message"] or f"扫描 {total_count} 个文件，识别视频 {success_count} 个"),
                media_source_id=media_source_id,
                media_source_name=row["media_source_name"],
                related_id=media_source_id,
                related_type="media_source",
                total_count=total_count,
                success_count=success_count,
                failed_count=failed_count,
                warning_count=warning_count,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                log_task_type=TASK_TYPE_SCAN_JOB,
                log_task_id=task_id,
                target_route="scan-results",
                target_query={
                    "media_source_id": str(media_source_id) if media_source_id is not None else "",
                    "scan_job_id": str(task_id),
                },
                archived=False,
                archived_at=None,
                archived_by=None,
                archive_reason=None,
            )
        )
    return items


def _operation_media_source(connection: sqlite3.Connection, operation_id: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT MIN(ms.id) AS media_source_id, MIN(ms.name) AS media_source_name, "
        "MIN(mf.scan_job_id) AS scan_job_id "
        "FROM rename_operation_items roi "
        "JOIN rename_previews rp ON rp.id = roi.rename_preview_id "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE roi.operation_id = ?",
        (operation_id,),
    ).fetchone()


def _rename_operation_items(connection: sqlite3.Connection) -> list[TaskGovernanceItem]:
    rows = connection.execute(
        "SELECT id, status, total_count, ready_count, conflict_count, renamed_count, "
        "failed_count, created_at, updated_at FROM rename_operations"
    ).fetchall()
    items: list[TaskGovernanceItem] = []
    for row in rows:
        task_id = int(row["id"])
        source_row = _operation_media_source(connection, task_id)
        media_source_id = (
            int(source_row["media_source_id"])
            if source_row is not None and source_row["media_source_id"] is not None
            else None
        )
        scan_job_id = (
            int(source_row["scan_job_id"])
            if source_row is not None and source_row["scan_job_id"] is not None
            else None
        )
        target_query: dict[str, str] = {}
        if media_source_id is not None:
            target_query["media_source_id"] = str(media_source_id)
        if scan_job_id is not None:
            target_query["scan_job_id"] = str(scan_job_id)
        items.append(
            TaskGovernanceItem(
                id=f"{TASK_TYPE_RENAME_OPERATION}:{task_id}",
                task_type=TASK_TYPE_RENAME_OPERATION,
                task_id=task_id,
                status=str(row["status"]),
                title=f"重命名批次 #{task_id}",
                summary=(
                    f"共 {int(row['total_count'] or 0)} 条，已重命名 {int(row['renamed_count'] or 0)} 条，"
                    f"失败 {int(row['failed_count'] or 0)} 条，冲突 {int(row['conflict_count'] or 0)} 条"
                ),
                media_source_id=media_source_id,
                media_source_name=source_row["media_source_name"] if source_row is not None else None,
                related_id=scan_job_id,
                related_type="scan_job" if scan_job_id is not None else None,
                total_count=int(row["total_count"] or 0),
                success_count=int(row["renamed_count"] or 0),
                failed_count=int(row["failed_count"] or 0),
                warning_count=int(row["conflict_count"] or 0),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                log_task_type=TASK_TYPE_RENAME_OPERATION,
                log_task_id=task_id,
                target_route="rename-previews",
                target_query=target_query,
                archived=False,
                archived_at=None,
                archived_by=None,
                archive_reason=None,
            )
        )
    return items


def _rollback_plan_items(connection: sqlite3.Connection) -> list[TaskGovernanceItem]:
    rows = connection.execute(
        "SELECT id, operation_id, status, item_count, executable_count, conflict_count, "
        "created_by, created_at, updated_at FROM rename_rollback_plans"
    ).fetchall()
    items: list[TaskGovernanceItem] = []
    for row in rows:
        plan_id = int(row["id"])
        operation_id = int(row["operation_id"])
        source_row = _operation_media_source(connection, operation_id)
        media_source_id = (
            int(source_row["media_source_id"])
            if source_row is not None and source_row["media_source_id"] is not None
            else None
        )
        items.append(
            TaskGovernanceItem(
                id=f"{TASK_TYPE_ROLLBACK_PLAN}:{plan_id}",
                task_type=TASK_TYPE_ROLLBACK_PLAN,
                task_id=plan_id,
                status=str(row["status"]),
                title=f"回滚计划 #{plan_id}",
                summary=(
                    f"来源重命名批次 #{operation_id}，共 {int(row['item_count'] or 0)} 条，"
                    f"可执行/已执行 {int(row['executable_count'] or 0)} 条，冲突/失败 {int(row['conflict_count'] or 0)} 条"
                ),
                media_source_id=media_source_id,
                media_source_name=source_row["media_source_name"] if source_row is not None else None,
                related_id=operation_id,
                related_type=TASK_TYPE_RENAME_OPERATION,
                total_count=int(row["item_count"] or 0),
                success_count=int(row["executable_count"] or 0),
                failed_count=0,
                warning_count=int(row["conflict_count"] or 0),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                log_task_type=TASK_TYPE_ROLLBACK_PLAN,
                log_task_id=plan_id,
                target_route="rename-previews",
                target_query={},
                archived=False,
                archived_at=None,
                archived_by=None,
                archive_reason=None,
            )
        )
    return items


def list_task_governance_items(
    settings: AppSettings,
    *,
    task_type: str | None = None,
    status: str | None = None,
    media_source_id: int | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    include_archived: bool = False,
    limit: int = 200,
) -> list[TaskGovernanceItem]:
    """聚合扫描、重命名和回滚任务。"""

    if task_type and task_type not in TASK_TYPES:
        raise ValueError("任务类型不受支持")
    safe_limit = max(1, min(int(limit), 500))
    with closing(_connect(settings)) as connection:
        archives = _archive_map(connection)
        items = [
            *_scan_job_items(connection),
            *_rename_operation_items(connection),
            *_rollback_plan_items(connection),
        ]
    items = [_with_archive_state(item, archives.get((item.task_type, item.task_id))) for item in items]
    filtered = [
        item
        for item in items
        if _matches_filters(
            item,
            task_type=task_type,
            status=status,
            media_source_id=media_source_id,
            start_at=start_at,
            end_at=end_at,
            include_archived=include_archived,
        )
    ]
    filtered.sort(key=lambda item: (item.updated_at, item.task_type, item.task_id), reverse=True)
    return filtered[:safe_limit]


def set_task_archived(
    settings: AppSettings,
    *,
    task_type: str,
    task_id: int,
    archived: bool,
    archived_by: str | None = None,
    reason: str | None = None,
) -> TaskGovernanceItem:
    """归档或恢复任务治理展示；不删除任何任务和真实文件。"""

    if task_type not in TASK_TYPES:
        raise ValueError("任务类型不受支持")
    with closing(_connect(settings)) as connection:
        if not _task_exists(connection, task_type, task_id):
            raise ValueError("任务不存在")
        if archived:
            connection.execute(
                "INSERT INTO task_archives (task_type, task_id, archived_at, archived_by, reason) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(task_type, task_id) DO UPDATE SET "
                "archived_at = excluded.archived_at, "
                "archived_by = excluded.archived_by, "
                "reason = excluded.reason",
                (task_type, task_id, _utc_now_text(), archived_by, reason),
            )
        else:
            connection.execute(
                "DELETE FROM task_archives WHERE task_type = ? AND task_id = ?",
                (task_type, task_id),
            )
        connection.commit()

    item = next(
        (
            row
            for row in list_task_governance_items(
                settings,
                task_type=task_type,
                include_archived=True,
                limit=500,
            )
            if row.task_id == task_id
        ),
        None,
    )
    if item is None:
        raise ValueError("任务不存在")
    return item
