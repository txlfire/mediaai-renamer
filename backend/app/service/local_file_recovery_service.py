"""停服后核对本地动作，仅修复数据库，不再次移动或删除文件。"""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from app.core.config import AppSettings
from app.service.local_file_action_service import localFileIdentity, updateLocalFileAction
from app.service.rename_operation_service import _update_successful_media_record, _sync_scan_index_after_rename
from app.service.rename_rollback_service import _update_media_record_after_rollback
from app.service.scan_service import _file_modified_at


def _entryIdentity(parent: Path, name: str | None) -> list[int] | None:
    if not name:
        return None
    with os.scandir(parent) as entries:
        matches = [Path(entry.path) for entry in entries if entry.name == name]
    if len(matches) != 1:
        return None
    try:
        return localFileIdentity(matches[0])
    except (FileNotFoundError, OSError, ValueError):
        return None


def _outcome(row: sqlite3.Row) -> str:
    source = Path(row["source_path"])
    target = Path(row["target_path"])
    try:
        expected = json.loads(row["identity_json"])
        expectedTarget = json.loads(row["target_identity_json"]) if row["target_identity_json"] else expected
        sourceEntryName = row["source_entry_name"] if "source_entry_name" in row.keys() else None
        targetEntryName = row["target_entry_name"] if "target_entry_name" in row.keys() else None
        actionPhase = row["action_phase"] if "action_phase" in row.keys() else None
        if os.path.normcase(str(source.absolute())) == os.path.normcase(str(target.absolute())):
            # 大小写重命名必须按实际目录项拼写判断；历史记录缺少证据时保持人工核对。
            if not sourceEntryName or not targetEntryName or sourceEntryName == targetEntryName:
                return "manual"
            if source.parent != target.parent:
                return "manual"
            sourceIdentity = _entryIdentity(source.parent, sourceEntryName)
            targetIdentity = _entryIdentity(target.parent, targetEntryName)
            if sourceIdentity is not None and targetIdentity is not None:
                return "manual"
            if (
                targetIdentity == expectedTarget
                and sourceIdentity is None
                and actionPhase in {"move_issued", "target_confirmed"}
            ):
                return "applied"
            if (
                sourceIdentity == expected
                and targetIdentity is None
                and actionPhase in {"intent_recorded", "move_issued"}
            ):
                return "not_applied"
            return "manual"
        try:
            sourceIdentity = localFileIdentity(source)
        except FileNotFoundError:
            sourceIdentity = None
        try:
            targetIdentity = localFileIdentity(target)
        except FileNotFoundError:
            targetIdentity = None
        if targetIdentity == expectedTarget:
            if sourceIdentity is None or row["source_path"] == row["target_path"]:
                return "applied"
        if sourceIdentity == expected and targetIdentity is None:
            return "not_applied"
    except (OSError, ValueError):
        pass
    return "manual"


def closeLocalFileActionManually(
    settings: AppSettings,
    actionId: int,
    *,
    resolution: str,
    reason: str,
    evidence: str,
    serviceStopped: bool = False,
    actorName: str = "local-maintenance",
) -> dict:
    """停服后按人工证据结案；只修复数据库，不执行文件移动。"""

    if not serviceStopped:
        raise ValueError("人工结案前必须停止目标服务并显式确认")
    if resolution not in {"applied", "not_applied"}:
        raise ValueError("人工结案结果必须是 applied 或 not_applied")
    reason = reason.strip()
    evidence = evidence.strip()
    if not reason or len(reason) > 500:
        raise ValueError("人工结案原因不能为空且不能超过500个字符")
    if not evidence or len(evidence) > 2000:
        raise ValueError("人工核对证据不能为空且不能超过2000个字符")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM local_file_actions WHERE id = ?", (actionId,)).fetchone()
        if row is None or row["status"] not in {"prepared", "applied", "recovery_required"}:
            raise ValueError("本地动作不存在或已经结案")
        if _outcome(row) != "manual":
            raise ValueError("该动作可由自动核对处理，不允许人工覆盖")
        now = datetime.now(timezone.utc).isoformat()
        _repair(connection, row, resolution, now, settings)
        updateLocalFileAction(
            settings,
            actionId,
            "completed" if resolution == "applied" else "not_applied",
            message=f"人工核对结案：{reason}",
            connection=connection,
            actionPhase="business_committed" if resolution == "applied" else "closed_not_applied",
        )
        from app.service.audit_service import record_audit_event
        record_audit_event(
            settings,
            event_type="maintenance",
            actor_name=actorName,
            target_type="local_file_action",
            target_id=str(actionId),
            action="manual_reconcile",
            result="success",
            summary=f"人工核对并结案本地文件动作：{resolution}",
            detail={"resolution": resolution, "reason": reason, "evidence": evidence},
            connection=connection,
        )
        connection.commit()
    return {"id": actionId, "resolution": resolution, "closed": True}


def _summarizeRename(connection: sqlite3.Connection, operationId: int, now: str) -> None:
    counts = dict(connection.execute(
        "SELECT status, COUNT(*) FROM rename_operation_items WHERE operation_id = ? GROUP BY status", (operationId,),
    ).fetchall())
    ready = counts.get("ready", 0)
    failed = counts.get("failed", 0)
    renamed = counts.get("renamed", 0)
    status = "interrupted" if ready else "completed"
    if failed:
        status = "partial_failed" if renamed else "failed"
    connection.execute(
        "UPDATE rename_operations SET status = ?, ready_count = ?, conflict_count = ?, renamed_count = ?, failed_count = ?, updated_at = ? WHERE id = ?",
        (status, ready, counts.get("conflict", 0), renamed, failed, now, operationId),
    )


def _repairPendingMoveTask(connection: sqlite3.Connection, row: sqlite3.Row, outcome: str, now: str):
    # 旧schema的独立迁移意图没有批次；不为历史动作猜测或新建任务关联。
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_move_items'").fetchone() is None:
        return None
    candidates = connection.execute(
        "SELECT i.*,t.status AS task_status,t.execution_token FROM pending_move_items i "
        "JOIN pending_move_tasks t ON t.id=i.task_id WHERE i.pending_file_id=? "
        "AND i.status IN ('moving','recovery_required')", (row["record_id"],),
    ).fetchall()
    if not candidates:
        return None
    matches = [item for item in candidates if str(Path(item["source_path"]).absolute()) == row["source_path"]
               and str(Path(item["target_path"]).absolute()) == row["target_path"] and item["media_source_id"] == row["media_source_id"]]
    if len(matches) != 1:
        raise ValueError("迁移意图与唯一任务条目不一致，拒绝推断")
    item = matches[0]
    if item["task_status"] in {"queued", "running"} or item["execution_token"] is not None:
        raise ValueError("请先确认迁移进程实际退出并核对释放执行槽")
    connection.execute("UPDATE pending_move_items SET status=?,message=? WHERE id=?",
        ("moved" if outcome == "applied" else "not_started", "本机停服按文件身份核对，未重放动作", item["id"]))
    connection.execute("UPDATE pending_move_tasks SET moved_count=(SELECT COUNT(*) FROM pending_move_items "
        "WHERE task_id=? AND status='moved'),failed_count=(SELECT COUNT(*) FROM pending_move_items "
        "WHERE task_id=? AND status='failed'),updated_at=? WHERE id=?", (item["task_id"], item["task_id"], now, item["task_id"]))
    return item["task_id"]


def _repair(connection: sqlite3.Connection, row: sqlite3.Row, outcome: str, now: str, settings: AppSettings) -> None:
    actionType = row["action_type"]
    recordId = int(row["record_id"])
    source = Path(row["source_path"])
    target = Path(row["target_path"])
    if actionType == "rename":
        item = connection.execute(
            "SELECT item.operation_id, item.rename_preview_id, file.file_path, file.media_source_id, item.source_path, item.target_path "
            "FROM rename_operation_items item JOIN rename_previews preview ON preview.id = item.rename_preview_id "
            "JOIN media_files file ON file.id = preview.media_file_id WHERE item.id = ?", (recordId,),
        ).fetchone()
        if item is None or item["media_source_id"] != row["media_source_id"] or item["file_path"] not in {str(source), str(target)}:
            raise ValueError("关联媒体记录缺失或路径已变化")
        if str(Path(item["source_path"]).absolute()) != str(source) or str(Path(item["target_path"]).absolute()) != str(target):
            raise ValueError("动作与业务条目路径不一致")
        if outcome == "applied":
            _update_successful_media_record(connection, item["rename_preview_id"], target, now)
            _sync_scan_index_after_rename(connection, item["rename_preview_id"], source, target, now)
        connection.execute(
            "UPDATE rename_operation_items SET status = ?, message = ?, updated_at = ? WHERE id = ?",
            ("renamed" if outcome == "applied" else "failed", None if outcome == "applied" else "动作未执行，请重新生成干跑", now, recordId),
        )
        _summarizeRename(connection, item["operation_id"], now)
    elif actionType == "rollback":
        item = connection.execute(
            "SELECT rollback.plan_id, rollback.operation_item_id, rollback.current_path, rollback.rollback_path, file.file_path, file.media_source_id "
            "FROM rename_rollback_items rollback JOIN rename_operation_items item ON item.id = rollback.operation_item_id "
            "JOIN rename_previews preview ON preview.id = item.rename_preview_id "
            "JOIN media_files file ON file.id = preview.media_file_id WHERE rollback.id = ?", (recordId,),
        ).fetchone()
        if item is None or str(Path(item["current_path"]).absolute()) != str(source) or str(Path(item["rollback_path"]).absolute()) != str(target):
            raise ValueError("回滚动作关联或路径不一致")
        if item["media_source_id"] != row["media_source_id"] or item["file_path"] not in {str(source), str(target)}:
            raise ValueError("回滚关联媒体记录已变化")
        if outcome == "applied":
            _update_media_record_after_rollback(connection, item["operation_item_id"], source, target, now)
        connection.execute(
            "UPDATE rename_rollback_items SET status = ?, message = ?, executed_at = ?, updated_at = ? WHERE id = ?",
            ("rolled_back" if outcome == "applied" else "failed", None if outcome == "applied" else "动作未执行，请重新干跑", now if outcome == "applied" else None, now, recordId),
        )
        counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM rename_rollback_items WHERE plan_id = ? GROUP BY status", (item["plan_id"],),
        ).fetchall())
        failed = counts.get("failed", 0)
        rolledBack = counts.get("rolled_back", 0)
        status = "interrupted" if counts.get("ready", 0) else "executed"
        if failed:
            status = "partial_failed" if rolledBack else "failed"
        connection.execute(
            "UPDATE rename_rollback_plans SET status = ?, executable_count = ?, conflict_count = ?, updated_at = ? WHERE id = ?",
            (status, rolledBack, failed + counts.get("conflict", 0), now, item["plan_id"]),
        )
    elif actionType == "pending_move":
        item = connection.execute("SELECT file_path, media_source_id FROM pending_files WHERE id = ?", (recordId,)).fetchone()
        if item is None or item[1] != row["media_source_id"] or item[0] not in {str(source), str(target)}:
            raise ValueError("待处理记录缺失或路径已变化")
        taskId = _repairPendingMoveTask(connection, row, outcome, now)
        connection.execute(
            "UPDATE pending_files SET status = ?, file_path = ? WHERE id = ?",
            ("moved" if outcome == "applied" else "pending", str(target) if outcome == "applied" else str(source), recordId),
        )
        if taskId is not None:
            from app.service.operation_log_service import record_operation_log
            counts = connection.execute("SELECT moved_count,total_count FROM pending_move_tasks WHERE id=?", (taskId,)).fetchone()
            record_operation_log(settings, task_type="pending_move", task_id=taskId, level="info", stage="pending_move_reconciled",
                message="本机停服核对迁移事实，未重放文件动作", progress_current=counts[0], progress_total=counts[1],
                detail={"local_action_id": row["id"], "outcome": outcome}, connection=connection)
    else:
        raise ValueError("未知本地动作类型")
    if outcome == "applied" and actionType != "pending_move":
        stat = target.stat()
        connection.execute(
            "UPDATE media_files SET file_size = ?, modified_at = ? WHERE file_path = ? AND media_source_id = ?",
            (stat.st_size, _file_modified_at(stat), str(target), row["media_source_id"]),
        )


def recoverLocalFileActions(settings: AppSettings, *, apply: bool = False, serviceStopped: bool = False, limit: int = 200) -> dict:
    if apply and not serviceStopped:
        raise ValueError("修复前必须停止目标服务并显式确认")
    if not 1 <= limit <= 5000:
        raise ValueError("每次核对数量必须在 1-5000 之间")
    databasePath = settings.database_path.resolve()
    if not databasePath.is_file():
        raise ValueError("目标实例数据库不存在")
    mode = "rw" if apply else "ro"
    report = {"apply": apply, "wouldRepair": 0, "wouldRelease": 0, "repaired": 0, "notApplied": 0, "manual": 0, "failed": 0, "hasMore": False, "items": []}
    with closing(sqlite3.connect(f"{databasePath.as_uri()}?mode={mode}", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM local_file_actions WHERE status IN ('prepared', 'applied', 'recovery_required') ORDER BY id LIMIT ?", (limit + 1,),
        ).fetchall()
        report["hasMore"] = len(rows) > limit
        for row in rows[:limit]:
            outcome = _outcome(row)
            if outcome == "manual":
                report["manual"] += 1
            elif not apply:
                report["wouldRepair" if outcome == "applied" else "wouldRelease"] += 1
            else:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    current = connection.execute("SELECT * FROM local_file_actions WHERE id = ?", (row["id"],)).fetchone()
                    if current["status"] not in {"prepared", "applied", "recovery_required"} or _outcome(current) != outcome:
                        raise ValueError("动作状态或文件在核对期间变化")
                    now = datetime.now(timezone.utc).isoformat()
                    _repair(connection, current, outcome, now, settings)
                    updateLocalFileAction(
                        settings,
                        row["id"],
                        "completed" if outcome == "applied" else "not_applied",
                        connection=connection,
                        actionPhase="business_committed" if outcome == "applied" else "closed_not_applied",
                    )
                    connection.execute(
                        "INSERT INTO audit_events (event_type, actor_name, target_type, target_id, action, result, summary, created_at) "
                        "VALUES ('maintenance', 'local-maintenance', 'local_file_action', ?, 'reconcile', 'success', ?, ?)",
                        (str(row["id"]), "本机停服核对并修复本地动作记录：" + outcome, now),
                    )
                    connection.commit()
                    report["repaired" if outcome == "applied" else "notApplied"] += 1
                except (OSError, sqlite3.Error, ValueError):
                    connection.rollback()
                    report["failed"] += 1
                    outcome = "failed"
            report["items"].append({"id": row["id"], "actionType": row["action_type"], "recordId": row["record_id"], "outcome": outcome})
    return report
