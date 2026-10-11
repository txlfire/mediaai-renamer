"""待处理迁移可靠受理、逐项授权及只读任务结果。"""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat

from app.schema.pending_move import PendingMoveItem, PendingMoveTask
from app.service.file_task_queue_service import (
    FileTaskLeaseError, now, _expiry, requireFileTaskActor, requireFileTaskLease,
    assertFileTaskSlotFree, claimFileTask, heartbeatFileTask, recoverExpiredFileTasks,
    finishInterruptedFileTask,
)
from app.service.operation_log_service import record_operation_log
from app.service.settings_service import get_effective_settings

_SOURCE = "SELECT p.*,s.path AS source_root,s.path_type AS source_type,s.updated_at AS source_updated_at," \
    "s.enabled AS source_enabled,s.deleted_at AS source_deleted_at,j.source_invalidated_at AS scan_invalidated " \
    "FROM pending_files p JOIN media_sources s ON s.id=p.media_source_id JOIN scan_jobs j ON j.id=p.scan_job_id "


def _snapshot(row):
    return {key: row[key] for key in ("media_source_id", "scan_job_id", "file_path", "file_name", "file_size",
        "source_root", "source_type", "source_updated_at", "source_enabled", "source_deleted_at", "scan_invalidated")}


def _requestKey(requestId, actorId):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", requestId):
        raise ValueError("迁移请求标识无效")
    return sha256(f"{actorId or 0}:{requestId}".encode()).hexdigest()


def getPendingMove(settings, taskId, *, connection=None):
    if connection is None:
        with closing(sqlite3.connect(settings.database_path)) as owned:
            owned.row_factory = sqlite3.Row
            owned.execute("BEGIN")
            return getPendingMove(settings, taskId, connection=owned)
    row = connection.execute("SELECT * FROM pending_move_tasks WHERE id=?", (taskId,)).fetchone()
    if row is None:
        raise ValueError("迁移任务不存在")
    items = connection.execute("SELECT * FROM pending_move_items WHERE task_id=? ORDER BY id", (taskId,)).fetchall()
    return PendingMoveTask(int(row["id"]), row["status"], row["target_directory"], row["total_count"],
        row["moved_count"], row["failed_count"], row["error_message"], row["created_at"], row["updated_at"],
        [PendingMoveItem(item["id"], item["pending_file_id"], item["source_path"], item["target_path"],
                         item["status"], item["message"]) for item in items])


def findPendingMove(settings, requestId, actorId):
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        row = connection.execute("SELECT id FROM pending_move_tasks WHERE request_key=?",
                                 (_requestKey(requestId, actorId),)).fetchone()
        return getPendingMove(settings, row[0], connection=connection) if row else None


def acceptPendingMove(settings, ids, targetDirectory, requestId, context, *, synchronous=False):
    """受理事务只冻结数据库事实；不探测磁盘或通过请求线程执行文件动作。"""
    if not ids or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise ValueError("请选择有效待迁移记录")
    ids = sorted(set(ids))
    if len(ids) > int(get_effective_settings(settings)["operations.batch_limit"]):
        raise ValueError("迁移条目超过批量上限")
    target = targetDirectory.strip()
    if not target or not Path(target).is_absolute() or "://" in target or "\x00" in target or len(target) > 4096:
        raise ValueError("迁移目标必须为本机可访问的绝对目录")
    target = os.path.abspath(target)
    actorId = context.actor.id if context.actor else None
    requestKey = _requestKey(requestId, actorId)
    payload = json.dumps({"ids": ids, "target_directory": target}, sort_keys=True)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        requireFileTaskActor(connection, "pending_move", actorId)
        existing = connection.execute("SELECT id,payload_json FROM pending_move_tasks WHERE request_key=?", (requestKey,)).fetchone()
        if existing:
            if existing["payload_json"] != payload:
                raise ValueError("迁移请求标识已绑定其他内容")
            return getPendingMove(settings, existing["id"], connection=connection)
        if synchronous:
            assertFileTaskSlotFree(connection, "pending_move", -1)
        rows = connection.execute(_SOURCE + f"WHERE p.id IN ({','.join('?' for _ in ids)}) ORDER BY p.id", ids).fetchall()
        if len(rows) != len(ids) or any(row["status"] != "pending" or not row["source_enabled"]
                or row["source_deleted_at"] or row["scan_invalidated"] or row["source_type"] == "webdav"
                or "://" in row["file_path"] for row in rows):
            raise ValueError("所选记录不存在或不处于待处理状态，已预约迁移或来源已失效")
        destinations = [os.path.normcase(os.path.join(target, Path(row["file_path"]).name)) for row in rows]
        if len(set(destinations)) != len(destinations):
            raise ValueError("所选文件目标名称重复，请分批迁移")
        if connection.execute("SELECT 1 FROM local_file_actions WHERE status IN ('prepared','applied','recovery_required') "
            f"AND action_type='pending_move' AND record_id IN ({','.join('?' for _ in ids)}) LIMIT 1", ids).fetchone():
            raise ValueError("所选记录存在待核对文件动作")
        timestamp = now()
        taskId = connection.execute("INSERT INTO pending_move_tasks(request_key,payload_json,target_directory,total_count,"
            "actor_id,actor_name,audit_context_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (requestKey, payload, target, len(ids), actorId, context.actor.username if context.actor else "system",
             json.dumps({"ipAddress": context.ipAddress, "userAgent": context.userAgent}), timestamp, timestamp)).lastrowid
        connection.executemany("INSERT INTO pending_move_items(task_id,pending_file_id,media_source_id,source_path,target_path,snapshot_json) "
            "VALUES (?,?,?,?,?,?)", ((taskId, row["id"], row["media_source_id"], row["file_path"],
                os.path.join(target, Path(row["file_path"]).name), json.dumps(_snapshot(row), sort_keys=True)) for row in rows))
        from app.service.file_action_audit_service import createFileActionAuditIntent
        intentId = createFileActionAuditIntent(settings, "pending_move", taskId, context, connection=connection)
        connection.execute("UPDATE pending_move_tasks SET audit_intent_id=? WHERE id=?", (intentId, taskId))
        connection.executemany("UPDATE pending_files SET status='moving' WHERE id=?", ((value,) for value in ids))
        if synchronous:
            from app.service.file_task_queue_service import PROCESS_INSTANCE_ID, PROCESS_INSTANCE_STARTED_AT
            connection.execute("UPDATE pending_move_tasks SET status='running',execution_token=?,execution_expires_at=?,execution_pid=?,"
                "execution_instance_id=?,execution_started_at=? WHERE id=?",
                (secrets.token_urlsafe(24), _expiry(), os.getpid(), PROCESS_INSTANCE_ID, PROCESS_INSTANCE_STARTED_AT, taskId))
        record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info", stage="pending_move_queued",
            message="迁移任务已受理，等待文件写槽" if not synchronous else "兼容同步迁移已取得文件写槽", connection=connection)
        connection.commit()
    return getPendingMove(settings, taskId)


def requirePendingMoveItem(connection, taskId, token, pendingId):
    task = requireFileTaskLease(connection, "pending_move", taskId, token)
    item = connection.execute("SELECT * FROM pending_move_items WHERE task_id=? AND pending_file_id=? AND status IN ('ready','moving')",
                              (taskId, pendingId)).fetchone()
    current = connection.execute(_SOURCE + "WHERE p.id=?", (pendingId,)).fetchone()
    if item is None or current is None or current["status"] != "moving" or _snapshot(current) != json.loads(item["snapshot_json"]):
        raise FileTaskLeaseError("迁移记录或来源已变化，请核对任务")
    return task, item


def directoryIdentity(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_ino <= 0:
        raise ValueError("迁移目标必须为有稳定标识的普通目录")
    return [info.st_dev, info.st_ino]


def requirePendingMoveOwnership(connection, taskId, token, pendingId):
    """记录已确认动作只核对归属；撤权/过期不能抹掉此前已发生的文件事实。"""
    task = connection.execute("SELECT * FROM pending_move_tasks WHERE id=? AND execution_token=?", (taskId, token)).fetchone()
    item = connection.execute("SELECT * FROM pending_move_items WHERE task_id=? AND pending_file_id=? AND status='moving'",
                              (taskId, pendingId)).fetchone()
    pending = connection.execute("SELECT status,file_path FROM pending_files WHERE id=?", (pendingId,)).fetchone()
    if task is None or item is None or pending is None or pending["status"] != "moving" or pending["file_path"] != item["source_path"]:
        raise FileTaskLeaseError("迁移结果归属已变化，请核对文件动作")
    return task


def checkPendingMove(settings, connection, taskId, token, pendingId):
    task, item = requirePendingMoveItem(connection, taskId, token, pendingId)
    if directoryIdentity(task["target_directory"]) != json.loads(task["target_identity_json"]):
        raise FileTaskLeaseError("迁移目标目录已替换，停止后续动作")
    sourceRoot = Path(json.loads(item["snapshot_json"])["source_root"]).resolve(strict=True)
    source = Path(item["source_path"]).resolve(strict=True)
    if not source.is_relative_to(sourceRoot) or source == Path(item["target_path"]):
        raise FileTaskLeaseError("迁移源路径超出来源范围或与目标相同")
    # 文件系统探测后再检查授权，不能把探测前权限视为长时间I/O后的授权。
    requirePendingMoveItem(connection, taskId, token, pendingId)


def executeAcceptedPendingMove(settings, taskId, token):
    from app.service.pending_file_service import _move_pending_files
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        task = requireFileTaskLease(connection, "pending_move", taskId, token)
        targetIdentity = directoryIdentity(task["target_directory"])
        requireFileTaskLease(connection, "pending_move", taskId, token)
        connection.execute("UPDATE pending_move_tasks SET target_identity_json=? WHERE id=? AND execution_token=?",
                           (json.dumps(targetIdentity), taskId, token))
        connection.commit()
        pendingIds = [item.pending_file_id for item in getPendingMove(settings, taskId).items if item.status == "ready"]
        for pendingId in pendingIds:
            try:
                _move_pending_files(settings, [pendingId], task["target_directory"], taskId=taskId, executionToken=token)
            except FileTaskLeaseError:
                raise
            except (OSError, ValueError, sqlite3.Error) as exc:
                connection.rollback()
                connection.execute("BEGIN IMMEDIATE")
                requireFileTaskLease(connection, "pending_move", taskId, token)
                unknown = connection.execute("SELECT 1 FROM local_file_actions WHERE action_type='pending_move' AND record_id=? "
                    "AND status IN ('prepared','applied','recovery_required') LIMIT 1", (pendingId,)).fetchone() is not None
                connection.execute("UPDATE pending_move_items SET status=?,message=? WHERE task_id=? AND pending_file_id=?",
                    ("recovery_required" if unknown else "failed", f"迁移未完成（{type(exc).__name__}），请核对文件动作", taskId, pendingId))
                connection.execute("UPDATE pending_move_tasks SET status=?,failed_count=(SELECT COUNT(*) FROM pending_move_items "
                    "WHERE task_id=? AND status='failed'),error_message='迁移未完成，后续条目未执行；请核对实际文件',updated_at=? WHERE id=?",
                    ("interrupted" if unknown else "partial_failed" if getPendingMove(settings, taskId, connection=connection).moved_count else "failed",
                     taskId, now(), taskId))
                record_operation_log(settings, task_type="pending_move", task_id=taskId, level="error", stage="pending_move_failed",
                    message="迁移中断，未知动作不自动重试" if unknown else "迁移失败，后续条目未执行", connection=connection)
                connection.commit()
                return getPendingMove(settings, taskId)
        connection.execute("BEGIN IMMEDIATE")
        requireFileTaskLease(connection, "pending_move", taskId, token)
        connection.execute("UPDATE pending_move_tasks SET status='completed',updated_at=? WHERE id=?", (now(), taskId))
        record_operation_log(settings, task_type="pending_move", task_id=taskId, level="success", stage="pending_move_completed",
                             message="迁移任务全部完成", progress_current=len(pendingIds), progress_total=len(pendingIds), connection=connection)
        connection.commit()
    return getPendingMove(settings, taskId)


def releasePendingMoveReservations(settings, taskId, token):
    """执行实际结束后仅释放没有未知文件动作的预约，不重放或修改成功路径。"""
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM pending_move_tasks WHERE id=? AND execution_token=? AND status NOT IN ('queued','running')",
                                  (taskId, token)).fetchone():
            return
        connection.execute("UPDATE pending_files SET status='pending' WHERE status='moving' AND id IN "
            "(SELECT pending_file_id FROM pending_move_items WHERE task_id=? AND status<>'moved') AND NOT EXISTS "
            "(SELECT 1 FROM local_file_actions a WHERE a.action_type='pending_move' AND a.record_id=pending_files.id "
            "AND a.status IN ('prepared','applied','recovery_required'))", (taskId,))
        connection.execute("UPDATE pending_move_items SET status='recovery_required' WHERE task_id=? AND status='moving' AND EXISTS "
            "(SELECT 1 FROM local_file_actions a WHERE a.action_type='pending_move' AND a.record_id=pending_move_items.pending_file_id "
            "AND a.status IN ('prepared','applied','recovery_required'))", (taskId,))
        connection.execute("UPDATE pending_move_items SET status='not_started' WHERE task_id=? AND status IN ('ready','moving')", (taskId,))
        connection.commit()


def claimPendingMove(settings):
    return claimFileTask(settings, "pending_move")


def heartbeatPendingMove(settings, taskId, token):
    return heartbeatFileTask(settings, "pending_move", taskId, token)


def recoverExpiredPendingMoves(settings):
    return recoverExpiredFileTasks(settings, "pending_move")


def finishInterruptedPendingMove(settings, taskId, token, reason, *, processExited, localRecovery=False):
    return finishInterruptedFileTask(settings, "pending_move", taskId, token, reason,
        processExited=processExited, localRecovery=localRecovery)
