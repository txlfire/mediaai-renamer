"""任务治理聚合服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any

from app.core.config import AppSettings
from app.service.audit_service import AuditWriteContext, record_audit_event

TASK_TYPE_SCAN_JOB = "scan_job"
TASK_TYPE_RENAME_OPERATION = "rename_operation"
TASK_TYPE_ROLLBACK_PLAN = "rollback_plan"
TASK_TYPE_PENDING_MOVE = "pending_move"
TASK_TYPES = {TASK_TYPE_SCAN_JOB, TASK_TYPE_RENAME_OPERATION, TASK_TYPE_ROLLBACK_PLAN, TASK_TYPE_PENDING_MOVE, "background_job"}


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
    elif task_type == TASK_TYPE_PENDING_MOVE:
        table = "pending_move_tasks"
    elif task_type == "background_job":
        table = "background_jobs"
    else:
        return False
    row = connection.execute(f"SELECT 1 FROM {table} WHERE id = ?", (task_id,)).fetchone()
    return row is not None


def _archive_map(connection: sqlite3.Connection, selected: list[tuple[str, int]]) -> dict[tuple[str, int], sqlite3.Row]:
    if not selected:
        return {}
    rows = connection.execute("SELECT * FROM task_archives WHERE " + " OR ".join(
        "(task_type=? AND task_id=?)" for _ in selected),
        [value for pair in selected for value in pair]).fetchall()
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


def _selected_clause(ids: list[int]) -> str:
    return " WHERE id IN (" + ",".join("?" for _ in ids) + ")"


def _scan_job_items(connection: sqlite3.Connection, ids: list[int]) -> list[TaskGovernanceItem]:
    if not ids:
        return []
    rows = connection.execute(
        "SELECT sj.id, sj.media_source_id, ms.name AS media_source_name, sj.status, "
        "sj.scanned_count, sj.video_count, sj.warning_count, sj.error_message, "
        "sj.created_at, COALESCE(sj.ended_at, sj.started_at, sj.created_at) AS updated_at "
        "FROM scan_jobs sj "
        "LEFT JOIN media_sources ms ON ms.id = sj.media_source_id "
        "WHERE sj.id IN (" + ",".join("?" for _ in ids) + ")", ids
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


def _operation_media_sources(connection: sqlite3.Connection, operationIds: list[int]) -> dict[int, sqlite3.Row]:
    if not operationIds:
        return {}
    rows = connection.execute(
        "SELECT roi.operation_id, MIN(ms.id) AS media_source_id, MIN(ms.name) AS media_source_name, "
        "MIN(mf.scan_job_id) AS scan_job_id "
        "FROM rename_operation_items roi "
        "JOIN rename_previews rp ON rp.id = roi.rename_preview_id "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE roi.operation_id IN (" + ",".join("?" for _ in operationIds) + ") GROUP BY roi.operation_id",
        operationIds,
    ).fetchall()
    return {int(row["operation_id"]): row for row in rows}


def _rename_operation_items(connection: sqlite3.Connection, ids: list[int]) -> list[TaskGovernanceItem]:
    if not ids:
        return []
    rows = connection.execute(
        "SELECT id, status, total_count, ready_count, conflict_count, renamed_count, "
        "failed_count, created_at, updated_at FROM rename_operations" + _selected_clause(ids), ids
    ).fetchall()
    sources = _operation_media_sources(connection, ids)
    items: list[TaskGovernanceItem] = []
    for row in rows:
        task_id = int(row["id"])
        source_row = sources.get(task_id)
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
        target_query: dict[str, str] = {"operation_id": str(task_id)}
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


def _rollback_plan_items(connection: sqlite3.Connection, ids: list[int]) -> list[TaskGovernanceItem]:
    if not ids:
        return []
    rows = connection.execute(
        "SELECT id, operation_id, status, item_count, executable_count, conflict_count, "
        "created_by, created_at, updated_at, rolled_back_count, failed_count FROM rename_rollback_plans" + _selected_clause(ids), ids
    ).fetchall()
    sources = _operation_media_sources(connection, list({int(row["operation_id"]) for row in rows}))
    items: list[TaskGovernanceItem] = []
    for row in rows:
        plan_id = int(row["id"])
        operation_id = int(row["operation_id"])
        source_row = sources.get(operation_id)
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
                    f"已回滚 {int(row['rolled_back_count'] or 0)} 条，失败 {int(row['failed_count'] or 0)} 条"
                ),
                media_source_id=media_source_id,
                media_source_name=source_row["media_source_name"] if source_row is not None else None,
                related_id=operation_id,
                related_type=TASK_TYPE_RENAME_OPERATION,
                total_count=int(row["item_count"] or 0),
                success_count=int(row["rolled_back_count"] or 0),
                failed_count=int(row["failed_count"] or 0),
                warning_count=int(row["conflict_count"] or 0),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                log_task_type=TASK_TYPE_ROLLBACK_PLAN,
                log_task_id=plan_id,
                target_route="rename-previews",
                target_query={"operation_id": str(operation_id), "rollback_plan_id": str(plan_id)},
                archived=False,
                archived_at=None,
                archived_by=None,
                archive_reason=None,
            )
        )
    return items


def _pending_move_items(connection, ids):
    if not ids:
        return []
    rows = connection.execute("SELECT t.*,MIN(i.media_source_id) AS media_source_id,MIN(s.name) AS source_name,"
        "MIN(p.scan_job_id) AS scan_job_id,COUNT(DISTINCT i.media_source_id) AS source_count,COUNT(DISTINCT p.scan_job_id) AS job_count "
        "FROM pending_move_tasks t JOIN pending_move_items i ON i.task_id=t.id "
        "LEFT JOIN pending_files p ON p.id=i.pending_file_id LEFT JOIN media_sources s ON s.id=i.media_source_id "
        f"WHERE t.id IN ({','.join('?' for _ in ids)}) GROUP BY t.id", ids).fetchall()
    return [TaskGovernanceItem(
        id=f"pending_move:{row['id']}", task_type="pending_move", task_id=row["id"], status=row["status"],
        title=f"迁移任务 #{row['id']}", summary=f"已迁移 {row['moved_count']} / {row['total_count']} 项，失败 {row['failed_count']} 项",
        media_source_id=row["media_source_id"] if row["source_count"] == 1 else None,
        media_source_name=row["source_name"] if row["source_count"] == 1 else None, related_id=None, related_type=None,
        total_count=row["total_count"], success_count=row["moved_count"], failed_count=row["failed_count"],
        warning_count=max(0, row["total_count"] - row["moved_count"] - row["failed_count"]) if row["status"] not in {"queued", "running"} else 0,
        created_at=row["created_at"], updated_at=row["updated_at"], log_task_type="pending_move", log_task_id=row["id"],
        target_route="rename-previews", target_query={"pending_move_task_id": str(row["id"]),
            **({"media_source_id": str(row["media_source_id"])} if row["source_count"] == 1 else {}),
            **({"scan_job_id": str(row["scan_job_id"])} if row["job_count"] == 1 else {})},
        archived=False, archived_at=None, archived_by=None, archive_reason=None) for row in rows]


def _background_job_items(connection, ids):
    if not ids:
        return []
    rows = connection.execute("SELECT id,request_id,kind,status,total_count,completed_count,success_count,failed_count,"
        "unknown_count,created_at,updated_at,result_cleared_at FROM background_jobs "
        f"WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()
    return [TaskGovernanceItem(id=f"background_job:{row['id']}", task_type="background_job", task_id=row["id"],
        status=row["status"], title=f"后台探测/匹配 #{row['id']}",
        summary=f"已处理 {row['completed_count']}/{row['total_count']} 项，需核查 {row['unknown_count']} 项"
            + ("；结果已清理" if row["result_cleared_at"] else ""),
        media_source_id=None, media_source_name=None, related_id=None, related_type=None,
        total_count=row["total_count"], success_count=row["success_count"], failed_count=row["failed_count"],
        warning_count=row["unknown_count"], created_at=row["created_at"], updated_at=row["updated_at"],
        log_task_type="background_job", log_task_id=row["id"], target_route="rename-previews",
        target_query={"background_request_id": row["request_id"]},
        archived=False, archived_at=None, archived_by=None, archive_reason=None) for row in rows]


_TASK_QUERY = """
WITH operation_sources AS (
    SELECT roi.operation_id, MIN(ms.id) AS media_source_id
    FROM rename_operation_items roi
    JOIN rename_previews rp ON rp.id=roi.rename_preview_id
    JOIN media_files mf ON mf.id=rp.media_file_id
    JOIN media_sources ms ON ms.id=mf.media_source_id
    GROUP BY roi.operation_id
), tasks AS (
    SELECT 'scan_job' AS task_type, id AS task_id, status, media_source_id,
        scanned_count AS total_count, video_count AS success_count,
        CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END AS failed_count,
        warning_count, created_at, COALESCE(ended_at,started_at,created_at) AS updated_at
    FROM scan_jobs
    UNION ALL
    SELECT 'rename_operation', ro.id, ro.status, os.media_source_id,
        ro.total_count, ro.renamed_count, ro.failed_count, ro.conflict_count, ro.created_at, ro.updated_at
    FROM rename_operations ro LEFT JOIN operation_sources os ON os.operation_id=ro.id
    UNION ALL
    SELECT 'rollback_plan', rp.id, rp.status, os.media_source_id,
        rp.item_count, rp.rolled_back_count, rp.failed_count, rp.conflict_count, rp.created_at, rp.updated_at
    FROM rename_rollback_plans rp LEFT JOIN operation_sources os ON os.operation_id=rp.operation_id
    UNION ALL
    SELECT 'pending_move', t.id,t.status,NULL,t.total_count,t.moved_count,t.failed_count,
        CASE WHEN t.status NOT IN ('queued','running') THEN MAX(0,t.total_count-t.moved_count-t.failed_count) ELSE 0 END,
        t.created_at,t.updated_at FROM pending_move_tasks t
    UNION ALL
    SELECT 'background_job',id,status,NULL,total_count,success_count,failed_count,unknown_count,created_at,updated_at
    FROM background_jobs
)
"""
_TASK_SORTS = {"task_id", "status", "total_count", "success_count", "failed_count",
               "warning_count", "created_at", "updated_at"}


def _hydrate_tasks(connection: sqlite3.Connection, selected: list[tuple[str, int]]) -> list[TaskGovernanceItem]:
    archives = _archive_map(connection, selected)
    groups = {kind: [taskId for taskType, taskId in selected if taskType == kind] for kind in TASK_TYPES}
    items = [*_scan_job_items(connection, groups[TASK_TYPE_SCAN_JOB]),
             *_rename_operation_items(connection, groups[TASK_TYPE_RENAME_OPERATION]),
             *_rollback_plan_items(connection, groups[TASK_TYPE_ROLLBACK_PLAN])]
    items.extend(_pending_move_items(connection, groups[TASK_TYPE_PENDING_MOVE]))
    items.extend(_background_job_items(connection, groups["background_job"]))
    indexed = {(item.task_type, item.task_id): item for item in items}
    return [_with_archive_state(indexed[key], archives.get(key)) for key in selected]


def listTaskGovernancePage(
    settings: AppSettings, *, page: int = 1, page_size: int = 50,
    sort_by: str = "updated_at", sort_order: str = "desc",
    task_type: str | None = None, status: str | None = None,
    media_source_id: int | None = None, start_at: str | None = None,
    end_at: str | None = None, include_archived: bool = False,
    _legacyLimit: int | None = None,
) -> dict[str, Any]:
    if task_type and task_type not in TASK_TYPES:
        raise ValueError("任务类型不受支持")
    maximum = 500 if _legacyLimit is not None else 200
    if page < 1 or not 1 <= page_size <= maximum:
        raise ValueError("分页参数超出允许范围")
    if sort_by not in _TASK_SORTS or sort_order not in {"asc", "desc"}:
        raise ValueError("排序参数不受支持")
    clauses, values = [], []
    for column, value, operator in (("task_type", task_type, "="), ("status", status, "="),
        ("created_at", start_at, ">="), ("created_at", end_at, "<=")):
        if value is not None and value != "":
            clauses.append(f"t.{column} {operator} ?")
            values.append(value)
    if media_source_id is not None:
        # 混合来源迁移按任一关联项筛选，详情只在选页后加载，不聚合全量历史明细。
        clauses.append("((t.task_type='pending_move' AND EXISTS (SELECT 1 FROM pending_move_items i "
                       "WHERE i.task_id=t.task_id AND i.media_source_id=?)) "
                       "OR (t.task_type<>'pending_move' AND t.media_source_id=?))")
        values.extend((media_source_id, media_source_id))
    if not include_archived:
        clauses.append("NOT EXISTS (SELECT 1 FROM task_archives a WHERE a.task_type=t.task_type AND a.task_id=t.task_id)")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(_connect(settings)) as connection:
        # 计数、选页和详情使用同一快照，避免归档或新任务造成页内不一致。
        connection.execute("BEGIN")
        total = int(connection.execute(_TASK_QUERY + "SELECT COUNT(*) FROM tasks t" + where, values).fetchone()[0])
        actualPage = min(page, max(1, (total + page_size - 1) // page_size))
        rows = connection.execute(_TASK_QUERY + "SELECT task_type,task_id FROM tasks t" + where +
            f" ORDER BY t.{sort_by} {sort_order},t.task_type DESC,t.task_id DESC LIMIT ? OFFSET ?",
            [*values, page_size, (actualPage - 1) * page_size]).fetchall()
        items = _hydrate_tasks(connection, [(str(row[0]), int(row[1])) for row in rows])
    return {"items": items, "total": total, "page": actualPage, "page_size": page_size}


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
    """聚合扫描、重命名、回滚和待处理迁移任务。"""

    safe_limit = max(1, min(int(limit), 500))
    return listTaskGovernancePage(settings, page_size=safe_limit, _legacyLimit=safe_limit,
        task_type=task_type, status=status, media_source_id=media_source_id,
        start_at=start_at, end_at=end_at, include_archived=include_archived)["items"]


def set_task_archived(
    settings: AppSettings,
    *,
    task_type: str,
    task_id: int,
    archived: bool,
    archived_by: str | None = None,
    reason: str | None = None,
    auditContext: AuditWriteContext | None = None,
) -> TaskGovernanceItem:
    """归档或恢复任务治理展示；不删除任何任务和真实文件。"""

    if task_type not in TASK_TYPES:
        raise ValueError("任务类型不受支持")
    context = auditContext or AuditWriteContext()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
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
        action = "archive_task" if archived else "restore_task"
        record_audit_event(
            settings,
            event_type="task.governance",
            action=action,
            result="success",
            summary=("归档任务" if archived else "恢复任务") + f"：{task_type}#{task_id}",
            target_type=task_type,
            target_id=str(task_id),
            actor=context.actor,
            actor_name=archived_by if context.actor is None else None,
            ip_address=context.ipAddress,
            user_agent=context.userAgent,
            detail={"task_type": task_type, "task_id": task_id, "archived": archived, "reason": reason},
            connection=connection,
        )
        connection.commit()

        return _hydrate_tasks(connection, [(task_type, task_id)])[0]
