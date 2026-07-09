"""Safe rename operation service."""

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePath
import sqlite3

from app.core.config import AppSettings
from app.schema.media import RenameOperation, RenameOperationItem
from app.service.media_source_service import get_media_source_protocol_context
from app.service.operation_log_service import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    TASK_TYPE_RENAME_OPERATION,
    cleanup_operation_logs,
    record_operation_log,
)
from app.service.scan_service import _file_modified_at, _normalized_index_path, _upsert_scan_file_index
from app.service.shared_protocols.registry import get_protocol
from app.service.settings_service import get_effective_settings

READY_STATUSES = {"generated", "edited"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_item(row: sqlite3.Row) -> RenameOperationItem:
    return RenameOperationItem(
        id=int(row["id"]),
        operation_id=int(row["operation_id"]),
        rename_preview_id=int(row["rename_preview_id"]),
        source_path=str(row["source_path"]),
        target_path=str(row["target_path"]),
        status=str(row["status"]),
        message=row["message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_operation(row: sqlite3.Row, items: list[RenameOperationItem]) -> RenameOperation:
    return RenameOperation(
        id=int(row["id"]),
        status=str(row["status"]),
        mode=str(row["mode"]),
        total_count=int(row["total_count"]),
        ready_count=int(row["ready_count"]),
        conflict_count=int(row["conflict_count"]),
        renamed_count=int(row["renamed_count"]),
        failed_count=int(row["failed_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        items=items,
    )


def _target_name_is_valid(target_name: str) -> bool:
    if not target_name.strip():
        return False
    return PurePath(target_name).name == target_name and "/" not in target_name and "\\" not in target_name


def _query_previews(connection: sqlite3.Connection, preview_ids: list[int]) -> list[sqlite3.Row]:
    if not preview_ids:
        return []
    placeholders = ", ".join("?" for _ in preview_ids)
    return connection.execute(
        "SELECT rp.id, rp.status, rp.suggested_name, rp.edited_name, mf.file_path, "
        "ms.id AS media_source_id, ms.path_type "
        "FROM rename_previews rp "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        f"WHERE rp.id IN ({placeholders}) "
        "ORDER BY rp.id",
        preview_ids,
    ).fetchall()


def _update_successful_media_record(
    connection: sqlite3.Connection,
    rename_preview_id: int,
    target_path: Path,
    updated_at: str,
) -> None:
    connection.execute(
        "UPDATE media_files "
        "SET file_path = ?, file_name = ?, extension = ?, modified_at = ? "
        "WHERE id = (SELECT media_file_id FROM rename_previews WHERE id = ?)",
        (str(target_path), target_path.name, target_path.suffix.lower(), updated_at, rename_preview_id),
    )
    connection.execute(
        "UPDATE rename_previews SET status = ?, message = ?, updated_at = ? WHERE id = ?",
        ("renamed", None, updated_at, rename_preview_id),
    )


def _sync_scan_index_after_rename(
    connection: sqlite3.Connection,
    rename_preview_id: int,
    source_path: Path,
    target_path: Path,
    updated_at: str,
) -> None:
    """真实重命名成功后同步增量扫描索引。"""

    row = connection.execute(
        "SELECT mf.media_source_id, mf.scan_job_id, ms.path AS media_source_path "
        "FROM rename_previews rp "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE rp.id = ?",
        (rename_preview_id,),
    ).fetchone()
    if row is None:
        return

    media_source_id = int(row["media_source_id"])
    scan_job_id = int(row["scan_job_id"])
    media_source_path = Path(str(row["media_source_path"]))
    source_normalized_path = _normalized_index_path(source_path, media_source_path)
    connection.execute(
        "UPDATE scan_file_index SET status = ?, rename_preview_id = ?, updated_at = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        ("renamed", rename_preview_id, updated_at, media_source_id, source_normalized_path),
    )

    if not target_path.exists():
        return

    stat = target_path.stat()
    _upsert_scan_file_index(
        connection,
        media_source_id,
        scan_job_id,
        target_path,
        media_source_path,
        stat.st_size,
        _file_modified_at(stat),
        "active",
        updated_at,
    )
    target_normalized_path = _normalized_index_path(target_path, media_source_path)
    connection.execute(
        "UPDATE scan_file_index SET rename_preview_id = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        (rename_preview_id, media_source_id, target_normalized_path),
    )


def _build_item_plan(
    settings: AppSettings,
    row: sqlite3.Row,
    duplicate_targets: Counter[str],
) -> tuple[str, str, str, str | None]:
    source_path = Path(str(row["file_path"]))
    target_name = str(row["edited_name"] or row["suggested_name"])
    target_path = source_path.parent / target_name
    status = "ready"
    message = None

    if str(row["status"]) not in READY_STATUSES:
        status = "conflict"
        message = "预览状态不允许重命名"
    elif not _target_name_is_valid(target_name):
        status = "conflict"
        message = "目标文件名非法"
    elif not source_path.exists():
        status = "conflict"
        message = "源文件不存在"
    elif target_path.exists() and source_path.resolve() != target_path.resolve():
        status = "conflict"
        message = "目标文件已存在"
    elif duplicate_targets[str(target_path)] > 1:
        status = "conflict"
        message = "批次内目标文件重复"

    if status == "ready":
        readiness = get_protocol(str(row["path_type"])).check_rename_ready(
            str(source_path),
            str(target_path),
            get_media_source_protocol_context(settings, int(row["media_source_id"])),
        )
        if not readiness.success:
            status = "conflict"
            message = readiness.message

    return str(source_path), str(target_path), status, message


def create_rename_dry_run(settings: AppSettings, rename_preview_ids: list[int]) -> RenameOperation:
    """Create a dry-run safe rename operation without modifying files."""

    now = _utc_now()
    preview_ids = list(dict.fromkeys(rename_preview_ids))
    batch_limit = int(get_effective_settings(settings).get("operations.batch_limit") or 200)
    if len(preview_ids) > batch_limit:
        raise ValueError(f"批量操作数量不能超过 {batch_limit} 条")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = _query_previews(connection, preview_ids)
        target_paths = []
        for row in rows:
            source_path = Path(str(row["file_path"]))
            target_name = str(row["edited_name"] or row["suggested_name"])
            target_paths.append(str(source_path.parent / target_name))
        duplicate_targets = Counter(target_paths)

        item_plans = [
            (row["id"], *_build_item_plan(settings, row, duplicate_targets))
            for row in rows
        ]
        ready_count = sum(1 for item in item_plans if item[3] == "ready")
        conflict_count = sum(1 for item in item_plans if item[3] == "conflict")

        cursor = connection.execute(
            "INSERT INTO rename_operations "
            "(status, mode, total_count, ready_count, conflict_count, renamed_count, failed_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)",
            ("dry_run", "safe_rename", len(item_plans), ready_count, conflict_count, now, now),
        )
        operation_id = int(cursor.lastrowid)
        for preview_id, source_path, target_path, status, message in item_plans:
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(operation_id, rename_preview_id, source_path, target_path, status, message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (operation_id, preview_id, source_path, target_path, status, message, now, now),
            )
        conflict_examples = [
            {"preview_id": preview_id, "target_path": target_path, "message": message}
            for preview_id, _source_path, target_path, status, message in item_plans
            if status == "conflict"
        ][:10]
        record_operation_log(
            settings,
            task_type=TASK_TYPE_RENAME_OPERATION,
            task_id=operation_id,
            level=LEVEL_SUCCESS if conflict_count == 0 else LEVEL_WARNING,
            stage="dry_run_completed",
            message=f"重命名干跑完成：共 {len(item_plans)} 项，可执行 {ready_count} 项，冲突 {conflict_count} 项",
            progress_current=len(item_plans),
            progress_total=len(item_plans),
            detail={
                "ready_count": ready_count,
                "conflict_count": conflict_count,
                "conflict_examples": conflict_examples,
            },
            connection=connection,
        )
        connection.commit()
    cleanup_operation_logs(settings)

    return get_rename_operation(settings, operation_id)


def get_rename_operation(settings: AppSettings, operation_id: int) -> RenameOperation:
    """Get one rename operation with item details."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        operation_row = connection.execute(
            "SELECT id, status, mode, total_count, ready_count, conflict_count, "
            "renamed_count, failed_count, created_at, updated_at "
            "FROM rename_operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if operation_row is None:
            raise ValueError("重命名批次不存在")
        item_rows = connection.execute(
            "SELECT id, operation_id, rename_preview_id, source_path, target_path, status, "
            "message, created_at, updated_at "
            "FROM rename_operation_items WHERE operation_id = ? ORDER BY id",
            (operation_id,),
        ).fetchall()

    return _row_to_operation(operation_row, [_row_to_item(row) for row in item_rows])


def execute_rename_operation(settings: AppSettings, operation_id: int) -> RenameOperation:
    """Execute ready items in a dry-run rename operation."""

    now = _utc_now()
    operation = get_rename_operation(settings, operation_id)
    if operation.status != "dry_run":
        return operation

    renamed_count = 0
    failed_count = 0

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        record_operation_log(
            settings,
            task_type=TASK_TYPE_RENAME_OPERATION,
            task_id=operation_id,
            level=LEVEL_INFO,
            stage="rename_started",
            message=f"开始执行重命名：待处理 {operation.ready_count} 项",
            progress_current=0,
            progress_total=operation.ready_count,
            detail={"operation_status": operation.status},
            connection=connection,
        )
        for item in operation.items:
            if item.status != "ready":
                continue

            try:
                source_path = Path(item.source_path)
                target_path = Path(item.target_path)
                if not source_path.exists():
                    raise ValueError("源文件不存在")
                if target_path.exists() and source_path.resolve() != target_path.resolve():
                    raise ValueError("目标文件已存在")
                media_source_row = connection.execute(
                    "SELECT ms.id AS media_source_id, ms.path_type FROM rename_previews rp "
                    "JOIN media_files mf ON mf.id = rp.media_file_id "
                    "JOIN media_sources ms ON ms.id = mf.media_source_id "
                    "WHERE rp.id = ?",
                    (item.rename_preview_id,),
                ).fetchone()
                path_type = str(media_source_row["path_type"]) if media_source_row else "local"
                context = (
                    get_media_source_protocol_context(settings, int(media_source_row["media_source_id"]))
                    if media_source_row
                    else None
                )
                readiness = get_protocol(path_type).check_rename_ready(str(source_path), str(target_path), context)
                if not readiness.success:
                    raise ValueError(readiness.message)
                source_path.rename(target_path)
                _update_successful_media_record(
                    connection,
                    item.rename_preview_id,
                    target_path,
                    now,
                )
                sync_message = None
                try:
                    _sync_scan_index_after_rename(connection, item.rename_preview_id, source_path, target_path, now)
                except Exception as sync_exc:  # noqa: BLE001 - 文件已重命名，索引失败只记录提示。
                    sync_message = f"索引同步失败：{sync_exc}"
                connection.execute(
                    "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    ("renamed", sync_message, now, item.id),
                )
                renamed_count += 1
            except Exception as exc:  # noqa: BLE001 - per-item failure must be recorded.
                connection.execute(
                    "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    ("failed", str(exc), now, item.id),
                )
                failed_count += 1
                if failed_count <= 20:
                    record_operation_log(
                        settings,
                        task_type=TASK_TYPE_RENAME_OPERATION,
                        task_id=operation_id,
                        level=LEVEL_ERROR,
                        stage="rename_item_failed",
                        message=f"重命名失败：{Path(item.source_path).name}，原因：{exc}",
                        progress_current=renamed_count + failed_count,
                        progress_total=operation.ready_count,
                        detail={
                            "rename_preview_id": item.rename_preview_id,
                            "source_path": item.source_path,
                            "target_path": item.target_path,
                        },
                        connection=connection,
                    )

        item_counts = {
            "ready": 0,
            "conflict": 0,
            "renamed": 0,
            "failed": 0,
        }
        count_rows = connection.execute(
            "SELECT status, COUNT(*) AS item_count "
            "FROM rename_operation_items WHERE operation_id = ? GROUP BY status",
            (operation_id,),
        ).fetchall()
        for row in count_rows:
            status = str(row["status"])
            if status in item_counts:
                item_counts[status] = int(row["item_count"])

        final_status = "completed"
        if item_counts["failed"]:
            final_status = "partial_failed" if item_counts["renamed"] else "failed"
        connection.execute(
            "UPDATE rename_operations "
            "SET status = ?, ready_count = ?, conflict_count = ?, renamed_count = ?, failed_count = ?, updated_at = ? "
            "WHERE id = ?",
            (
                final_status,
                item_counts["ready"],
                item_counts["conflict"],
                item_counts["renamed"],
                item_counts["failed"],
                now,
                operation_id,
            ),
        )
        record_operation_log(
            settings,
            task_type=TASK_TYPE_RENAME_OPERATION,
            task_id=operation_id,
            level=LEVEL_SUCCESS if final_status == "completed" else LEVEL_WARNING,
            stage="rename_completed",
            message=(
                f"重命名执行完成：成功 {item_counts['renamed']} 项，"
                f"失败 {item_counts['failed']} 项，冲突 {item_counts['conflict']} 项"
            ),
            progress_current=item_counts["renamed"] + item_counts["failed"],
            progress_total=operation.ready_count,
            detail={
                "status": final_status,
                "renamed_count": item_counts["renamed"],
                "failed_count": item_counts["failed"],
                "conflict_count": item_counts["conflict"],
            },
            connection=connection,
        )
        connection.commit()
    cleanup_operation_logs(settings)

    updated = get_rename_operation(settings, operation_id)
    return updated
