"""任务治理聚合服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
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
    }


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _matches_filters(
    item: TaskGovernanceItem,
    *,
    task_type: str | None,
    status: str | None,
    media_source_id: int | None,
    start_at: str | None,
    end_at: str | None,
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
    limit: int = 200,
) -> list[TaskGovernanceItem]:
    """聚合扫描、重命名和回滚任务。"""

    if task_type and task_type not in TASK_TYPES:
        raise ValueError("任务类型不受支持")
    safe_limit = max(1, min(int(limit), 500))
    with closing(_connect(settings)) as connection:
        items = [
            *_scan_job_items(connection),
            *_rename_operation_items(connection),
            *_rollback_plan_items(connection),
        ]
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
        )
    ]
    filtered.sort(key=lambda item: (item.updated_at, item.task_type, item.task_id), reverse=True)
    return filtered[:safe_limit]
