"""重命名回滚服务。"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.core.config import AppSettings
from app.schema.media import RenameRollbackItem, RenameRollbackPlan
from app.service.media_source_service import get_media_source_protocol_context
from app.service.operation_log_service import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    TASK_TYPE_ROLLBACK_PLAN,
    cleanup_operation_logs,
    record_operation_log,
)
from app.service.scan_service import _file_modified_at, _normalized_index_path, _upsert_scan_file_index
from app.service.shared_protocols.registry import get_protocol

PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_CHECKED = "checked"
PLAN_STATUS_EXECUTED = "executed"
PLAN_STATUS_PARTIAL_FAILED = "partial_failed"
PLAN_STATUS_FAILED = "failed"

ITEM_STATUS_PENDING = "pending"
ITEM_STATUS_READY = "ready"
ITEM_STATUS_CONFLICT = "conflict"
ITEM_STATUS_ROLLED_BACK = "rolled_back"
ITEM_STATUS_FAILED = "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_item(row: sqlite3.Row) -> RenameRollbackItem:
    return RenameRollbackItem(
        id=int(row["id"]),
        plan_id=int(row["plan_id"]),
        operation_item_id=int(row["operation_item_id"]),
        current_path=str(row["current_path"]),
        rollback_path=str(row["rollback_path"]),
        status=str(row["status"]),
        message=row["message"],
        executed_at=row["executed_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_plan(row: sqlite3.Row, items: list[RenameRollbackItem]) -> RenameRollbackPlan:
    return RenameRollbackPlan(
        id=int(row["id"]),
        operation_id=int(row["operation_id"]),
        status=str(row["status"]),
        item_count=int(row["item_count"]),
        executable_count=int(row["executable_count"]),
        conflict_count=int(row["conflict_count"]),
        created_by=row["created_by"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        items=items,
    )


def _fetch_plan(connection: sqlite3.Connection, plan_id: int) -> RenameRollbackPlan:
    plan_row = connection.execute(
        "SELECT id, operation_id, status, item_count, executable_count, conflict_count, "
        "created_by, created_at, updated_at "
        "FROM rename_rollback_plans WHERE id = ?",
        (plan_id,),
    ).fetchone()
    if plan_row is None:
        raise ValueError("回滚计划不存在")
    item_rows = connection.execute(
        "SELECT id, plan_id, operation_item_id, current_path, rollback_path, status, "
        "message, executed_at, created_at, updated_at "
        "FROM rename_rollback_items WHERE plan_id = ? ORDER BY id",
        (plan_id,),
    ).fetchall()
    return _row_to_plan(plan_row, [_row_to_item(row) for row in item_rows])


def get_rename_rollback_plan(settings: AppSettings, plan_id: int) -> RenameRollbackPlan:
    """查询单个回滚计划。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _fetch_plan(connection, plan_id)


def list_rename_rollback_plans(
    settings: AppSettings,
    operation_id: int | None = None,
) -> list[RenameRollbackPlan]:
    """查询回滚计划列表。"""

    conditions: list[str] = []
    params: list[object] = []
    if operation_id is not None:
        conditions.append("operation_id = ?")
        params.append(operation_id)
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id FROM rename_rollback_plans"
            f"{where_clause} ORDER BY id DESC",
            tuple(params),
        ).fetchall()
        return [_fetch_plan(connection, int(row["id"])) for row in rows]


def create_rename_rollback_plan(
    settings: AppSettings,
    operation_id: int,
    created_by: str | None = None,
) -> RenameRollbackPlan:
    """基于已成功重命名明细创建回滚计划。"""

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        operation_row = connection.execute(
            "SELECT id, status FROM rename_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if operation_row is None:
            raise ValueError("重命名批次不存在")
        rows = connection.execute(
            "SELECT id, source_path, target_path FROM rename_operation_items "
            "WHERE operation_id = ? AND status = 'renamed' ORDER BY id",
            (operation_id,),
        ).fetchall()
        if not rows:
            raise ValueError("该重命名批次没有可回滚的成功条目")

        cursor = connection.execute(
            "INSERT INTO rename_rollback_plans "
            "(operation_id, status, item_count, executable_count, conflict_count, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, 0, ?, ?, ?)",
            (operation_id, PLAN_STATUS_DRAFT, len(rows), created_by, now, now),
        )
        plan_id = int(cursor.lastrowid)
        for row in rows:
            connection.execute(
                "INSERT INTO rename_rollback_items "
                "(plan_id, operation_item_id, current_path, rollback_path, status, message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    plan_id,
                    int(row["id"]),
                    str(row["target_path"]),
                    str(row["source_path"]),
                    ITEM_STATUS_PENDING,
                    now,
                    now,
                ),
            )
        record_operation_log(
            settings,
            task_type=TASK_TYPE_ROLLBACK_PLAN,
            task_id=plan_id,
            level=LEVEL_INFO,
            stage="rollback_plan_created",
            message=f"已创建回滚计划：共 {len(rows)} 项",
            progress_current=0,
            progress_total=len(rows),
            detail={"operation_id": operation_id, "created_by": created_by},
            connection=connection,
        )
        connection.commit()
    cleanup_operation_logs(settings)
    return get_rename_rollback_plan(settings, plan_id)


def _rollback_item_context(
    connection: sqlite3.Connection,
    operation_item_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT roi.id AS operation_item_id, roi.rename_preview_id, "
        "mf.media_source_id, mf.scan_job_id, ms.path AS media_source_path, ms.path_type "
        "FROM rename_operation_items roi "
        "JOIN rename_previews rp ON rp.id = roi.rename_preview_id "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE roi.id = ?",
        (operation_item_id,),
    ).fetchone()


def _validate_rollback_item(
    settings: AppSettings,
    connection: sqlite3.Connection,
    item: RenameRollbackItem,
    duplicate_rollback_paths: Counter[str],
) -> tuple[str, str | None]:
    current_path = Path(item.current_path)
    rollback_path = Path(item.rollback_path)
    if duplicate_rollback_paths[str(rollback_path)] > 1:
        return ITEM_STATUS_CONFLICT, "批次内回滚目标重复"
    if not current_path.exists():
        return ITEM_STATUS_CONFLICT, "当前文件不存在"
    if rollback_path.exists() and current_path.resolve() != rollback_path.resolve():
        return ITEM_STATUS_CONFLICT, "回滚目标已存在"
    context_row = _rollback_item_context(connection, item.operation_item_id)
    path_type = str(context_row["path_type"]) if context_row else "local"
    media_source_id = int(context_row["media_source_id"]) if context_row else None
    readiness = get_protocol(path_type).check_rename_ready(
        str(current_path),
        str(rollback_path),
        get_media_source_protocol_context(settings, media_source_id) if media_source_id is not None else None,
    )
    if not readiness.success:
        return ITEM_STATUS_CONFLICT, readiness.message
    return ITEM_STATUS_READY, None


def dry_run_rename_rollback_plan(settings: AppSettings, plan_id: int) -> RenameRollbackPlan:
    """校验回滚计划，不改动文件。"""

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        plan = _fetch_plan(connection, plan_id)
        if plan.status in {PLAN_STATUS_EXECUTED, PLAN_STATUS_PARTIAL_FAILED, PLAN_STATUS_FAILED}:
            raise ValueError("已执行的回滚计划不能再次 dry-run")
        duplicate_rollback_paths = Counter(item.rollback_path for item in plan.items)
        executable_count = 0
        conflict_count = 0
        conflict_examples: list[dict[str, object]] = []
        for item in plan.items:
            status, message = _validate_rollback_item(settings, connection, item, duplicate_rollback_paths)
            if status == ITEM_STATUS_READY:
                executable_count += 1
            else:
                conflict_count += 1
                if len(conflict_examples) < 10:
                    conflict_examples.append(
                        {
                            "item_id": item.id,
                            "current_path": item.current_path,
                            "rollback_path": item.rollback_path,
                            "message": message,
                        }
                    )
            connection.execute(
                "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                (status, message, now, item.id),
            )
        connection.execute(
            "UPDATE rename_rollback_plans "
            "SET status = ?, executable_count = ?, conflict_count = ?, updated_at = ? WHERE id = ?",
            (PLAN_STATUS_CHECKED, executable_count, conflict_count, now, plan_id),
        )
        record_operation_log(
            settings,
            task_type=TASK_TYPE_ROLLBACK_PLAN,
            task_id=plan_id,
            level=LEVEL_SUCCESS if conflict_count == 0 else LEVEL_WARNING,
            stage="rollback_dry_run_completed",
            message=f"回滚 dry-run 完成：共 {plan.item_count} 项，可执行 {executable_count} 项，冲突 {conflict_count} 项",
            progress_current=plan.item_count,
            progress_total=plan.item_count,
            detail={
                "operation_id": plan.operation_id,
                "executable_count": executable_count,
                "conflict_count": conflict_count,
                "conflict_examples": conflict_examples,
            },
            connection=connection,
        )
        connection.commit()
    cleanup_operation_logs(settings)
    return get_rename_rollback_plan(settings, plan_id)


def _update_media_record_after_rollback(
    connection: sqlite3.Connection,
    operation_item_id: int,
    current_path: Path,
    rollback_path: Path,
    updated_at: str,
) -> None:
    context_row = _rollback_item_context(connection, operation_item_id)
    if context_row is None:
        return
    rename_preview_id = int(context_row["rename_preview_id"])
    media_source_id = int(context_row["media_source_id"])
    scan_job_id = int(context_row["scan_job_id"])
    media_source_path = Path(str(context_row["media_source_path"]))
    connection.execute(
        "UPDATE media_files "
        "SET file_path = ?, file_name = ?, extension = ?, modified_at = ? "
        "WHERE id = (SELECT media_file_id FROM rename_previews WHERE id = ?)",
        (str(rollback_path), rollback_path.name, rollback_path.suffix.lower(), updated_at, rename_preview_id),
    )
    connection.execute(
        "UPDATE rename_previews SET status = ?, message = ?, updated_at = ? WHERE id = ?",
        ("rolled_back", "已回滚", updated_at, rename_preview_id),
    )
    current_normalized_path = _normalized_index_path(current_path, media_source_path)
    connection.execute(
        "UPDATE scan_file_index SET status = ?, updated_at = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        ("rolled_back", updated_at, media_source_id, current_normalized_path),
    )
    if not rollback_path.exists():
        return
    stat = rollback_path.stat()
    _upsert_scan_file_index(
        connection,
        media_source_id,
        scan_job_id,
        rollback_path,
        media_source_path,
        stat.st_size,
        _file_modified_at(stat),
        "active",
        updated_at,
    )
    rollback_normalized_path = _normalized_index_path(rollback_path, media_source_path)
    connection.execute(
        "UPDATE scan_file_index SET rename_preview_id = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        (rename_preview_id, media_source_id, rollback_normalized_path),
    )


def execute_rename_rollback_plan(settings: AppSettings, plan_id: int) -> RenameRollbackPlan:
    """执行已 dry-run 通过的回滚计划。"""

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        plan = _fetch_plan(connection, plan_id)
        if plan.status != PLAN_STATUS_CHECKED:
            raise ValueError("请先执行回滚 dry-run")
        if plan.executable_count <= 0:
            raise ValueError("回滚计划没有可执行条目")
        record_operation_log(
            settings,
            task_type=TASK_TYPE_ROLLBACK_PLAN,
            task_id=plan_id,
            level=LEVEL_INFO,
            stage="rollback_started",
            message=f"开始执行回滚：待处理 {plan.executable_count} 项",
            progress_current=0,
            progress_total=plan.executable_count,
            detail={"operation_id": plan.operation_id},
            connection=connection,
        )
        rolled_back_count = 0
        failed_count = 0
        duplicate_rollback_paths = Counter(item.rollback_path for item in plan.items)
        for item in plan.items:
            if item.status != ITEM_STATUS_READY:
                continue
            status, message = _validate_rollback_item(settings, connection, item, duplicate_rollback_paths)
            if status != ITEM_STATUS_READY:
                connection.execute(
                    "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    (ITEM_STATUS_FAILED, message, now, item.id),
                )
                failed_count += 1
                record_operation_log(
                    settings,
                    task_type=TASK_TYPE_ROLLBACK_PLAN,
                    task_id=plan_id,
                    level=LEVEL_ERROR,
                    stage="rollback_item_failed",
                    message=f"回滚失败：{Path(item.current_path).name}，原因：{message}",
                    progress_current=rolled_back_count + failed_count,
                    progress_total=plan.executable_count,
                    detail={"current_path": item.current_path, "rollback_path": item.rollback_path},
                    connection=connection,
                )
                continue
            try:
                current_path = Path(item.current_path)
                rollback_path = Path(item.rollback_path)
                current_path.rename(rollback_path)
                _update_media_record_after_rollback(
                    connection,
                    item.operation_item_id,
                    current_path,
                    rollback_path,
                    now,
                )
                connection.execute(
                    "UPDATE rename_rollback_items "
                    "SET status = ?, message = NULL, executed_at = ?, updated_at = ? WHERE id = ?",
                    (ITEM_STATUS_ROLLED_BACK, now, now, item.id),
                )
                rolled_back_count += 1
            except Exception as exc:  # noqa: BLE001 - 单项失败不能中断整个回滚计划。
                connection.execute(
                    "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    (ITEM_STATUS_FAILED, str(exc), now, item.id),
                )
                failed_count += 1
                record_operation_log(
                    settings,
                    task_type=TASK_TYPE_ROLLBACK_PLAN,
                    task_id=plan_id,
                    level=LEVEL_ERROR,
                    stage="rollback_item_failed",
                    message=f"回滚失败：{Path(item.current_path).name}，原因：{exc}",
                    progress_current=rolled_back_count + failed_count,
                    progress_total=plan.executable_count,
                    detail={"current_path": item.current_path, "rollback_path": item.rollback_path},
                    connection=connection,
                )

        final_status = PLAN_STATUS_EXECUTED
        if failed_count:
            final_status = PLAN_STATUS_PARTIAL_FAILED if rolled_back_count else PLAN_STATUS_FAILED
        connection.execute(
            "UPDATE rename_rollback_plans "
            "SET status = ?, executable_count = ?, conflict_count = ?, updated_at = ? WHERE id = ?",
            (final_status, rolled_back_count, failed_count, now, plan_id),
        )
        record_operation_log(
            settings,
            task_type=TASK_TYPE_ROLLBACK_PLAN,
            task_id=plan_id,
            level=LEVEL_SUCCESS if final_status == PLAN_STATUS_EXECUTED else LEVEL_WARNING,
            stage="rollback_completed",
            message=f"回滚执行完成：成功 {rolled_back_count} 项，失败 {failed_count} 项",
            progress_current=rolled_back_count + failed_count,
            progress_total=plan.executable_count,
            detail={
                "operation_id": plan.operation_id,
                "status": final_status,
                "rolled_back_count": rolled_back_count,
                "failed_count": failed_count,
            },
            connection=connection,
        )
        connection.commit()
    cleanup_operation_logs(settings)
    return get_rename_rollback_plan(settings, plan_id)
