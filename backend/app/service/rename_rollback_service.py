"""重命名回滚服务。"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlparse

from app.core.config import AppSettings
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.schema.media import RenameRollbackItem, RenameRollbackPlan
from app.service.media_source_service import get_media_source_protocol_context
from app.service.operation_log_service import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    TASK_TYPE_ROLLBACK_PLAN,
    cleanup_operation_logs_best_effort as cleanup_operation_logs,
    record_operation_log,
)
from app.service.remote_operation_service import (
    acquire_remote_operation_lock,
    create_remote_operation_item,
    release_remote_operation_lock,
    update_remote_operation_item_status,
)
from app.service.scan_service import (
    _file_modified_at,
    _normalized_index_path,
    _remote_normalized_index_path,
    _upsert_scan_file_index,
)
from app.service.shared_protocols.registry import get_protocol
from app.service.remote_file_index_service import syncRemoteFileIndex
from app.utils.file_operations import moveFileNoReplace
from app.service.local_file_action_service import (
    prepareLocalFileAction,
    updateLocalFileAction,
    verifyLocalFileIdentity,
    verifyLocalMoveOutcome,
)
from app.service.local_file_action_service import localFileIdentity
from app.service.shared_protocols.webdav import isStrongEtag

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
REMOTE_PATH_TYPES = {"webdav"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_remote_path_type(path_type: str) -> bool:
    return path_type in REMOTE_PATH_TYPES


def _remote_file_name(remote_path: str) -> str:
    path = urlparse(remote_path).path
    name = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    return name or remote_path


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
        rolled_back_count=int(row["rolled_back_count"]),
        failed_count=int(row["failed_count"]),
        error_message=row["error_message"],
    )


def _fetch_plan(connection: sqlite3.Connection, plan_id: int) -> RenameRollbackPlan:
    plan_row = connection.execute(
        "SELECT id, operation_id, status, item_count, executable_count, conflict_count, "
         "created_by, created_at, updated_at, rolled_back_count, failed_count, error_message "
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
    *,
    auditContext: AuditWriteContext | None = None,
) -> RenameRollbackPlan:
    """基于已成功重命名明细创建回滚计划。"""

    now = _utc_now()
    context = auditContext or AuditWriteContext()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        operation_row = connection.execute(
            "SELECT id, status, execution_token FROM rename_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if operation_row is None:
            raise ValueError("重命名批次不存在")
        if operation_row["status"] in {"queued", "running"} or operation_row["execution_token"] is not None:
            raise ValueError("重命名尚未结束或执行进程待核对，暂不能生成回滚计划")
        historical = connection.execute(
            "SELECT 1 FROM rename_operation_items item JOIN rename_previews preview ON preview.id = item.rename_preview_id "
            "JOIN media_files file ON file.id = preview.media_file_id JOIN media_sources source ON source.id = file.media_source_id "
            "WHERE item.operation_id = ? AND item.status = 'renamed' AND (source.deleted_at IS NOT NULL OR EXISTS "
            "(SELECT 1 FROM scan_jobs job WHERE job.id = file.scan_job_id AND job.source_invalidated_at IS NOT NULL)) LIMIT 1",
            (operation_id,),
        ).fetchone()
        if historical:
            raise ValueError("媒体源已变更或退役，历史批次不能生成新的回滚计划")
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
        record_audit_event(settings, event_type="rename.rollback", action="create_rollback_plan", result="success",
            summary=f"创建回滚计划：{plan_id}", target_type="rollback_plan", target_id=plan_id,
            actor=context.actor, actor_name=(created_by or "system") if context.actor is None else None,
            ip_address=context.ipAddress, user_agent=context.userAgent,
            detail={"operation_id": operation_id, "plan_id": plan_id, "item_count": len(rows)}, connection=connection)
        connection.commit()
    cleanup_operation_logs(settings)
    return get_rename_rollback_plan(settings, plan_id)


def _rollback_item_context(
    connection: sqlite3.Connection,
    operation_item_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT roi.id AS operation_item_id, roi.rename_preview_id, "
        "roi.status AS operation_item_status, roi.source_path, roi.target_path, "
        "rp.status AS preview_status, rp.updated_at AS preview_updated_at, "
        "mf.file_path, mf.media_source_id, mf.scan_job_id, "
        "ms.path AS media_source_path, ms.path_type, ms.enabled, ms.updated_at AS source_updated_at, "
        "ms.deleted_at, (SELECT source_invalidated_at FROM scan_jobs WHERE id = mf.scan_job_id) AS source_invalidated_at "
        "FROM rename_operation_items roi "
        "JOIN rename_previews rp ON rp.id = roi.rename_preview_id "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE roi.id = ?",
        (operation_item_id,),
    ).fetchone()


def _rollbackSnapshot(settings: AppSettings, connection: sqlite3.Connection, item: RenameRollbackItem) -> dict:
    contextRow = _rollback_item_context(connection, item.operation_item_id)
    if (contextRow is None or not contextRow["enabled"] or contextRow["deleted_at"]
            or contextRow["source_invalidated_at"]):
        raise ValueError("回滚关联记录不存在或媒体源已禁用")
    if (contextRow["operation_item_status"] != "renamed" or contextRow["preview_status"] != "renamed"
            or contextRow["file_path"] != item.current_path
            or contextRow["target_path"] != item.current_path
            or contextRow["source_path"] != item.rollback_path):
        raise ValueError("回滚关联状态或路径已变化，请重新核对")
    snapshot = dict(contextRow)
    if _is_remote_path_type(str(contextRow["path_type"])):
        info = get_protocol(str(contextRow["path_type"])).get_file_info(
            item.current_path, get_media_source_protocol_context(settings, int(contextRow["media_source_id"])),
        )
        if not isStrongEtag(info.version):
            raise ValueError("WebDAV 未提供强 ETag，无法安全执行回滚")
        snapshot["remoteIdentity"] = [info.version, info.file_size, info.modified_at]
    else:
        snapshot["fileIdentity"] = localFileIdentity(Path(item.current_path))
    return snapshot


def _validateRollbackSnapshot(settings: AppSettings, connection: sqlite3.Connection, item: RenameRollbackItem) -> dict:
    stored = connection.execute(
        "SELECT snapshot_json FROM rename_rollback_items WHERE id = ?", (item.id,),
    ).fetchone()
    if stored is None or not stored[0]:
        raise ValueError("回滚干跑缺少安全快照，请重新执行 dry-run")
    snapshot = json.loads(stored[0])
    if snapshot != _rollbackSnapshot(settings, connection, item):
        raise ValueError("回滚源文件、媒体源或关联记录已变化，请重新核对")
    return snapshot


def _validate_rollback_item(
    settings: AppSettings,
    connection: sqlite3.Connection,
    item: RenameRollbackItem,
    duplicate_rollback_paths: Counter[str],
) -> tuple[str, str | None]:
    if duplicate_rollback_paths[item.rollback_path] > 1:
        return ITEM_STATUS_CONFLICT, "批次内回滚目标重复"
    context_row = _rollback_item_context(connection, item.operation_item_id)
    path_type = str(context_row["path_type"]) if context_row else "local"
    media_source_id = int(context_row["media_source_id"]) if context_row else None
    if not _is_remote_path_type(path_type):
        current_path = Path(item.current_path)
        rollback_path = Path(item.rollback_path)
        if not current_path.exists():
            return ITEM_STATUS_CONFLICT, "当前文件不存在"
        if rollback_path.exists() and current_path.resolve() != rollback_path.resolve():
            return ITEM_STATUS_CONFLICT, "回滚目标已存在"
    readiness = get_protocol(path_type).check_rename_ready(
        item.current_path,
        item.rollback_path,
        get_media_source_protocol_context(settings, media_source_id) if media_source_id is not None else None,
    )
    if not readiness.success:
        return ITEM_STATUS_CONFLICT, readiness.message
    return ITEM_STATUS_READY, None


def _rollbackDryRunDatabaseState(connection: sqlite3.Connection, planId: int):
    plan = connection.execute("SELECT * FROM rename_rollback_plans WHERE id=?", (planId,)).fetchone()
    if plan is None:
        raise ValueError("回滚计划不存在")
    items = connection.execute("SELECT * FROM rename_rollback_items WHERE plan_id=? ORDER BY id", (planId,)).fetchall()
    contexts = []
    for item in items:
        context = _rollback_item_context(connection, item["operation_item_id"])
        contexts.append(dict(context) if context is not None else None)
    original = connection.execute("SELECT status,execution_token,updated_at FROM rename_operations WHERE id=?",
                                  (plan["operation_id"],)).fetchone()
    return dict(plan), [dict(item) for item in items], contexts, tuple(original) if original is not None else None


def dry_run_rename_rollback_plan(settings: AppSettings, plan_id: int, *,
                                 auditContext: AuditWriteContext | None = None) -> RenameRollbackPlan:
    """事务外探测，短写事务复核原计划与关联版本；不改动文件。"""

    now = _utc_now()
    context = auditContext or AuditWriteContext()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        plan = _fetch_plan(connection, plan_id)
        databaseSnapshot = _rollbackDryRunDatabaseState(connection, plan_id)
        connection.commit()
        if plan.status not in {PLAN_STATUS_DRAFT, PLAN_STATUS_CHECKED}:
            raise ValueError("已执行的回滚计划不能再次 dry-run")
        original = databaseSnapshot[3]
        if databaseSnapshot[0]["execution_token"] is not None or (original is not None
                and (original[0] in {"queued", "running"} or original[1] is not None)):
            raise ValueError("原执行进程退出未确认，不能重新 dry-run")
        duplicate_rollback_paths = Counter(item.rollback_path for item in plan.items)
        executable_count = 0
        conflict_count = 0
        conflict_examples: list[dict[str, object]] = []
        results = []
        for item in plan.items:
            status, message = _validate_rollback_item(settings, connection, item, duplicate_rollback_paths)
            snapshotJson = None
            if status == ITEM_STATUS_READY:
                try:
                    snapshotJson = json.dumps(_rollbackSnapshot(settings, connection, item), ensure_ascii=False)
                except (ValueError, OSError) as exc:
                    status, message = ITEM_STATUS_CONFLICT, str(exc)
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
            results.append((status, message, now, snapshotJson, item.id))

        # 不允许慢探测覆写另一干跑或已受理执行；此处只读取数据库快照。
        connection.execute("BEGIN IMMEDIATE")
        if databaseSnapshot != _rollbackDryRunDatabaseState(connection, plan_id):
            raise ValueError("干跑探测期间回滚计划、来源或关联记录已变化，请重新核对")
        if context.actor is not None:
            from app.service.file_task_queue_service import requireFileTaskActor
            requireFileTaskActor(connection, "rollback_plan", context.actor.id)
        for result in results:
            connection.execute(
                "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ?, snapshot_json = ? WHERE id = ?",
                result,
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
        auditResult = "success" if conflict_count == 0 else ("partial_success" if executable_count else "failed")
        record_audit_event(settings, event_type="rename.rollback", action="dry_run_rollback_plan", result=auditResult,
            summary=f"回滚 dry-run：{plan_id}，可执行 {executable_count} 项，冲突 {conflict_count} 项",
            target_type="rollback_plan", target_id=plan_id, actor=context.actor,
            actor_name="system" if context.actor is None else None,
            ip_address=context.ipAddress, user_agent=context.userAgent,
            detail={"plan_id": plan_id, "status": PLAN_STATUS_CHECKED,
                    "executable_count": executable_count, "conflict_count": conflict_count}, connection=connection)
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


def _update_remote_media_record_after_rollback(
    connection: sqlite3.Connection,
    operation_item_id: int,
    current_path: str,
    rollback_path: str,
    updated_at: str,
) -> None:
    context_row = _rollback_item_context(connection, operation_item_id)
    if context_row is None:
        return
    rename_preview_id = int(context_row["rename_preview_id"])
    media_source_id = int(context_row["media_source_id"])
    media_source_path = str(context_row["media_source_path"])
    rollback_name = _remote_file_name(rollback_path)
    extension = "." + rollback_name.rsplit(".", 1)[-1].lower() if "." in rollback_name else ""
    connection.execute(
        "UPDATE media_files "
        "SET file_path = ?, file_name = ?, extension = ?, modified_at = ? "
        "WHERE id = (SELECT media_file_id FROM rename_previews WHERE id = ?)",
        (rollback_path, rollback_name, extension, updated_at, rename_preview_id),
    )
    connection.execute(
        "UPDATE rename_previews SET status = ?, message = ?, updated_at = ? WHERE id = ?",
        ("rolled_back", "已回滚", updated_at, rename_preview_id),
    )
    current_normalized_path = _remote_normalized_index_path(current_path, media_source_path)
    rollback_normalized_path = _remote_normalized_index_path(rollback_path, media_source_path)
    connection.execute(
        "UPDATE scan_file_index SET status = ?, updated_at = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        ("rolled_back", updated_at, media_source_id, current_normalized_path),
    )
    connection.execute(
        "UPDATE scan_file_index SET status = ?, rename_preview_id = ?, updated_at = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        ("active", rename_preview_id, updated_at, media_source_id, rollback_normalized_path),
    )


def execute_rename_rollback_plan(settings: AppSettings, plan_id: int, *, executionToken: str | None = None) -> RenameRollbackPlan:
    """执行已 dry-run 通过的回滚计划。"""

    now = _utc_now()
    synchronousClaim = executionToken is None
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        plan = _fetch_plan(connection, plan_id)
        if executionToken is None and plan.status != PLAN_STATUS_CHECKED:
            raise ValueError("请先执行回滚 dry-run")
        if plan.executable_count <= 0:
            raise ValueError("回滚计划没有可执行条目")
        from app.service.file_task_queue_service import FileTaskLeaseError, requireFileTaskLease, assertFileTaskSlotFree

        def guard():
            if executionToken is not None:
                requireFileTaskLease(
                    connection,
                    "rollback_plan",
                    plan_id,
                    executionToken,
                    requireAuditIntent=not synchronousClaim,
                )

        def saveProgress():
            connection.execute(
                "UPDATE rename_rollback_plans SET rolled_back_count=?,failed_count=?,updated_at=? WHERE id=?",
                (rolled_back_count, failed_count, _utc_now(), plan_id),
            )
            processed = rolled_back_count + failed_count
            if processed == plan.executable_count or processed % max(1, (plan.executable_count + 99) // 100) == 0:
                record_operation_log(settings, task_type=TASK_TYPE_ROLLBACK_PLAN, task_id=plan_id,
                    level=LEVEL_INFO, stage="rollback_progress",
                    message=f"回滚进度：成功 {rolled_back_count} 项，失败 {failed_count} 项",
                    progress_current=processed, progress_total=plan.executable_count, connection=connection)

        if synchronousClaim:
            from app.service.file_task_queue_service import claimSynchronousFileTask
            connection.execute("BEGIN IMMEDIATE")
            executionToken = claimSynchronousFileTask(
                connection,
                "rollback_plan",
                plan_id,
                PLAN_STATUS_CHECKED,
            )
            connection.commit()
        else:
            guard()
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
        connection.commit()
        rolled_back_count = 0
        failed_count = 0
        duplicate_rollback_paths = Counter(item.rollback_path for item in plan.items)
        for item in plan.items:
            if item.status != ITEM_STATUS_READY:
                continue
            guard()
            now = _utc_now()
            status, message = _validate_rollback_item(settings, connection, item, duplicate_rollback_paths)
            snapshot = None
            if status == ITEM_STATUS_READY:
                try:
                    snapshot = _validateRollbackSnapshot(settings, connection, item)
                except (ValueError, OSError) as exc:
                    status, message = ITEM_STATUS_CONFLICT, str(exc)
            if status != ITEM_STATUS_READY:
                connection.execute(
                    "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    (ITEM_STATUS_FAILED, message, now, item.id),
                )
                failed_count += 1
                saveProgress()
                if failed_count <= 20:
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
                connection.commit()
                continue
            localActionId = None
            try:
                context_row = _rollback_item_context(connection, item.operation_item_id)
                path_type = str(context_row["path_type"]) if context_row else "local"
                if _is_remote_path_type(path_type):
                    if context_row is None:
                        raise ValueError("远程媒体源不存在")
                    media_source_id = int(context_row["media_source_id"])
                    lock_key = f"media-source:{media_source_id}:write"
                    lock = acquire_remote_operation_lock(
                        settings,
                        media_source_id=media_source_id,
                        lock_key=lock_key,
                        owner="system",
                        task_type=TASK_TYPE_ROLLBACK_PLAN,
                        task_id=plan_id,
                        ttl_seconds=300,
                    )
                    remote_item = None
                    remoteApplied = False
                    remoteResultUnknown = False
                    try:
                        guard()
                        remote_item = create_remote_operation_item(
                            settings,
                            media_source_id=media_source_id,
                            operation_type="rollback",
                            idempotency_key=f"rollback-plan:{plan_id}:item:{item.id}",
                            source_path=item.current_path,
                            target_path=item.rollback_path,
                            source_version=snapshot["remoteIdentity"][0],
                            recovery={
                                "operation_id": plan.operation_id,
                                "operation_item_id": item.operation_item_id,
                                "rollback_plan_id": plan_id,
                                "rollback_item_id": item.id,
                            },
                        )
                        context = get_media_source_protocol_context(settings, media_source_id)
                        context = replace(context, expected_source_version=snapshot["remoteIdentity"][0])
                        protocol = get_protocol(path_type)
                        mover = getattr(protocol, "move_file", None)
                        if mover is None:
                            raise ValueError("当前协议不支持远程真实回滚")
                        guard()
                        move_result = mover(item.current_path, item.rollback_path, context)
                        if not move_result.success:
                            remoteResultUnknown = move_result.indeterminate
                            raise ValueError(move_result.message)
                        remoteApplied = True
                        update_remote_operation_item_status(
                            settings,
                            remote_item.id,
                            "remote_applied",
                            recovery={
                                "operation_id": plan.operation_id,
                                "operation_item_id": item.operation_item_id,
                                "rollback_plan_id": plan_id,
                                "rollback_item_id": item.id,
                            },
                        )
                        targetInfo = protocol.get_file_info(item.rollback_path, context)
                        _update_remote_media_record_after_rollback(
                            connection,
                            item.operation_item_id,
                            item.current_path,
                            item.rollback_path,
                            now,
                        )
                        contextRow = _rollback_item_context(connection, item.operation_item_id)
                        if contextRow is None:
                            raise ValueError("回滚关联媒体记录不存在")
                        syncRemoteFileIndex(
                            connection, int(contextRow["rename_preview_id"]), item.current_path,
                            targetInfo, "rolled_back", now,
                        )
                        connection.execute(
                            "UPDATE rename_rollback_items "
                            "SET status = ?, message = NULL, executed_at = ?, updated_at = ? WHERE id = ?",
                            (ITEM_STATUS_ROLLED_BACK, now, now, item.id),
                        )
                        connection.execute(
                            "UPDATE remote_operation_items SET status = 'completed', error_message = NULL, "
                            "target_version = ?, updated_at = ? WHERE id = ?", (targetInfo.version, _utc_now(), remote_item.id),
                        )
                        rolled_back_count += 1
                        saveProgress()
                        connection.commit()
                    except Exception as remote_exc:  # noqa: BLE001 - 单条远程回滚失败必须记录。
                        connection.rollback()
                        # 事务回滚后只保留已提交计数，不能沿用尚未提交的内存成功数。
                        savedCounts = connection.execute(
                            "SELECT rolled_back_count,failed_count FROM rename_rollback_plans WHERE id=?", (plan_id,),
                        ).fetchone()
                        rolled_back_count, failed_count = int(savedCounts[0]), int(savedCounts[1])
                        if remote_item is not None:
                            update_remote_operation_item_status(
                                settings,
                                remote_item.id,
                                "recovery_required" if remoteApplied or remoteResultUnknown else "failed",
                                error_message=(
                                    f"远端已回滚，业务提交未完成，需要恢复：{remote_exc}"
                                    if remoteApplied
                                    else f"远端回滚 MOVE 已发出但结果不明确，需要人工核查：{remote_exc}"
                                    if remoteResultUnknown
                                    else str(remote_exc)
                                ),
                            )
                        raise
                    finally:
                        if lock is not None:
                            try:
                                release_remote_operation_lock(settings, lock_key, lock.lease_token)
                            except Exception as lockError:  # noqa: BLE001 - 已提交的文件结果不能被解锁异常覆盖。
                                record_operation_log(settings, task_type=TASK_TYPE_ROLLBACK_PLAN, task_id=plan_id,
                                    level=LEVEL_WARNING, stage="rollback_lock_release_failed",
                                    message=f"远端写锁释放失败（{type(lockError).__name__}），请核对遗留锁",
                                    connection=connection)
                                connection.commit()
                    continue
                current_path = Path(item.current_path)
                rollback_path = Path(item.rollback_path)
                if context_row is None:
                    raise ValueError("回滚关联媒体记录不存在")
                guard()
                localActionId = prepareLocalFileAction(
                    settings, mediaSourceId=int(context_row["media_source_id"]), actionType="rollback",
                    recordId=item.id, sourcePath=str(current_path), targetPath=str(rollback_path),
                    expectedIdentity=snapshot["fileIdentity"],
                )
                guard()
                verifyLocalFileIdentity(current_path, snapshot["fileIdentity"])
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "prepared",
                    actionPhase="move_issued",
                )
                moveFileNoReplace(current_path, rollback_path)
                targetIdentity = verifyLocalMoveOutcome(
                    current_path,
                    rollback_path,
                    snapshot["fileIdentity"],
                )
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "applied",
                    targetIdentity=targetIdentity,
                    actionPhase="target_confirmed",
                )
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
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "completed",
                    connection=connection,
                    actionPhase="business_committed",
                )
                rolled_back_count += 1
            except FileTaskLeaseError:
                connection.rollback()
                raise
            except Exception as exc:  # noqa: BLE001 - 单项失败不能中断整个回滚计划。
                connection.rollback()
                if localActionId is not None:
                    updateLocalFileAction(settings, localActionId, "recovery_required", message=str(exc))
                itemMessage = (
                    f"本地动作未完成一致性提交，请停服核对恢复：{exc}" if localActionId is not None else str(exc)
                )
                connection.execute(
                    "UPDATE rename_rollback_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    (ITEM_STATUS_FAILED, itemMessage, now, item.id),
                )
                failed_count += 1
                if failed_count <= 20:
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

            saveProgress()
            connection.commit()

        final_status = PLAN_STATUS_EXECUTED
        if failed_count:
            final_status = PLAN_STATUS_PARTIAL_FAILED if rolled_back_count else PLAN_STATUS_FAILED
        finalGuard = " AND status='running' AND execution_token=? AND julianday(execution_expires_at)>julianday('now')" if executionToken is not None else ""
        changed = connection.execute(
            "UPDATE rename_rollback_plans "
            "SET status=?,executable_count=?,conflict_count=?,updated_at=? WHERE id=?" + finalGuard,
            (final_status, rolled_back_count, failed_count, _utc_now(), plan_id) +
            ((executionToken,) if executionToken is not None else ()),
        ).rowcount
        if changed != 1:
            connection.rollback()
            raise FileTaskLeaseError("回滚执行授权已失效，请核对已提交的文件动作")
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
    if synchronousClaim:
        from app.service.file_task_queue_service import releaseSynchronousFileTask
        releaseSynchronousFileTask(settings, "rollback_plan", plan_id, executionToken)
    cleanup_operation_logs(settings)
    return get_rename_rollback_plan(settings, plan_id)
