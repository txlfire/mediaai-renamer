"""Pending file queue service."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import errno
import sqlite3
from urllib.parse import unquote, urlparse

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.schema.ai_parse import AiParseResult
from app.schema.pending_files import PendingFile
from app.schema.pending_move import PendingMoveTask
from app.service.ai_parse_service import parse_media_with_ai
from app.utils.filename_parser import parse_media_filename
from app.utils.file_operations import moveFileNoReplace, copyFileNoReplace
from app.service.local_file_action_service import prepareLocalFileAction, updateLocalFileAction, localFileIdentity

logger = get_logger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_pending_file(row: sqlite3.Row) -> PendingFile:
    return PendingFile(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        scan_job_id=int(row["scan_job_id"]),
        file_path=str(row["file_path"]),
        file_name=str(row["file_name"]),
        extension=str(row["extension"]),
        file_size=int(row["file_size"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def _get_pending_row(connection: sqlite3.Connection, pending_file_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
        "file_size, reason, status, created_at FROM pending_files WHERE id = ?",
        (pending_file_id,),
    ).fetchone()
    if row is None:
        raise ValueError("待处理文件不存在")
    return row


def add_pending_file(
    connection: sqlite3.Connection,
    media_source_id: int,
    scan_job_id: int,
    file_path: str | Path,
    file_size: int,
    reason: str,
    *,
    staging: bool = False,
) -> None:
    """Insert one file into the pending queue using the caller's transaction."""

    file_path_text = str(file_path)
    parsed_path = urlparse(file_path_text).path if "://" in file_path_text else file_path_text
    file_name = Path(unquote(parsed_path)).name
    table = "temp.scan_pending_stage" if staging else "pending_files"
    connection.execute(
        f"INSERT INTO {table} "
        "(media_source_id, scan_job_id, file_path, file_name, extension, file_size, reason, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            media_source_id,
            scan_job_id,
            file_path_text,
            file_name,
            Path(file_name).suffix.lower(),
            file_size,
            reason,
            "pending",
            _utc_now(),
        ),
    )


def list_pending_files(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
) -> list[PendingFile]:
    """List pending files with optional source/job filters."""

    conditions = ["status IN ('pending', 'moving')"]
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)
    where_clause = f" WHERE {' AND '.join(conditions)}"

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
            "file_size, reason, CASE WHEN status = 'moving' AND EXISTS ("
            "SELECT 1 FROM local_file_actions action WHERE action.action_type = 'pending_move' "
            "AND action.record_id = pending_files.id AND action.status = 'recovery_required') "
            f"THEN 'recovery_required' ELSE status END AS status, created_at FROM pending_files{where_clause} ORDER BY id",
            params,
        ).fetchall()
    return [_row_to_pending_file(row) for row in rows]


def listPendingFilesPage(settings: AppSettings, media_source_id: int | None = None,
                         scan_job_id: int | None = None, *, page: int = 1, page_size: int = 50) -> dict:
    """按同一快照读取待处理页及全局状态计数，保留恢复状态投影。"""
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("分页参数超出范围")
    conditions = ["status IN ('pending','moving')"]
    params: list[object] = []
    for column, value in (("media_source_id", media_source_id), ("scan_job_id", scan_job_id)):
        if value is not None:
            conditions.append(f"{column} = ?")
            params.append(value)
    where = " WHERE " + " AND ".join(conditions)
    projected_status = "CASE WHEN status = 'moving' AND recovery.record_id IS NOT NULL " \
        "THEN 'recovery_required' ELSE status END"
    source = " FROM pending_files LEFT JOIN (SELECT DISTINCT record_id FROM local_file_actions " \
        "WHERE action_type = 'pending_move' AND status = 'recovery_required') recovery " \
        "ON recovery.record_id = pending_files.id"
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        groups = connection.execute(f"SELECT {projected_status} AS visible_status, COUNT(*) AS count "
            f"{source}{where} GROUP BY visible_status", params).fetchall()
        stats = {str(row["visible_status"]): int(row["count"]) for row in groups}
        total = sum(stats.values())
        page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = connection.execute("SELECT id,media_source_id,scan_job_id,file_path,file_name,extension,file_size,reason,"
            f"{projected_status} AS status,created_at{source}{where} ORDER BY id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size]).fetchall()
    return {"items": [_row_to_pending_file(row) for row in rows], "total": total,
            "stats": stats, "page": page, "page_size": page_size}


def remove_pending_file(settings: AppSettings, pending_file_id: int) -> PendingFile:
    """Mark one pending file task as removed without touching the real file."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = _get_pending_row(connection, pending_file_id)
        if row["status"] == "moving" or connection.execute(
            "SELECT 1 FROM local_file_actions WHERE action_type = 'pending_move' AND record_id = ? "
            "AND status IN ('prepared', 'applied', 'recovery_required') LIMIT 1", (pending_file_id,),
        ).fetchone():
            raise ValueError("待处理文件正在迁移或需要恢复，暂不能移除")
        connection.execute(
            "UPDATE pending_files SET status = ? WHERE id = ?",
            ("removed", pending_file_id),
        )
        connection.commit()
    return _row_to_pending_file(row)


def parse_pending_file_with_ai(settings: AppSettings, pending_file_id: int) -> AiParseResult:
    """Run AI structured parsing for one pending file without mutating queue state."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_pending_row(connection, pending_file_id)
        if row["status"] != "pending":
            raise ValueError("待处理文件正在迁移或需要恢复，暂不能提交 AI")

    return parse_media_with_ai(
        settings,
        source_module="pending_file",
        source_record_id=int(row["id"]),
        file_name=str(row["file_name"]),
        file_path=str(row["file_path"]),
        parsed=parse_media_filename(str(row["file_name"])),
    )


def clear_pending_files(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
) -> int:
    """Mark matching pending file tasks as removed."""

    conditions = ["status = 'pending'"]
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        cursor = connection.execute(
            f"UPDATE pending_files SET status = 'removed' WHERE {' AND '.join(conditions)}",
            params,
        )
        connection.commit()
        return int(cursor.rowcount)


def move_pending_files(
    settings: AppSettings,
    pending_file_ids: list[int],
    target_directory: str,
    *, context=None, response=None,
) -> list[PendingFile] | PendingMoveTask:
    """旧同步返回契约保留，但执行期间也占用共同文件写槽。"""
    from app.service.audit_service import AuditWriteContext
    from app.service.pending_move_service import acceptPendingMove, executeAcceptedPendingMove, finishInterruptedPendingMove
    import secrets
    target = Path(target_directory)
    if not target.exists() or not target.is_dir():
        raise ValueError("目标目录不存在")
    task = acceptPendingMove(settings, pending_file_ids, str(target.absolute()), secrets.token_urlsafe(18), context or AuditWriteContext(), synchronous=True)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        token = connection.execute("SELECT execution_token FROM pending_move_tasks WHERE id=?", (task.id,)).fetchone()[0]
    try:
        result = executeAcceptedPendingMove(settings, task.id, token)
        if result.status != "completed":
            if response is not None:
                response.status_code = 207
            return result
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return [_row_to_pending_file(_get_pending_row(connection, item.pending_file_id)) for item in result.items]
    finally:
        # 同步调用没有独立worker；仅在实际文件调用退出后释放，不由HTTP超时释放。
        try:
            finishInterruptedPendingMove(settings, task.id, token, "同步迁移已结束，未完成条目请核对", processExited=True)
        except Exception as exc:  # noqa: BLE001 - 已知文件结果不被审计故障覆盖，遗留槽供停服核对。
            if response is not None:
                response.headers["X-MediaAI-Audit-State"] = "pending"
            from app.core.logger import get_logger
            try:
                get_logger(__name__).error("迁移结果收尾待核对 task_id=%s error_type=%s", task.id, type(exc).__name__)
            except Exception:  # noqa: BLE001 - 辅助诊断失败也不能覆盖实际迁移结果。
                pass


def _move_pending_files(settings, pending_file_ids, target_directory, *, taskId=None, executionToken=None):
    """Move selected files to a target directory and mark tasks as moved."""
    from app.service.file_task_queue_service import FileTaskLeaseError
    from app.service.pending_move_service import checkPendingMove, requirePendingMoveOwnership, directoryIdentity
    import json

    target_path = Path(target_directory)
    if not target_path.exists() or not target_path.is_dir():
        raise ValueError("目标目录不存在")
    if not pending_file_ids:
        raise ValueError("请选择待迁移文件")

    moved: list[PendingFile] = []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        placeholders = ", ".join("?" for _ in pending_file_ids)
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
            f"file_size, reason, status, created_at FROM pending_files WHERE status = ? AND id IN ({placeholders})",
            ["moving" if executionToken else "pending", *pending_file_ids],
        ).fetchall()
        if len(rows) != len(set(pending_file_ids)):
            raise ValueError("所选记录不存在或不处于待处理状态，请刷新后重试")
        for row in rows:
            if executionToken:
                checkPendingMove(settings, connection, taskId, executionToken, row["id"])
            source_path = Path(str(row["file_path"]))
            if not source_path.exists():
                suffix = f"；此前 {len(moved)} 项已迁移并提交，请刷新列表" if moved else ""
                logger.warning("待处理迁移源文件不存在 pending_file_id=%s source_path=%s", row["id"], source_path)
                raise ValueError(f"源文件不存在：{source_path.name}{suffix}")
            destination = target_path / source_path.name
            if destination.exists():
                suffix = f"；此前 {len(moved)} 项已迁移并提交，请刷新列表" if moved else ""
                logger.warning("待处理迁移目标文件已存在 pending_file_id=%s target_path=%s", row["id"], destination)
                raise ValueError(f"目标文件已存在：{destination.name}{suffix}")
            actionId = None
            copyStarted = False
            identity = localFileIdentity(source_path)
            if identity[2] != row["file_size"]:
                raise ValueError("源文件大小已变化，请重新扫描并选择")
            try:
                actionId = prepareLocalFileAction(
                    settings, mediaSourceId=int(row["media_source_id"]), actionType="pending_move",
                    recordId=int(row["id"]), sourcePath=str(source_path), targetPath=str(destination), expectedIdentity=identity,
                    taskId=taskId, executionToken=executionToken,
                )
                if executionToken:
                    from app.service.operation_log_service import record_operation_log
                    record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info",
                        stage="pending_move_item_started", message="迁移条目开始执行", detail={"pending_file_id": row["id"]})
                if executionToken:
                    checkPendingMove(settings, connection, taskId, executionToken, row["id"])
                if localFileIdentity(source_path) != identity:
                    raise FileTaskLeaseError("迁移前源文件身份已变化")
                try:
                    updateLocalFileAction(
                        settings,
                        actionId,
                        "prepared",
                        actionPhase="move_issued",
                    )
                    moveFileNoReplace(source_path, destination)
                except OSError as moveError:
                    if moveError.errno != errno.EXDEV:
                        raise
                    copyStarted = True
                    updateLocalFileAction(
                        settings,
                        actionId,
                        "prepared",
                        actionPhase="copy_started",
                    )
                    if executionToken:
                        record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info",
                            stage="pending_move_copy_started", message="跨文件系统复制开始，源文件暂不删除", detail={"pending_file_id": row["id"]})
                    targetIdentity = copyFileNoReplace(source_path, destination, identity)
                    updateLocalFileAction(
                        settings,
                        actionId,
                        "prepared",
                        targetIdentity=targetIdentity,
                        actionPhase="copy_started",
                    )
                    if localFileIdentity(source_path) != identity or localFileIdentity(destination) != targetIdentity:
                        raise ValueError("复制后文件身份发生变化，停止删除源文件")
                    if executionToken:
                        record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info",
                            stage="pending_move_copy_completed", message="复制已完成，核对权限及身份后才删除源文件", detail={"pending_file_id": row["id"]})
                        checkPendingMove(settings, connection, taskId, executionToken, row["id"])
                    source_path.unlink()
                updateLocalFileAction(
                    settings,
                    actionId,
                    "applied",
                    actionPhase="target_confirmed",
                )
                expectedTarget = targetIdentity if copyStarted else identity
                if localFileIdentity(destination) != expectedTarget:
                    raise FileTaskLeaseError("迁移后目标文件身份已变化，请核对文件动作")
                if executionToken:
                    task = requirePendingMoveOwnership(connection, taskId, executionToken, row["id"])
                    if directoryIdentity(target_path) != json.loads(task["target_identity_json"]):
                        raise FileTaskLeaseError("迁移后目标目录身份已变化")
                connection.execute("BEGIN IMMEDIATE")
                if executionToken:
                    requirePendingMoveOwnership(connection, taskId, executionToken, row["id"])
                connection.execute(
                    "UPDATE pending_files SET status = ?, file_path = ? WHERE id = ?",
                    ("moved", str(destination), row["id"]),
                )
                updateLocalFileAction(
                    settings,
                    actionId,
                    "completed",
                    connection=connection,
                    actionPhase="business_committed",
                )
                if executionToken:
                    connection.execute("UPDATE pending_move_items SET status='moved',message=NULL WHERE task_id=? AND pending_file_id=?", (taskId, row["id"]))
                    connection.execute("UPDATE pending_move_tasks SET moved_count=(SELECT COUNT(*) FROM pending_move_items "
                        "WHERE task_id=? AND status='moved'),updated_at=? WHERE id=?", (taskId, _utc_now(), taskId))
                    from app.service.operation_log_service import record_operation_log
                    counts = connection.execute("SELECT moved_count,total_count FROM pending_move_tasks WHERE id=?", (taskId,)).fetchone()
                    record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info", stage="pending_move_item",
                        message="迁移条目已提交", progress_current=counts[0], progress_total=counts[1], detail={"pending_file_id": row["id"]}, connection=connection)
                connection.commit()
                moved.append(_row_to_pending_file(_get_pending_row(connection, int(row["id"]))))
            except (OSError, sqlite3.Error, ValueError) as exc:
                connection.rollback()
                if actionId is not None:
                    unchanged = False
                    try:
                        targetAbsent = False
                        try:
                            destination.lstat()
                        except FileNotFoundError:
                            targetAbsent = True
                        unchanged = localFileIdentity(source_path) == identity and (
                            targetAbsent or (isinstance(exc, FileExistsError) and not copyStarted)
                        )
                    except (OSError, ValueError):
                        pass
                    if unchanged:
                        connection.execute("UPDATE pending_files SET status = 'pending' WHERE id = ?", (row["id"],))
                        updateLocalFileAction(settings, actionId, "not_applied", message=str(exc), connection=connection)
                        connection.commit()
                    else:
                        updateLocalFileAction(settings, actionId, "recovery_required", message=str(exc))
                suffix = f"；此前 {len(moved)} 项已迁移并提交，请刷新列表" if moved else ""
                if isinstance(exc, FileTaskLeaseError):
                    raise
                raise ValueError(f"迁移未完成，文件状态已保留；未完成意图请停服核对恢复{suffix}") from exc
    return moved
