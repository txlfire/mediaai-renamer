"""有界后台探测/外发任务；受理不访问媒体，未知项不自动重放。"""

from contextlib import closing, nullcontext
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import sqlite3
from uuid import UUID

from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.auth_service import _row_to_user
from app.service.execution_guard_service import executionGuard
from app.service.operation_log_service import record_operation_log
from app.service.settings_service import get_effective_settings
from app.service.scan_recovery_service import isProcessAlive

KINDS = {"rename_dry_run": "rename:execute", "rollback_dry_run": "rollback:execute",
         "metadata_batch": "metadata:submit", "ai_batch": "metadata:submit"}
TERMINAL = {"completed", "partial_completed", "failed", "interrupted"}
INSTANCE_ID = secrets.token_hex(16)
MAX_ITEMS = 1000


class BackgroundJobConflict(ValueError):
    """请求冲突或容量不足，不能通过自动新建请求规避。"""


class BackgroundJobInterrupted(ValueError):
    """执行授权、输入或配置失效；不得继续外发/发布。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _expiry():
    return (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _connect(settings):
    connection = sqlite3.connect(settings.database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _actor(connection, actorId, actorName, kind):
    row = connection.execute("SELECT * FROM users WHERE id=?", (actorId,)).fetchone()
    if (row is None or not row["enabled"] or row["username"] != actorName
            or KINDS[kind] not in json.loads(row["permissions_json"])):
        raise BackgroundJobInterrupted("发起用户已停用或权限被撤销")
    return _row_to_user(row)


def _configHash(settings, connection):
    effective = get_effective_settings(settings)
    bound = {key: value for key, value in effective.items()
             if key.startswith(("ai.", "tmdb.", "metadata.", "privacy.", "naming.")) or key == "operations.batch_limit"}
    providers = [dict(row) for row in connection.execute("SELECT * FROM metadata_provider_configs ORDER BY id")]
    # 凭据只参与摘要，不进入持久化快照、结果或日志。
    return _hash([bound, providers])


def _previewState(connection, previewId):
    from app.service.preview_service import _get_preview_row
    row = _get_preview_row(connection, previewId)
    source = connection.execute("SELECT ms.*,sj.source_invalidated_at,mf.file_size,mf.modified_at "
        "FROM media_files mf JOIN media_sources ms ON ms.id=mf.media_source_id "
        "JOIN scan_jobs sj ON sj.id=mf.scan_job_id WHERE mf.id=?", (row["media_file_id"],)).fetchone()
    if (source is None or source["deleted_at"] or not source["enabled"] or source["source_invalidated_at"]
            or row["status"] not in {"generated", "needs_review", "edited", "no_rename", "unable_rename"}):
        raise BackgroundJobInterrupted("预览或媒体源已失效，请重新核对")
    return row, source


def _previewHash(connection, previewId):
    row, source = _previewState(connection, previewId)
    return _hash([dict(row), dict(source)])


def _snapshot(connection, kind, payload):
    if kind == "rollback_dry_run":
        from app.service.rename_rollback_service import _rollbackDryRunDatabaseState
        state = _rollbackDryRunDatabaseState(connection, payload["plan_id"])
        if state[0]["status"] not in {"draft", "checked"} or state[0]["execution_token"] is not None:
            raise BackgroundJobConflict("回滚计划不是可干跑状态")
        return {"plan": _hash(state), "header": _hash([state[0], state[3]]),
                "items": {str(item["id"]): _hash([item, context]) for item, context in zip(state[1], state[2])}}
    values = {}
    for itemId in payload["rename_preview_ids"]:
        row, source = _previewState(connection, itemId)
        values[str(itemId)] = _hash([dict(row), dict(source)])
        values[f"source:{itemId}"] = _hash(dict(source))
    return values


def _normalise(kind, payload):
    if kind not in KINDS:
        raise ValueError("不支持的后台任务类型")
    if kind == "rollback_dry_run":
        planId = payload.get("plan_id")
        if isinstance(planId, bool) or not isinstance(planId, int) or planId <= 0:
            raise ValueError("回滚计划ID无效")
        return {"plan_id": planId}
    ids = payload.get("rename_preview_ids") or []
    if not isinstance(ids, list) or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in ids):
        raise ValueError("预览ID列表无效")
    ids = sorted(set(ids))
    if not ids or len(ids) > MAX_ITEMS:
        raise BackgroundJobConflict(f"后台任务必须选择1～{MAX_ITEMS}项")
    value = {"rename_preview_ids": ids}
    if kind in {"metadata_batch", "ai_batch"}:
        source = payload.get("metadata_match_source", "parsed_title")
        if source not in {"parsed_title", "original_file_name", "parent_folder_title"}:
            raise ValueError("不支持的匹配来源")
        value["metadata_match_source"] = source
    if kind == "metadata_batch":
        mode = payload.get("mode", "fallback")
        if mode not in {"primary_only", "fallback", "parallel"}:
            raise ValueError("不支持的多源匹配模式")
        value["mode"] = mode
    return value


def _public(row):
    return {key: row[key] for key in ("id", "request_id", "kind", "status", "total_count", "completed_count", "unknown_count", "success_count", "failed_count",
        "error_message", "created_at", "updated_at", "result_cleared_at")} | {
        "result_available": row["status"] in TERMINAL and row["result_cleared_at"] is None
        and (row["completed_count"] > 0 or (bool(row["result_exists"]) if "result_exists" in row.keys() else row["result_json"] is not None))}


def _allowed(row, user):
    if user is None or (row["actor_id"] != user.id and "task:manage" not in user.permissions):
        raise LookupError("后台任务不存在或无权查看")
    if KINDS[row["kind"]] not in user.permissions:
        raise PermissionError("当前用户不再具有该任务权限")


def getBackgroundJob(settings, requestId, user, *, includeResult=False):
    with closing(_connect(settings)) as connection:
        columns = "*" if includeResult else ("id,request_id,kind,actor_id,status,total_count,completed_count,unknown_count,success_count,failed_count,error_message,"
            "created_at,updated_at,result_cleared_at,(result_json IS NOT NULL) AS result_exists")
        row = connection.execute(f"SELECT {columns} FROM background_jobs WHERE request_id=?", (requestId,)).fetchone()
        if row is None:
            raise LookupError("后台任务不存在，请核对请求ID，不要自动重复提交")
        _allowed(row, user)
        result = _public(row)
        if includeResult:
            if row["result_cleared_at"]:
                raise LookupError("任务结果已按保留或大小限制清理；原任务不会重新执行")
            result["result"] = json.loads(row["result_json"]) if row["result_json"] else None
            result["items"] = [dict(item) | {"outcome": json.loads(item["outcome_json"]) if item["outcome_json"] else None}
                for item in connection.execute("SELECT item_id,status,outcome_json FROM background_job_items WHERE job_id=? ORDER BY item_id", (row["id"],))]
            for item in result["items"]:
                item.pop("outcome_json")
        return result


def acceptBackgroundJob(settings, kind, requestId, payload, context):
    requestId = str(UUID(str(requestId)))
    payload = _normalise(kind, payload)
    requestHash = _hash([kind, payload])
    actor = context.actor
    if actor is None:
        raise PermissionError("后台受理需要已认证用户")
    with closing(_connect(settings)) as database:
        _actor(database, actor.id, actor.username, kind)
        previous = database.execute("SELECT * FROM background_jobs WHERE request_id=?", (requestId,)).fetchone()
        if previous is not None:
            if previous["actor_id"] != actor.id or previous["request_hash"] != requestHash:
                raise BackgroundJobConflict("请求ID已用于另一任务，拒绝覆盖或重发")
            return _public(previous)
    reservation = settings.background_jobs.result_max_size_mb * 1024 * 1024
    cleanupBackgroundResults(settings, reserveBytes=reservation)
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        actor = context.actor
        if actor is None:
            raise PermissionError("后台受理需要已认证用户")
        _actor(connection, actor.id, actor.username, kind)
        previous = connection.execute("SELECT * FROM background_jobs WHERE request_id=?", (requestId,)).fetchone()
        if previous is not None:
            if previous["actor_id"] != actor.id or previous["request_hash"] != requestHash:
                raise BackgroundJobConflict("请求ID已用于另一任务，拒绝覆盖或重发")
            return _public(previous)
        ids = payload.get("rename_preview_ids", [])
        limit = min(MAX_ITEMS, int(get_effective_settings(settings)["operations.batch_limit"]))
        if len(ids) > limit:
            raise BackgroundJobConflict(f"批量操作数量不能超过 {limit} 条")
        if connection.execute("SELECT COUNT(*) FROM background_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL").fetchone()[0] >= settings.background_jobs.max_queued:
            raise BackgroundJobConflict("后台队列已满，请先查询现有任务")
        used = connection.execute("SELECT COALESCE(SUM(result_bytes),0) FROM background_jobs WHERE result_cleared_at IS NULL AND result_bytes>0").fetchone()[0]
        active = connection.execute("SELECT COUNT(*) FROM background_jobs WHERE status IN ('queued','running') OR execution_token IS NOT NULL").fetchone()[0]
        if used + (active + 1) * reservation > settings.background_jobs.result_total_size_mb * 1024 * 1024:
            raise BackgroundJobConflict("后台结果容量或预留空间不足，请等待现有任务结束")
        snapshot = _snapshot(connection, kind, payload)
        if kind == "rollback_dry_run":
            count = connection.execute("SELECT COUNT(*) FROM rename_rollback_items WHERE plan_id=?", (payload["plan_id"],)).fetchone()[0]
            if count > limit:
                raise BackgroundJobConflict(f"回滚干跑数量不能超过 {limit} 条")
        if ids:
            placeholders = ",".join("?" for _ in ids)
            if connection.execute(f"SELECT 1 FROM background_job_items i JOIN background_jobs j ON j.id=i.job_id "
                f"WHERE i.item_id IN ({placeholders}) AND j.kind<>'rollback_dry_run' "
                "AND (j.status IN ('queued','running') OR j.execution_token IS NOT NULL) LIMIT 1", ids).fetchone():
                raise BackgroundJobConflict("所选预览已有后台任务，请查询后再操作")
        elif connection.execute("SELECT 1 FROM background_jobs WHERE kind='rollback_dry_run' "
                "AND json_extract(input_json,'$.plan_id')=? AND (status IN ('queued','running') OR execution_token IS NOT NULL) LIMIT 1",
                (payload["plan_id"],)).fetchone():
            raise BackgroundJobConflict("该回滚计划已有后台干跑")
        timestamp = now()
        count = len(ids) if ids else 1
        cursor = connection.execute("INSERT INTO background_jobs(request_id,request_hash,kind,actor_id,actor_name,input_json,"
            "config_hash,snapshot_json,audit_context_json,total_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (requestId, requestHash, kind, actor.id, actor.username, _json(payload), _configHash(settings, connection), _json(snapshot),
             _json({"ipAddress": context.ipAddress, "userAgent": context.userAgent}), count, timestamp, timestamp))
        jobId = cursor.lastrowid
        baseBytes = len((_json(payload) + _json(snapshot) + _json({"ipAddress": context.ipAddress, "userAgent": context.userAgent})).encode())
        connection.execute("UPDATE background_jobs SET result_bytes=? WHERE id=?", (baseBytes, jobId))
        connection.executemany("INSERT INTO background_job_items(job_id,item_id) VALUES(?,?)", ((jobId, item) for item in (ids or [payload["plan_id"]])))
        record_audit_event(settings, event_type="background.accepted", action="accept_background_job", result="success",
            summary="后台任务已受理，尚未执行", target_type="background_job", target_id=jobId, actor=actor,
            ip_address=context.ipAddress, user_agent=context.userAgent, detail={"kind": kind, "total_count": count}, connection=connection)
        record_operation_log(settings, task_type="background_job", task_id=jobId, level="info", stage="queued",
            message="后台任务已受理，等待执行", progress_current=0, progress_total=count, connection=connection)
        row = connection.execute("SELECT * FROM background_jobs WHERE id=?", (jobId,)).fetchone()
        connection.commit()
        return _public(row)


def requireBackgroundJobLease(settings, connection, jobId, token, *, checkConfig=True):
    row = connection.execute("SELECT * FROM background_jobs WHERE id=? AND execution_token=? "
        "AND status='running' AND julianday(execution_expires_at)>julianday('now')", (jobId, token)).fetchone()
    if row is None:
        raise BackgroundJobInterrupted("后台执行授权已失效，不继续外发或发布")
    actor = _actor(connection, row["actor_id"], row["actor_name"], row["kind"])
    if checkConfig and _configHash(settings, connection) != row["config_hash"]:
        raise BackgroundJobInterrupted("任务等待或执行期间配置已变化，请重新核对")
    return row, actor


def claimBackgroundJob(settings):
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM background_jobs WHERE execution_token IS NOT NULL LIMIT 1").fetchone():
            return None
        row = connection.execute("SELECT id FROM background_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        token = secrets.token_urlsafe(24)
        connection.execute("UPDATE background_jobs SET status='running',execution_token=?,execution_expires_at=?,"
            "execution_instance_id=?,execution_owner_pid=?,updated_at=? WHERE id=? AND status='queued'", (token, _expiry(), INSTANCE_ID, os.getpid(), now(), row["id"]))
        connection.commit()
        return row["id"], token


def heartbeatBackgroundJob(settings, jobId, token):
    with closing(_connect(settings)) as connection:
        try:
            requireBackgroundJobLease(settings, connection, jobId, token, checkConfig=False)
        except BackgroundJobInterrupted:
            return False
        changed = connection.execute("UPDATE background_jobs SET execution_expires_at=? WHERE id=? AND execution_token=? "
            "AND status='running' AND julianday(execution_expires_at)>julianday('now')", (_expiry(), jobId, token)).rowcount
        connection.commit()
        return changed == 1


def finishInterruptedBackgroundJob(settings, jobId, token, reason, *, processExited):
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM background_jobs WHERE id=? AND execution_token=?", (jobId, token)).fetchone()
        if row is None:
            return
        if row["status"] == "running":
            reason = f"{reason[:300]}；已发出但未记录结果的请求需核查，不会自动重试"
            connection.execute("UPDATE background_jobs SET status='interrupted',error_message=?,updated_at=? WHERE id=?", (reason, now(), jobId))
            connection.execute("UPDATE background_job_items SET status=CASE WHEN status='dispatched' THEN 'unknown' ELSE 'not_sent' END "
                "WHERE job_id=? AND status IN ('queued','running','dispatched')", (jobId,))
            connection.execute("UPDATE background_jobs SET unknown_count=(SELECT COUNT(*) FROM background_job_items "
                "WHERE job_id=? AND status='unknown') WHERE id=?", (jobId, jobId))
            record_operation_log(settings, task_type="background_job", task_id=jobId, level="error", stage="interrupted",
                message=reason, connection=connection)
            record_audit_event(settings, event_type="background.interrupted", action="interrupt_background_job", result="failed",
                summary=reason, target_type="background_job", target_id=jobId, actor_name=row["actor_name"],
                detail={"actor_id": row["actor_id"], "kind": row["kind"], "completed_count": row["completed_count"]}, connection=connection)
        if processExited:
            connection.execute("UPDATE background_jobs SET execution_token=NULL,execution_expires_at=NULL,execution_pid=NULL,"
                "execution_instance_id=NULL,execution_owner_pid=NULL WHERE id=? AND execution_token=?", (jobId, token))
        connection.commit()


def recoverExpiredBackgroundJobs(settings):
    with closing(_connect(settings)) as connection:
        rows = connection.execute("SELECT id,execution_token,execution_pid,execution_owner_pid,status,execution_expires_at FROM background_jobs "
            "WHERE execution_token IS NOT NULL LIMIT 100").fetchall()
    for row in rows:
        exited = False
        if row["execution_pid"] is not None:
            try:
                exited = not isProcessAlive(row["execution_pid"])
            except (ValueError, OSError):
                pass
        expired = not row["execution_expires_at"] or row["execution_expires_at"] <= now()
        if expired and row["execution_pid"] is None and row["execution_owner_pid"] is not None:
            # PID可靠保存后才会向子进程stdin授权；交接前监督者已退出则不存在获授权的子进程。
            try:
                exited = not isProcessAlive(row["execution_owner_pid"])
            except (ValueError, OSError):
                pass
        if exited or expired:
            finishInterruptedBackgroundJob(settings, row["id"], row["execution_token"], "后台进程退出或租约过期", processExited=exited)
    return len(rows)


def cleanupBackgroundResults(settings, *, reserveBytes=0):
    """有界清理普通终态结果，保留幂等摘要；不删安全审计或仍占槽的任务。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.background_jobs.retention_days)).isoformat()
    ceiling = settings.background_jobs.result_total_size_mb * 1024 * 1024
    cleared = 0
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidates = connection.execute("SELECT id,updated_at,result_bytes FROM background_jobs WHERE status IN "
            "('completed','partial_completed','failed','interrupted') AND execution_token IS NULL "
            "AND result_cleared_at IS NULL ORDER BY updated_at,id LIMIT 200").fetchall()
        used = connection.execute("SELECT COALESCE(SUM(result_bytes),0) FROM background_jobs WHERE result_cleared_at IS NULL AND result_bytes>0").fetchone()[0]
        reserved = connection.execute("SELECT COUNT(*) FROM background_jobs WHERE status IN ('queued','running') "
            "OR execution_token IS NOT NULL").fetchone()[0] * settings.background_jobs.result_max_size_mb * 1024 * 1024
        for row in candidates:
            if row["updated_at"] >= cutoff and used + reserved + reserveBytes <= ceiling:
                break
            connection.execute("UPDATE background_job_items SET outcome_json=NULL WHERE job_id=?", (row["id"],))
            connection.execute("UPDATE background_jobs SET input_json=NULL,snapshot_json=NULL,audit_context_json=NULL,"
                "result_json=NULL,result_bytes=0,result_cleared_at=? WHERE id=?", (now(), row["id"]))
            used -= row["result_bytes"]
            cleared += 1
        connection.commit()
    return cleared


def _cleanResult(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: _cleanResult(item) for key, item in value.items() if key not in {"raw_data", "raw_response", "prompt"}}
    if isinstance(value, (list, tuple)):
        return [_cleanResult(item) for item in value]
    return value


def _markItems(settings, jobId, token, ids, status):
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        requireBackgroundJobLease(settings, connection, jobId, token)
        connection.executemany("UPDATE background_job_items SET status=? WHERE job_id=? AND item_id=?",
                               ((status, jobId, itemId) for itemId in ids))
        connection.commit()


def _executionGuard(settings, jobId, token, expected, payload, kind, itemId=None):
    def guard(connection=None, phase="check"):
        owns = connection is None
        with closing(_connect(settings)) if owns else nullcontext(connection) as database:
            if phase == "send":
                database.execute("BEGIN IMMEDIATE")
            requireBackgroundJobLease(settings, database, jobId, token)
            if kind == "rollback_dry_run":
                if phase.startswith("rollback-probe:"):
                    from app.service.rename_rollback_service import _rollback_item_context
                    targetId = int(phase.split(":", 1)[1])
                    plan = database.execute("SELECT * FROM rename_rollback_plans WHERE id=?", (payload["plan_id"],)).fetchone()
                    item = database.execute("SELECT * FROM rename_rollback_items WHERE id=? AND plan_id=?", (targetId, payload["plan_id"])).fetchone()
                    original = database.execute("SELECT status,execution_token,updated_at FROM rename_operations WHERE id=?", (plan["operation_id"],)).fetchone() if plan is not None else None
                    context = _rollback_item_context(database, item["operation_item_id"]) if item is not None else None
                    valid = (plan is not None and item is not None
                             and expected["header"] == _hash([dict(plan), tuple(original) if original is not None else None])
                             and expected["items"].get(str(targetId)) == _hash([dict(item), dict(context) if context is not None else None]))
                else:
                    valid = expected == _snapshot(database, kind, payload)
                if not valid:
                    raise BackgroundJobInterrupted("回滚计划或来源已变化，拒绝继续干跑")
            elif itemId is not None:
                if expected[str(itemId)] != _previewHash(database, itemId):
                    raise BackgroundJobInterrupted("预览或媒体源已变化，拒绝外发或覆盖")
            elif phase.startswith("probe:"):
                targetId = int(phase.split(":", 1)[1])
                if expected[str(targetId)] != _previewHash(database, targetId):
                    raise BackgroundJobInterrupted("干跑输入已变化")
            elif expected != _snapshot(database, kind, payload):
                raise BackgroundJobInterrupted("干跑输入已变化，拒绝发布")
            if phase == "send":
                database.execute("UPDATE background_job_items SET status='dispatched' WHERE job_id=? AND item_id=?",
                                 (jobId, itemId))
                database.commit()
    return guard


def _publishOutcome(settings, jobId, token, itemId, outcome, itemLimit, expected=None):
    value = _cleanResult(outcome)
    encoded = _json(value)
    if len(encoded.encode()) > itemLimit:
        value = {"error": "结构化结果超过本任务大小限制，未保存候选；原请求不会自动重发"}
        encoded = _json(value)
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        job, _ = requireBackgroundJobLease(settings, connection, jobId, token)
        if expected is not None and job["kind"] == "ai_batch" and expected[str(itemId)] != _previewHash(connection, itemId):
            raise BackgroundJobInterrupted("AI返回期间输入已变化，拒绝发布过期候选")
        if expected is not None and job["kind"] == "metadata_batch":
            currentRow, currentSource = _previewState(connection, itemId)
            if expected[f"source:{itemId}"] != _hash(dict(currentSource)):
                raise BackgroundJobInterrupted("匹配期间媒体源已变化，拒绝发布过期结果")
            if is_dataclass(outcome.get("preview")):
                from app.service.preview_service import _row_to_preview
                if asdict(outcome["preview"]) != asdict(_row_to_preview(currentRow, get_effective_settings(settings))):
                    raise BackgroundJobInterrupted("匹配结果已被修改，拒绝发布过期结果")
        previous = connection.execute("SELECT status FROM background_job_items WHERE job_id=? AND item_id=?", (jobId, itemId)).fetchone()
        uncertain = bool(previous["status"] == "dispatched" and (value.get("error_type") in {"timeout", "network", "unknown"}
            or any(provider.get("status") == "failed" for provider in value.get("provider_results", []))))
        state = value.get("status") or value.get("preview", {}).get("metadata_match_status")
        failed = "error" in value or state == "failed"
        connection.execute("UPDATE background_job_items SET status=?,outcome_json=? WHERE job_id=? AND item_id=?",
            ("unknown" if uncertain else "failed" if failed else "completed", encoded, jobId, itemId))
        successful = not failed and state not in {"blocked", "skipped"}
        connection.execute("UPDATE background_jobs SET completed_count=completed_count+1,unknown_count=unknown_count+?,"
            "success_count=success_count+?,failed_count=failed_count+?,result_bytes=result_bytes+?,updated_at=? WHERE id=?",
            (int(uncertain), int(successful), int(failed), len(encoded.encode()), now(), jobId))
        row = connection.execute("SELECT * FROM background_jobs WHERE id=?", (jobId,)).fetchone()
        record_operation_log(settings, task_type="background_job", task_id=jobId, level="info", stage="item_completed",
            message=f"后台任务已处理 {row['completed_count']}/{row['total_count']} 项", progress_current=row["completed_count"],
            progress_total=row["total_count"], detail={"item_id": itemId}, connection=connection)
        connection.commit()


def _batchResult(kind, outcomes):
    if kind == "ai_batch":
        result = {"total_count": len(outcomes), "success_count": 0, "failed_count": 0, "blocked_count": 0,
                  "skipped_count": 0, "items": [], "failed_items": [], "usage": {}}
        for row in outcomes:
            value = json.loads(row["outcome_json"])
            if "error" in value:
                result["failed_count"] += 1
                result["failed_items"].append({"id": row["item_id"], "message": value["error"]})
                continue
            status = value["status"]
            key = {"success": "success_count", "blocked": "blocked_count", "skipped": "skipped_count"}.get(status, "failed_count")
            result[key] += 1
            result["items"].append({"id": row["item_id"], "result": value})
            for key, number in value.get("usage", {}).items():
                if not isinstance(number, bool) and isinstance(number, (int, float)):
                    result["usage"][key] = result["usage"].get(key, 0) + number
        return result
    result = {"total_count": len(outcomes), "success_count": 0, "failed_count": 0, "blocked_count": 0, "skipped_count": 0,
              "provider_success_count": 0, "provider_failed_count": 0, "provider_skipped_count": 0, "items": [], "failed_items": []}
    for row in outcomes:
        value = json.loads(row["outcome_json"])
        if "error" in value:
            result["failed_count"] += 1
            result["failed_items"].append({"id": row["item_id"], "message": value["error"]})
            continue
        state = value["preview"]["metadata_match_status"]
        result[{"blocked": "blocked_count", "failed": "failed_count"}.get(state, "success_count")] += 1
        result["items"].append(value)
        for provider in value.get("provider_results", []):
            key = {"success": "provider_success_count", "failed": "provider_failed_count"}.get(provider.get("status"), "provider_skipped_count")
            result[key] += 1
    return result


def executeBackgroundJob(settings, jobId, token):
    from app.service.rename_operation_service import create_rename_dry_run
    from app.service.rename_rollback_service import dry_run_rename_rollback_plan
    from app.service.preview_service import parse_rename_preview_with_ai, match_rename_preview_multi_source_metadata
    with closing(_connect(settings)) as connection:
        row, actor = requireBackgroundJobLease(settings, connection, jobId, token)
        payload, expected = json.loads(row["input_json"]), json.loads(row["snapshot_json"])
        if expected != _snapshot(connection, row["kind"], payload):
            raise BackgroundJobInterrupted("任务排队期间输入已变化")
        context = AuditWriteContext(actor, **json.loads(row["audit_context_json"]))
    kind = row["kind"]
    ids = payload.get("rename_preview_ids", [payload.get("plan_id")])
    limit = (settings.background_jobs.result_max_size_mb * 1024 * 1024 - row["result_bytes"] - 16384 - 128 * len(ids)) // (2 * len(ids))
    result = None
    if kind in {"rename_dry_run", "rollback_dry_run"}:
        _markItems(settings, jobId, token, ids, "running")
        with executionGuard(_executionGuard(settings, jobId, token, expected, payload, kind)):
            if kind == "rename_dry_run":
                operation = create_rename_dry_run(settings, ids, auditContext=context)
                result = {"operation_id": operation.id}
            else:
                plan = dry_run_rename_rollback_plan(settings, payload["plan_id"], auditContext=context)
                result = {"plan_id": plan.id, "operation_id": plan.operation_id}
        for itemId in ids:
            _publishOutcome(settings, jobId, token, itemId, result, limit)
    else:
        for itemId in ids:
            _markItems(settings, jobId, token, [itemId], "running")
            with executionGuard(_executionGuard(settings, jobId, token, expected, payload, kind, itemId)):
                if kind == "ai_batch":
                    outcome = parse_rename_preview_with_ai(settings, itemId, metadata_match_source=payload["metadata_match_source"])
                else:
                    outcome = match_rename_preview_multi_source_metadata(settings, itemId,
                        metadata_match_source=payload["metadata_match_source"], mode=payload["mode"])
            _publishOutcome(settings, jobId, token, itemId, outcome, limit, expected)
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        requireBackgroundJobLease(settings, connection, jobId, token)
        outcomes = connection.execute("SELECT item_id,outcome_json FROM background_job_items WHERE job_id=? ORDER BY item_id", (jobId,)).fetchall()
        if result is None:
            result = _batchResult(kind, outcomes)
        resultJson = _json(result)
        current = connection.execute("SELECT unknown_count,result_bytes FROM background_jobs WHERE id=?", (jobId,)).fetchone()
        truncated = current["result_bytes"] + len(resultJson.encode()) > settings.background_jobs.result_max_size_mb * 1024 * 1024
        if truncated:
            # 用量等聚合字段可能再次复制大块数据，终态摘要仍须遵守实际字节上限。
            result = {key: value for key, value in result.items() if key.endswith("_count")}
            result.update(items=[], failed_items=[], usage={}, result_truncated=True)
            resultJson = _json(result)
        partial = bool(truncated or result.get("failed_count") or result.get("blocked_count") or current["unknown_count"])
        status = "partial_completed" if partial else "completed"
        connection.execute("UPDATE background_jobs SET status=?,result_json=?,result_bytes=result_bytes+?,error_message=?,updated_at=? WHERE id=?",
                           (status, resultJson, len(resultJson.encode()), "聚合摘要超过大小限制，逐项已知结果仍可查看；不会重新执行" if truncated else None, now(), jobId))
        record_audit_event(settings, event_type="background.completed", action="execute_background_job",
            result="partial_success" if partial else "success", summary="后台任务已完成", target_type="background_job", target_id=jobId,
            actor=actor, detail={"kind": kind, "total_count": row["total_count"]}, connection=connection)
        record_operation_log(settings, task_type="background_job", task_id=jobId, level="success", stage="completed",
            message="后台任务已完成，结果可查询", progress_current=row["total_count"], progress_total=row["total_count"], connection=connection)
        connection.commit()


def listBackgroundJobs(settings, user, *, limit=20):
    if not 1 <= limit <= 50:
        raise ValueError("任务列表数量超限")
    kinds = [kind for kind, permission in KINDS.items() if permission in user.permissions]
    if not kinds:
        return {"items": []}
    with closing(_connect(settings)) as connection:
        placeholders = ",".join("?" for _ in kinds)
        rows = connection.execute(f"SELECT id,request_id,kind,status,total_count,completed_count,unknown_count,success_count,failed_count,error_message,"
            f"created_at,updated_at,result_cleared_at,(result_json IS NOT NULL) AS result_exists FROM background_jobs "
            f"WHERE actor_id=? AND kind IN ({placeholders}) ORDER BY id DESC LIMIT ?", (user.id, *kinds, limit)).fetchall()
        return {"items": [_public(row) for row in rows]}
