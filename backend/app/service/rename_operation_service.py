"""Safe rename operation service."""

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path, PurePath
import json
import sqlite3
from urllib.parse import quote, unquote, urlparse

from app.core.config import AppSettings
from app.service.cloud_directory_probe_service import directoryProbe
from app.schema.media import RenameOperation, RenameOperationItem
from app.service.media_source_service import get_media_source_protocol_context
from app.service.operation_log_service import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    TASK_TYPE_RENAME_OPERATION,
    cleanup_operation_logs_best_effort as cleanup_operation_logs,
    record_operation_log,
)
from app.service.remote_operation_service import (
    acquire_remote_operation_lock,
    create_remote_operation_item,
    release_remote_operation_lock,
    update_remote_operation_item_status,
)
from app.service.scan_service import _file_modified_at, _normalized_index_path, _upsert_scan_file_index
from app.service.shared_protocols.registry import get_protocol
from app.service.shared_protocols.registry import UnsupportedProtocolError
from app.service.shared_protocols.webdav import isStrongEtag
from app.service.credential_key import CredentialError
from app.service.external_request_budget_service import ExternalBudgetUnavailable
from app.service.settings_service import get_effective_settings
from app.service.remote_file_index_service import syncRemoteFileIndex
from app.utils.file_operations import moveFileNoReplace
from app.service.local_file_action_service import (
    prepareLocalFileAction,
    updateLocalFileAction,
    verifyLocalFileIdentity,
    verifyLocalMoveOutcome,
)

READY_STATUSES = {"generated", "edited"}
REMOTE_PATH_TYPES = {"webdav"}


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
        error_message=row["error_message"],
    )


def _target_name_is_valid(target_name: str) -> bool:
    if not target_name.strip():
        return False
    return PurePath(target_name).name == target_name and "/" not in target_name and "\\" not in target_name


def _is_remote_path_type(path_type: str) -> bool:
    return path_type in REMOTE_PATH_TYPES


def _build_target_path(source_path: str, target_name: str, path_type: str) -> str:
    if not _is_remote_path_type(path_type):
        return str(Path(source_path).parent / target_name)
    parsed = urlparse(source_path)
    parent_path = parsed.path.rsplit("/", 1)[0]
    safe_target_name = quote(unquote(target_name), safe="")
    target_path = f"{parent_path}/{safe_target_name}" if parent_path else f"/{safe_target_name}"
    return parsed._replace(path=target_path, params="", query="", fragment="").geturl()


def _remote_file_name(remote_path: str) -> str:
    path = urlparse(remote_path).path
    name = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    return name or remote_path


def _query_previews(connection: sqlite3.Connection, preview_ids: list[int]) -> list[sqlite3.Row]:
    if not preview_ids:
        return []
    placeholders = ", ".join("?" for _ in preview_ids)
    from app.service.cloud_directory_path_service import cloudPathEpoch
    epoch = cloudPathEpoch(connection)
    fence = " AND mf.scan_job_id>? " if epoch["revision"] else " "
    return connection.execute(
        "SELECT rp.id, rp.status, rp.suggested_name, rp.edited_name, mf.file_path, "
        "rp.updated_at AS preview_updated_at, mf.id AS media_file_id, mf.file_size, mf.modified_at, "
        "ms.id AS media_source_id, ms.path_type, ms.path AS source_root, ms.enabled, "
        "ms.updated_at AS source_updated_at "
        "FROM rename_previews rp "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "JOIN media_sources ms ON ms.id = mf.media_source_id "
        f"WHERE rp.id IN ({placeholders}) AND ms.deleted_at IS NULL " + fence +
        "AND NOT EXISTS (SELECT 1 FROM scan_jobs job WHERE job.id = mf.scan_job_id AND job.source_invalidated_at IS NOT NULL) "
        "ORDER BY rp.id",
        [*preview_ids, epoch["max_scan_job_id"]] if epoch["revision"] else preview_ids,
    ).fetchall()


def _previewSnapshot(settings: AppSettings, row: sqlite3.Row) -> dict:
    from app.service.execution_guard_service import checkExecutionGuard
    checkExecutionGuard(phase=f"probe:{row['id']}")
    snapshot = dict(row)
    if _is_remote_path_type(str(row["path_type"])):
        info = get_protocol(str(row["path_type"])).get_file_info(
            str(row["file_path"]), get_media_source_protocol_context(settings, int(row["media_source_id"])),
        )
        if not isStrongEtag(info.version):
            raise ValueError("WebDAV 未提供强 ETag，无法安全执行重命名")
        snapshot["remoteIdentity"] = [info.version, info.file_size, info.modified_at]
    else:
        stat = Path(str(row["file_path"])).stat()
        snapshot["fileIdentity"] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    return snapshot


def _validateItemSnapshot(settings: AppSettings, connection: sqlite3.Connection, item: RenameOperationItem) -> sqlite3.Row:
    stored = connection.execute(
        "SELECT snapshot_json FROM rename_operation_items WHERE id = ?", (item.id,),
    ).fetchone()
    if stored is None or not stored[0]:
        raise ValueError("干跑计划缺少安全快照，请重新生成干跑")
    rows = _query_previews(connection, [item.rename_preview_id])
    if not rows:
        raise ValueError("预览或媒体源已删除，请重新生成干跑")
    row = rows[0]
    if not row["enabled"] or row["status"] not in READY_STATUSES:
        raise ValueError("预览状态或媒体源已变化，请重新生成干跑")
    if json.loads(stored[0]) != _previewSnapshot(settings, row):
        raise ValueError("预览、媒体源或源文件已变化，请重新生成干跑")
    targetName = str(row["edited_name"] or row["suggested_name"])
    if str(row["file_path"]) != item.source_path or _build_target_path(
        str(row["file_path"]), targetName, str(row["path_type"]),
    ) != item.target_path:
        raise ValueError("干跑目标已变化，请重新生成干跑")
    return row


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


def _update_successful_remote_media_record(
    connection: sqlite3.Connection,
    rename_preview_id: int,
    target_path: str,
    updated_at: str,
) -> None:
    target_name = _remote_file_name(target_path)
    extension = "." + target_name.rsplit(".", 1)[-1].lower() if "." in target_name else ""
    connection.execute(
        "UPDATE media_files "
        "SET file_path = ?, file_name = ?, extension = ?, modified_at = ? "
        "WHERE id = (SELECT media_file_id FROM rename_previews WHERE id = ?)",
        (target_path, target_name, extension, updated_at, rename_preview_id),
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
    from app.service.execution_guard_service import checkExecutionGuard
    checkExecutionGuard(phase=f"probe:{row['id']}")
    source_path_value = str(row["file_path"])
    path_type = str(row["path_type"])
    target_name = str(row["edited_name"] or row["suggested_name"])
    target_path_value = _build_target_path(source_path_value, target_name, path_type)
    status = "ready"
    message = None

    if str(row["status"]) not in READY_STATUSES:
        status = "conflict"
        message = "预览状态不允许重命名"
    elif not _target_name_is_valid(target_name):
        status = "conflict"
        message = "目标文件名非法"
    elif not _is_remote_path_type(path_type):
        source_path = Path(source_path_value)
        target_path = Path(target_path_value)
        if not source_path.exists():
            status = "conflict"
            message = "源文件不存在"
        elif target_path.exists() and source_path.resolve() != target_path.resolve():
            status = "conflict"
            message = "目标文件已存在"
    if status == "ready" and duplicate_targets[target_path_value] > 1:
        status = "conflict"
        message = "批次内目标文件重复"

    if status == "ready":
        readiness = get_protocol(path_type).check_rename_ready(
            source_path_value,
            target_path_value,
            get_media_source_protocol_context(settings, int(row["media_source_id"])),
        )
        if not readiness.success:
            status = "conflict"
            message = readiness.message

    return source_path_value, target_path_value, status, message


@directoryProbe("rename_dry_run")
def create_rename_dry_run(settings: AppSettings, rename_preview_ids: list[int], *, auditContext=None) -> RenameOperation:
    """慢探测不持数据库事务；发布干跑前短事务复核元数据和当前权限。"""

    now = _utc_now()
    preview_ids = list(dict.fromkeys(rename_preview_ids))
    batch_limit = int(get_effective_settings(settings).get("operations.batch_limit") or 200)
    if len(preview_ids) > batch_limit:
        raise ValueError(f"批量操作数量不能超过 {batch_limit} 条")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        rows = _query_previews(connection, preview_ids)
        databaseSnapshot = [dict(row) for row in rows]
        connection.commit()
        target_paths = []
        for row in rows:
            target_name = str(row["edited_name"] or row["suggested_name"])
            target_paths.append(_build_target_path(str(row["file_path"]), target_name, str(row["path_type"])))
        duplicate_targets = Counter(target_paths)

        item_plans = [
            (row["id"], *_build_item_plan(settings, row, duplicate_targets))
            for row in rows
        ]
        rowsById = {int(row["id"]): row for row in rows}
        snapshots = {previewId: json.dumps(_previewSnapshot(settings, rowsById[previewId]), ensure_ascii=False)
                     for previewId, _, _, status, _ in item_plans if status == "ready"}
        ready_count = sum(1 for item in item_plans if item[3] == "ready")
        conflict_count = sum(1 for item in item_plans if item[3] == "conflict")

        # 探测可能耗时或阻塞，提交只核对数据库版本，不在写锁内再次探测文件。
        connection.execute("BEGIN IMMEDIATE")
        from app.service.execution_guard_service import checkExecutionGuard
        checkExecutionGuard(connection)
        if databaseSnapshot != [dict(row) for row in _query_previews(connection, preview_ids)]:
            raise ValueError("干跑探测期间预览或媒体源已变化，请重新核对")
        if auditContext is not None and auditContext.actor is not None:
            from app.service.file_task_queue_service import requireFileTaskActor
            requireFileTaskActor(connection, "rename_operation", auditContext.actor.id)

        cursor = connection.execute(
            "INSERT INTO rename_operations "
            "(status, mode, total_count, ready_count, conflict_count, renamed_count, failed_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)",
            ("dry_run", "safe_rename", len(item_plans), ready_count, conflict_count, now, now),
        )
        operation_id = int(cursor.lastrowid)
        for preview_id, source_path, target_path, status, message in item_plans:
            snapshotJson = snapshots.get(preview_id)
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(operation_id, rename_preview_id, source_path, target_path, status, message, created_at, updated_at, snapshot_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (operation_id, preview_id, source_path, target_path, status, message, now, now, snapshotJson),
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
            "renamed_count, failed_count, created_at, updated_at, error_message "
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


def execute_rename_operation(settings: AppSettings, operation_id: int, *, executionToken: str | None = None) -> RenameOperation:
    """Execute ready items in a dry-run rename operation."""

    now = _utc_now()
    operation = get_rename_operation(settings, operation_id)
    if executionToken is None and operation.status != "dry_run":
        return operation

    renamed_count = 0
    failed_count = 0
    synchronousClaim = executionToken is None

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        from app.service.rename_queue_service import requireRenameLease, RenameLeaseError
        def guard():
            if executionToken is not None:
                requireRenameLease(
                    connection,
                    operation_id,
                    executionToken,
                    requireAuditIntent=not synchronousClaim,
                )

        def saveProgress(succeeded: bool):
            nonlocal renamed_count, failed_count
            # 各项结果提交时同步批次计数，进程中断也能读到已完成的真实数量。
            connection.execute("UPDATE rename_operations SET renamed_count=renamed_count+?,failed_count=failed_count+?,"
                "ready_count=MAX(0,ready_count-1),updated_at=? WHERE id=?",
                (int(succeeded), int(not succeeded), _utc_now(), operation_id))
            counts = connection.execute("SELECT renamed_count,failed_count FROM rename_operations WHERE id=?", (operation_id,)).fetchone()
            renamed_count, failed_count = int(counts[0]), int(counts[1])
            processed = renamed_count + failed_count
            if processed == operation.ready_count or processed % max(1, (operation.ready_count + 99) // 100) == 0:
                record_operation_log(settings, task_type=TASK_TYPE_RENAME_OPERATION, task_id=operation_id,
                    level=LEVEL_INFO, stage="rename_progress", message=f"重命名进度：成功 {renamed_count} 项，失败 {failed_count} 项",
                    progress_current=processed, progress_total=operation.ready_count, connection=connection)

        if synchronousClaim:
            from app.service.file_task_queue_service import claimSynchronousFileTask
            connection.execute("BEGIN IMMEDIATE")
            conflict = connection.execute(
                "SELECT 1 FROM rename_operation_items mine "
                "JOIN rename_operation_items other ON other.rename_preview_id = mine.rename_preview_id "
                "JOIN rename_operations active ON active.id = other.operation_id "
                "WHERE mine.operation_id = ? AND mine.status = 'ready' "
                "AND (active.status IN ('queued','running') OR active.execution_token IS NOT NULL) "
                "AND active.id <> ? LIMIT 1",
                (operation_id, operation_id),
            ).fetchone()
            if conflict:
                raise ValueError("所选预览正在其他批次执行，请稍后重试")
            executionToken = claimSynchronousFileTask(
                connection,
                "rename_operation",
                operation_id,
                "dry_run",
            )
            connection.commit()
        else:
            guard()
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
        connection.commit()
        for item in operation.items:
            if item.status != "ready":
                continue

            guard()
            localActionId = None
            itemSucceeded = False
            systemicFailure = False
            systemicErrorType = None
            try:
                media_source_row = _validateItemSnapshot(settings, connection, item)
                path_type = str(media_source_row["path_type"]) if media_source_row else "local"
                if _is_remote_path_type(path_type):
                    if media_source_row is None:
                        raise ValueError("远程媒体源不存在")
                    media_source_id = int(media_source_row["media_source_id"])
                    lock_key = f"media-source:{media_source_id}:write"
                    lock = acquire_remote_operation_lock(
                        settings,
                        media_source_id=media_source_id,
                        lock_key=lock_key,
                        owner="system",
                        task_type=TASK_TYPE_RENAME_OPERATION,
                        task_id=operation_id,
                        ttl_seconds=300,
                    )
                    remote_item = None
                    remoteApplied = False
                    remoteResultUnknown = False
                    try:
                        remote_item = create_remote_operation_item(
                            settings,
                            media_source_id=media_source_id,
                            operation_type="rename",
                            idempotency_key=f"rename-operation:{operation_id}:item:{item.id}",
                            source_path=item.source_path,
                            target_path=item.target_path,
                            source_version=json.loads(connection.execute(
                                "SELECT snapshot_json FROM rename_operation_items WHERE id = ?", (item.id,),
                            ).fetchone()[0])["remoteIdentity"][0],
                            recovery={
                                "operation_id": operation_id,
                                "operation_item_id": item.id,
                                "rename_preview_id": item.rename_preview_id,
                            },
                        )
                        context = get_media_source_protocol_context(settings, media_source_id)
                        snapshot = json.loads(connection.execute(
                            "SELECT snapshot_json FROM rename_operation_items WHERE id = ?", (item.id,),
                        ).fetchone()[0])
                        context = replace(context, expected_source_version=snapshot["remoteIdentity"][0])
                        protocol = get_protocol(path_type)
                        mover = getattr(protocol, "move_file", None)
                        if mover is None:
                            raise ValueError("当前协议不支持远程真实重命名")
                        guard()
                        move_result = mover(item.source_path, item.target_path, context)
                        if not move_result.success:
                            remoteResultUnknown = move_result.indeterminate
                            raise ValueError(move_result.message)
                        remoteApplied = True
                        update_remote_operation_item_status(
                            settings,
                            remote_item.id,
                            "remote_applied",
                            recovery={
                                "operation_id": operation_id,
                                "operation_item_id": item.id,
                                "rename_preview_id": item.rename_preview_id,
                            },
                        )
                        targetInfo = protocol.get_file_info(item.target_path, context)
                        _update_successful_remote_media_record(
                            connection,
                            item.rename_preview_id,
                            item.target_path,
                            now,
                        )
                        syncRemoteFileIndex(connection, item.rename_preview_id, item.source_path, targetInfo, "renamed", now)
                        connection.execute(
                            "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                            ("renamed", None, now, item.id),
                        )
                        # 业务记录与完成标记同事务，远端动作不在此写事务内。
                        connection.execute(
                            "UPDATE remote_operation_items SET status = 'completed', error_message = NULL, "
                            "target_version = ?, updated_at = ? WHERE id = ?", (targetInfo.version, _utc_now(), remote_item.id),
                        )
                        renamed_count += 1
                        saveProgress(True)
                        connection.commit()
                    except Exception as remote_exc:  # noqa: BLE001 - 单条远程失败必须记录。
                        connection.rollback()
                        if remote_item is not None:
                            update_remote_operation_item_status(
                                settings,
                                remote_item.id,
                                "recovery_required" if remoteApplied or remoteResultUnknown else "failed",
                                error_message=(
                                    f"远端已移动，业务提交未完成，需要恢复：{remote_exc}"
                                    if remoteApplied
                                    else f"远端 MOVE 已发出但结果不明确，需要人工核查：{remote_exc}"
                                    if remoteResultUnknown
                                    else str(remote_exc)
                                ),
                            )
                        raise
                    finally:
                        if lock is not None:
                            try:
                                release_remote_operation_lock(settings, lock_key, lock.lease_token)
                            except Exception as releaseExc:  # noqa: BLE001 - 锁释放失败不能抹掉已提交的真实结果。
                                record_operation_log(settings, task_type=TASK_TYPE_RENAME_OPERATION, task_id=operation_id,
                                    level=LEVEL_WARNING, stage="rename_lock_release_failed",
                                    message="媒体源锁释放失败，保留已提交文件结果；请核对遗留锁",
                                    detail={"error_type": type(releaseExc).__name__})
                    continue
                source_path = Path(item.source_path)
                target_path = Path(item.target_path)
                if not source_path.exists():
                    raise ValueError("源文件不存在")
                if target_path.exists() and source_path.resolve() != target_path.resolve():
                    raise ValueError("目标文件已存在")
                context = (
                    get_media_source_protocol_context(settings, int(media_source_row["media_source_id"]))
                    if media_source_row
                    else None
                )
                readiness = get_protocol(path_type).check_rename_ready(str(source_path), str(target_path), context)
                if not readiness.success:
                    raise ValueError(readiness.message)
                savedSnapshot = json.loads(connection.execute(
                    "SELECT snapshot_json FROM rename_operation_items WHERE id = ?", (item.id,),
                ).fetchone()[0])
                guard()
                localActionId = prepareLocalFileAction(
                    settings, mediaSourceId=int(media_source_row["media_source_id"]),
                    actionType="rename", recordId=item.id, sourcePath=str(source_path), targetPath=str(target_path),
                    expectedIdentity=savedSnapshot["fileIdentity"],
                )
                guard()
                verifyLocalFileIdentity(source_path, savedSnapshot["fileIdentity"])
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "prepared",
                    actionPhase="move_issued",
                )
                moveFileNoReplace(source_path, target_path)
                targetIdentity = verifyLocalMoveOutcome(
                    source_path,
                    target_path,
                    savedSnapshot["fileIdentity"],
                )
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "applied",
                    targetIdentity=targetIdentity,
                    actionPhase="target_confirmed",
                )
                _update_successful_media_record(
                    connection,
                    item.rename_preview_id,
                    target_path,
                    now,
                )
                _sync_scan_index_after_rename(connection, item.rename_preview_id, source_path, target_path, now)
                connection.execute(
                    "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    ("renamed", None, now, item.id),
                )
                updateLocalFileAction(
                    settings,
                    localActionId,
                    "completed",
                    connection=connection,
                    actionPhase="business_committed",
                )
                renamed_count += 1
                itemSucceeded = True
            except Exception as exc:  # noqa: BLE001 - per-item failure must be recorded.
                connection.rollback()
                if isinstance(exc, RenameLeaseError):
                    raise
                systemicFailure = isinstance(
                    exc,
                    (sqlite3.Error, CredentialError, ExternalBudgetUnavailable, UnsupportedProtocolError),
                )
                systemicErrorType = type(exc).__name__ if systemicFailure else None
                if localActionId is not None:
                    updateLocalFileAction(settings, localActionId, "recovery_required", message=str(exc))
                itemMessage = (
                    f"本地动作未完成一致性提交，请停服核对恢复：{exc}" if localActionId is not None else str(exc)
                )
                connection.execute(
                    "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                    ("failed", itemMessage, now, item.id),
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
            saveProgress(itemSucceeded)
            connection.commit()
            if systemicFailure:
                record_operation_log(
                    settings,
                    task_type=TASK_TYPE_RENAME_OPERATION,
                    task_id=operation_id,
                    level=LEVEL_ERROR,
                    stage="rename_batch_stopped",
                    message=f"系统性故障已停止后续重命名：{systemicErrorType}",
                    progress_current=renamed_count + failed_count,
                    progress_total=operation.ready_count,
                    detail={"error_type": systemicErrorType},
                )
                break

        guard()
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
        finalGuard = " AND status='running' AND execution_token=? AND julianday(execution_expires_at)>julianday('now')" if executionToken is not None else ""
        finalized = connection.execute(
            "UPDATE rename_operations "
            "SET status = ?, ready_count = ?, conflict_count = ?, renamed_count = ?, failed_count = ?, updated_at = ? "
            "WHERE id = ?" + finalGuard,
            (
                final_status,
                item_counts["ready"],
                item_counts["conflict"],
                item_counts["renamed"],
                item_counts["failed"],
                now,
                operation_id,
            ) + ((executionToken,) if executionToken is not None else ()),
        )
        if finalized.rowcount != 1:
            connection.rollback()
            raise RenameLeaseError("重命名执行授权已失效；已提交结果保留，终态需核对")
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
    if synchronousClaim:
        from app.service.file_task_queue_service import releaseSynchronousFileTask
        releaseSynchronousFileTask(settings, "rename_operation", operation_id, executionToken)
    cleanup_operation_logs(settings)

    updated = get_rename_operation(settings, operation_id)
    return updated
