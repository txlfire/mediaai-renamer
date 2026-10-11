"""本地文件动作意图与状态；不在数据库写事务中执行文件操作。"""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat

from app.core.config import AppSettings

ACTIVE_STATUSES = ("prepared", "applied", "recovery_required")
ACTION_TYPES = {"rename", "rollback", "pending_move"}
ACTION_PHASES = {
    "intent_recorded",
    "move_issued",
    "copy_started",
    "target_confirmed",
    "business_committed",
    "closed_not_applied",
}


def localFileIdentity(path: str | Path) -> list[int]:
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("本地动作仅支持普通文件，不处理目录或符号链接")
    if info.st_ino <= 0:
        raise ValueError("文件系统未提供稳定文件标识，无法安全记录本地动作")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def actualDirectoryEntryName(path: str | Path) -> str:
    """返回目录中实际保存的名称拼写，避免 Windows 路径大小写折叠。"""

    target = Path(path).absolute()
    targetKey = os.path.normcase(str(target))
    with os.scandir(target.parent) as entries:
        matches = [entry.name for entry in entries if os.path.normcase(os.path.abspath(entry.path)) == targetKey]
    if len(matches) != 1:
        raise ValueError("源文件目录项不存在或名称不唯一")
    return matches[0]


def verifyLocalFileIdentity(path: str | Path, expectedIdentity: list[int]) -> None:
    """在文件动作发出前复核源文件仍是计划中的同一对象。"""

    if localFileIdentity(path) != expectedIdentity:
        raise ValueError("文件动作准备后源文件身份已变化，拒绝执行")


def verifyLocalMoveOutcome(
    sourcePath: str | Path,
    targetPath: str | Path,
    expectedIdentity: list[int],
) -> list[int]:
    """确认移动后的目标身份；无法确证时交由停服恢复流程处理。"""

    source = os.path.abspath(os.fspath(sourcePath))
    target = os.path.abspath(os.fspath(targetPath))
    targetIdentity = localFileIdentity(target)
    if targetIdentity != expectedIdentity:
        raise ValueError("文件动作已发出，但目标文件身份无法确认")
    if os.path.normcase(source) != os.path.normcase(target):
        try:
            localFileIdentity(source)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("文件动作已发出，但源路径仍存在，结果需要人工核查")
    return targetIdentity


def prepareLocalFileAction(
    settings: AppSettings, *, mediaSourceId: int, actionType: str, recordId: int,
    sourcePath: str, targetPath: str, expectedIdentity: list[int] | None = None,
    taskId: int | None = None, executionToken: str | None = None,
) -> int:
    if actionType not in ACTION_TYPES or "://" in sourcePath or "://" in targetPath:
        raise ValueError("本地动作类型或路径不合法")
    source = str(Path(sourcePath).absolute())
    target = str(Path(targetPath).absolute())
    sourceKey = os.path.normcase(source)
    targetKey = os.path.normcase(target)
    identity = localFileIdentity(source)
    sourceEntryName = actualDirectoryEntryName(source)
    targetEntryName = Path(target).name
    if expectedIdentity is not None and identity != expectedIdentity:
        raise ValueError("源文件已变化，请重新生成计划")
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        occupied = connection.execute(
            "SELECT 1 FROM local_file_actions WHERE status IN ('prepared', 'applied', 'recovery_required') "
            "AND (source_key IN (?, ?) OR target_key IN (?, ?)) LIMIT 1",
            (sourceKey, targetKey, sourceKey, targetKey),
        ).fetchone()
        if occupied:
            raise ValueError("文件存在未完成本地动作，请先核对和恢复")
        sourceRow = connection.execute("SELECT enabled FROM media_sources WHERE id = ?", (mediaSourceId,)).fetchone()
        if sourceRow is None or not sourceRow[0]:
            raise ValueError("媒体源不存在或已禁用")
        if actionType == "pending_move":
            if executionToken is not None:
                from app.service.pending_move_service import requirePendingMoveItem
                requirePendingMoveItem(connection, taskId, executionToken, recordId)
            pendingRow = connection.execute(
                "SELECT status, file_path, media_source_id FROM pending_files WHERE id = ?", (recordId,),
            ).fetchone()
            if pendingRow is None or pendingRow[0] != ("moving" if executionToken else "pending") or pendingRow[2] != mediaSourceId or os.path.normcase(os.path.abspath(pendingRow[1])) != sourceKey:
                raise ValueError("待处理记录已变化或正在迁移")
            connection.execute("UPDATE pending_files SET status = 'moving' WHERE id = ?", (recordId,))
            if executionToken is not None:
                connection.execute("UPDATE pending_move_items SET status='moving' WHERE task_id=? AND pending_file_id=?", (taskId, recordId))
        cursor = connection.execute(
            "INSERT INTO local_file_actions (media_source_id, action_type, record_id, source_path, target_path, "
            "source_key, target_key, identity_json, source_entry_name, target_entry_name, action_phase, "
            "status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intent_recorded', 'prepared', ?, ?)",
            (mediaSourceId, actionType, recordId, source, target, sourceKey, targetKey, json.dumps(identity),
             sourceEntryName, targetEntryName, now, now),
        )
        actionId = int(cursor.lastrowid)
        connection.commit()
    return actionId


def updateLocalFileAction(
    settings: AppSettings, actionId: int, status: str, *, message: str | None = None,
    connection: sqlite3.Connection | None = None,
    targetIdentity: list[int] | None = None,
    actionPhase: str | None = None,
) -> None:
    if status not in {*ACTIVE_STATUSES, "completed", "not_applied"}:
        raise ValueError("本地动作状态不合法")
    if actionPhase is not None and actionPhase not in ACTION_PHASES:
        raise ValueError("本地动作阶段不合法")
    if connection is None:
        with closing(sqlite3.connect(settings.database_path)) as ownedConnection:
            updateLocalFileAction(
                settings,
                actionId,
                status,
                message=message,
                connection=ownedConnection,
                targetIdentity=targetIdentity,
                actionPhase=actionPhase,
            )
            ownedConnection.commit()
        return
    cursor = connection.execute(
        "UPDATE local_file_actions SET status = ?, message = ?, "
        "target_identity_json = COALESCE(?, target_identity_json), "
        "action_phase = COALESCE(?, action_phase), updated_at = ? WHERE id = ?",
        (status, message, json.dumps(targetIdentity) if targetIdentity is not None else None,
         actionPhase, datetime.now(timezone.utc).isoformat(), actionId),
    )
    if cursor.rowcount != 1:
        raise ValueError("本地动作意图不存在")
