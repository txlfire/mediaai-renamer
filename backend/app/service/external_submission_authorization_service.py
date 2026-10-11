"""绑定用户、预览和配置的一次性外发授权；维护只补审计，不重放网络请求。"""

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.service.audit_intent_retention_service import ensureAuditIntentCapacity
from app.schema.media import ParsedMediaName
from app.service.ai_parse_service import parse_media_with_ai
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.external_submission_guard import (
    ExternalSubmissionPermit, _get_block_record, check_external_submission, maskSensitiveWords,
    matchSensitiveWordsFromEffective, submissionPayloadFingerprint,
)
from app.service.preview_service import _get_preview_row, _parsed_from_preview
from app.service.settings_service import get_effective_settings
from app.service.tmdb_client import TMDB_API_BASE_URL, TmdbClient

AUTHORIZATION_TTL_SECONDS = 300
logger = get_logger(__name__)


def _diagnostic(phase, exc):
    try:
        logger.warning("一次性外部提交诊断 phase=%s error_type=%s", phase, type(exc).__name__)
    except Exception:  # 诊断故障不影响已消费授权的结果。
        pass


def _candidatePayload(candidate):
    # 仅保存结构化候选，不留存服务商原始响应、提示词或任意 raw_data。
    fields = ("title", "media_type", "year", "season", "episode", "provider", "provider_id", "confidence")
    return {key: getattr(candidate, key) for key in fields if hasattr(candidate, key)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _checkActor(connection, context: AuditWriteContext) -> None:
    actor = context.actor
    if actor is None:
        raise PermissionError("一次性提交需要已认证用户")
    row = connection.execute("SELECT username,enabled,permissions_json FROM users WHERE id=?", (actor.id,)).fetchone()
    if (row is None or not row["enabled"] or row["username"] != actor.username
            or "metadata:submit" not in json.loads(row["permissions_json"])):
        raise PermissionError("用户已停用或不再具有外部提交权限")


def _snapshot(settings, connection, blockId: int):
    exists = connection.execute("SELECT id FROM external_submission_blocks WHERE id=?", (blockId,)).fetchone()
    if exists is None:
        raise LookupError("外部提交拦截记录不存在")
    block = _get_block_record(connection, blockId)
    if block.status not in {"blocked", "override_failed"}:
        raise ValueError("该记录不可再次提交；执行中或未知结果请先人工核查")
    if block.source_module != "rename_preview" or block.target_service not in {"ai", "tmdb"}:
        raise ValueError("该来源或目标服务不支持一次性提交")
    unresolved = connection.execute(
        "SELECT 1 FROM external_submission_authorizations a JOIN external_submission_blocks b ON b.id=a.block_id "
        "WHERE b.source_module=? AND b.source_record_id=? AND b.target_service=? AND "
        "(a.status='consumed' OR b.status='override_unknown') LIMIT 1",
        (block.source_module, block.source_record_id, block.target_service),
    ).fetchone()
    if unresolved:
        raise ValueError("该文件存在执行中或未知结果的授权，请先人工核查")
    row = _get_preview_row(connection, block.source_record_id)
    source = connection.execute(
        "SELECT ms.*,sj.source_invalidated_at,mf.scan_job_id,mf.file_size,mf.modified_at "
        "FROM media_files mf JOIN media_sources ms ON ms.id=mf.media_source_id "
        "JOIN scan_jobs sj ON sj.id=mf.scan_job_id WHERE mf.id=?", (row["media_file_id"],),
    ).fetchone()
    if (source is None or not source["enabled"] or source["deleted_at"]
            or source["source_invalidated_at"] or row["status"] not in {"generated", "needs_review"}):
        raise ValueError("媒体源或预览已失效，请重新扫描并生成预览")
    if block.file_name != row["file_name"] or block.file_path != row["file_path"]:
        raise ValueError("文件已变化，请重新生成拦截记录")
    modes = ["parsed_title"] if block.target_service == "ai" else [
        "parsed_title", "original_file_name", "parent_folder_title",
    ]
    matched = [(mode, _parsed_from_preview(row, mode)) for mode in modes]
    matched = [(mode, parsed) for mode, parsed in matched if parsed.title == block.match_title]
    if not matched:
        raise ValueError("解析标题已变化，请重新生成拦截记录")
    if len({_json(asdict(parsed)) for _, parsed in matched}) > 1:
        raise ValueError("旧记录的匹配来源不明确，请重新生成拦截记录")
    mode, parsed = matched[0]
    effective = get_effective_settings(settings)
    target = block.target_service
    if not effective.get(f"{target}.enabled"):
        raise ValueError("目标服务未启用")
    if target == "ai":
        if not effective.get("ai.api_key"):
            raise ValueError("目标 AI 服务未配置密钥")
        endpoint = urlsplit(str(effective.get("ai.base_url") or ""))
        service = {"provider": effective.get("ai.provider"), "model": effective.get("ai.model"),
                   "endpoint": urlunsplit((endpoint.scheme, endpoint.netloc.rsplit("@", 1)[-1], endpoint.path, "", "")),
                   "auth_channel": "Bearer", "timeout_ms": effective.get("ai.timeout_ms")}
    else:
        if not effective.get("tmdb.v4_token") and not effective.get("tmdb.api_key"):
            raise ValueError("TMDB 未配置读取令牌或密钥")
        service = {"provider": "TMDB", "model": None, "endpoint": TMDB_API_BASE_URL,
                   "auth_channel": "V4 Bearer" if effective.get("tmdb.v4_token") else "V3 API Key",
                   "timeout_ms": effective.get("tmdb.timeout_ms")}
    currentMatches = matchSensitiveWordsFromEffective(
        effective,
        [block.file_name, block.file_path, parsed.title],
    )
    policyRow = connection.execute(
        "SELECT MAX(updated_at) FROM system_settings WHERE key IN "
        "('privacy.default_sensitive_words_enabled','privacy.default_sensitive_words','privacy.custom_sensitive_words')",
    ).fetchone()
    policyEvidence = {
        "matched_count": len(currentMatches),
        "matched_words_masked": maskSensitiveWords(currentMatches),
        "policy_revision": str(policyRow[0]) if policyRow and policyRow[0] else "configuration-baseline",
        "policy_summary": "当前生效敏感词策略",
    }
    public = {"block_id": block.id, "source_record_id": block.source_record_id,
              "target_service": target, "file_name": block.file_name, "file_path": block.file_path,
              "parsed": asdict(parsed), "match_source": mode, "service": service,
              "policy_evidence": policyEvidence}
    # 密钥、媒体源凭据仅参与摘要，不写入授权快照或审计正文。
    boundConfig = {key: value for key, value in effective.items()
                   if key.startswith((f"{target}.", "privacy."))}
    fingerprint = hashlib.sha256(_json([public, dict(row), dict(source), block.updated_at, boundConfig]).encode()).hexdigest()
    return public, fingerprint, effective


def _audit(settings, connection, row, *, result, detail, phase):
    context = json.loads(row["context_json"])
    record_audit_event(settings, event_type=f"external_submission.{phase}", action="external_submission_override",
        result=result, summary=f"一次性外部提交：{phase}", target_type="external_submission_block",
        target_id=row["block_id"], actor_id=row["actor_id"], actor_name=row["actor_name"],
        ip_address=context.get("ip_address"), user_agent=context.get("user_agent"), detail=detail, connection=connection)


def prepareExternalSubmission(settings: AppSettings, blockId: int, context: AuditWriteContext) -> dict:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        _checkActor(connection, context)
        snapshot, fingerprint, _ = _snapshot(settings, connection, blockId)
        ensureAuditIntentCapacity(settings, connection, len(_json(snapshot).encode('utf-8')) + 1024)
        grantId = uuid4().hex
        expiresAt = (datetime.now(timezone.utc) + timedelta(seconds=AUTHORIZATION_TTL_SECONDS)).isoformat()
        connection.execute(
            "INSERT INTO external_submission_authorizations "
            "(id,block_id,actor_id,actor_name,fingerprint,snapshot_json,context_json,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (grantId, blockId, context.actor.id, context.actor.username, fingerprint, _json(snapshot),
             _json({"ip_address": context.ipAddress, "user_agent": context.userAgent}), _now(), expiresAt),
        )
        row = connection.execute("SELECT * FROM external_submission_authorizations WHERE id=?", (grantId,)).fetchone()
        _audit(settings, connection, row, result="success", detail={
            "submission_executed": False,
            "matched_count": snapshot["policy_evidence"]["matched_count"],
            "matched_words_masked": snapshot["policy_evidence"]["matched_words_masked"],
            "policy_revision": snapshot["policy_evidence"]["policy_revision"],
        }, phase="prepared")
        connection.commit()
    return {"id": grantId, "snapshot": snapshot, "expires_at": expiresAt}


def _grant(connection, grantId, blockId, context):
    _checkActor(connection, context)
    row = connection.execute("SELECT * FROM external_submission_authorizations WHERE id=? AND block_id=?",
                             (grantId, blockId)).fetchone()
    if row is None or row["actor_id"] != context.actor.id:
        raise LookupError("一次性提交授权不存在或已按保留策略清理")
    return row


def _finishAudit(settings, grantId) -> bool:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM external_submission_authorizations WHERE id=?", (grantId,)).fetchone()
        if row is None or row["status"] != "consumed" or not row["outcome_json"]:
            return False
        outcome = json.loads(row["outcome_json"])
        auditResult = {"completed": "success", "failed": "failed"}.get(outcome["status"], "pending")
        _audit(settings, connection, row, result=auditResult,
               detail={"status": outcome["status"], "submission_executed": outcome.get("submission_executed", True),
                       "candidate_count": len(outcome["candidates"])}, phase="outcome")
        connection.execute("UPDATE external_submission_authorizations SET status='completed',completed_at=? WHERE id=?",
                           (_now(), grantId))
        connection.commit()
    return True


def submitExternalSubmission(settings: AppSettings, blockId: int, grantId: str, reason: str,
                             context: AuditWriteContext) -> dict:
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("风险确认原因需为 1 至 1000 个字符")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = _grant(connection, grantId, blockId, context)
        if row["status"] != "prepared" or row["expires_at"] <= _now():
            raise ValueError("授权已使用或已过期，不会重复提交")
        snapshot, fingerprint, effective = _snapshot(settings, connection, blockId)
        if fingerprint != row["fingerprint"]:
            raise ValueError("文件、媒体源或服务配置已变化，请重新确认")
        connection.execute("UPDATE external_submission_authorizations SET status='consumed',consumed_at=? WHERE id=?",
                           (_now(), grantId))
        connection.execute("UPDATE external_submission_blocks SET status='override_submitting',override_reason=?,"
                           "operator=?,decided_at=?,updated_at=? WHERE id=?",
                           (reason, context.actor.username, _now(), _now(), blockId))
        _audit(settings, connection, row, result="pending", detail={"override_reason": reason,
            "target_service": snapshot["target_service"], "submission_executed": False,
            "matched_count": snapshot["policy_evidence"]["matched_count"],
            "matched_words_masked": snapshot["policy_evidence"]["matched_words_masked"],
            "policy_revision": snapshot["policy_evidence"]["policy_revision"]}, phase="consumed")
        connection.commit()
    # 授权先消费再发请求，网络期间不持有数据库写锁；中断后不重放。
    parsed = ParsedMediaName(**snapshot["parsed"])
    permit = ExternalSubmissionPermit(submissionPayloadFingerprint("rename_preview", snapshot["source_record_id"],
        snapshot["file_name"], snapshot["file_path"], parsed.title, snapshot["target_service"], asdict(parsed)))
    outcome = {"id": grantId, "status": "unknown", "message": "提交结果不明，请人工核查；不会自动重试", "candidates": []}
    try:
        if snapshot["target_service"] == "ai":
            effective = {**effective, "ai.max_retries": 0}
            result = parse_media_with_ai(settings, "rename_preview", snapshot["source_record_id"], snapshot["file_name"],
                snapshot["file_path"], parsed, authorization=permit, effectiveSettings=effective)
            if result.status == "success":
                outcome.update(status="completed", message="AI 解析完成，未自动应用候选或重命名",
                               candidates=[_candidatePayload(item) for item in result.candidates])
            elif result.error_type in {"budget_wait", "budget_unavailable"}:
                outcome.update(status="failed", message=result.message, submission_executed=False,
                               error_type=result.error_type)
            elif result.http_status in {400, 401, 403, 404, 422, 429}:
                outcome.update(status="failed", message="AI 服务明确拒绝请求，请检查配置或限流状态；不会自动重试",
                               http_status=result.http_status, error_type=result.error_type)
        else:
            check_external_submission(settings, "rename_preview", snapshot["source_record_id"], snapshot["file_name"],
                snapshot["file_path"], parsed.title, "tmdb", authorization=permit, parsedPayload=asdict(parsed))
            token = str(effective.get("tmdb.v4_token") or "")
            from app.service.external_request_budget_service import ExternalRequestBudget
            client = TmdbClient(requestBudget=ExternalRequestBudget(settings, "tmdb"),
                api_key="" if token else str(effective.get("tmdb.api_key") or ""), v4_token=token,
                language=str(effective.get("tmdb.language") or "zh-CN"), region=str(effective.get("tmdb.region") or "CN"),
                timeout_ms=int(effective.get("tmdb.timeout_ms") or 10000))
            candidates = client.search(parsed)
            outcome.update(status="completed", message="TMDB 查询完成，未自动应用候选或重命名",
                           candidates=[_candidatePayload(item) for item in candidates])
    except Exception as exc:  # 已发请求无法保证可重放，只记录异常类型，不持久化响应或凭据。
        outcome["error_type"] = type(exc).__name__
        cause = exc.__cause__ or exc
        from app.service.external_request_budget_service import ExternalBudgetWaitError, ExternalBudgetUnavailable
        if isinstance(exc, (ExternalBudgetWaitError, ExternalBudgetUnavailable)):
            outcome.update(status="failed", message=str(exc), submission_executed=False)
        if isinstance(cause, HTTPError) and cause.code in {400, 401, 403, 404, 422, 429}:
            outcome.update(status="failed", message="TMDB 服务明确拒绝请求，请检查配置或限流状态；不会自动重试", http_status=cause.code)
        _diagnostic("request", exc)
    auditPending = True
    try:
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE external_submission_authorizations SET outcome_json=? WHERE id=?",
                               (_json(outcome), grantId))
            connection.execute("UPDATE external_submission_blocks SET status=?,updated_at=? WHERE id=?",
                               ({"completed": "override_completed", "failed": "override_failed"}.get(outcome["status"], "override_unknown"), _now(), blockId))
            connection.commit()
        auditPending = not _finishAudit(settings, grantId)
    except Exception as exc:  # 审计故障不得使客户端重发已执行的外部请求。
        # 已完成的外部请求不能因审计失败而变成可重试错误。
        _diagnostic("outcome", exc)
    return {**outcome, "audit_pending": auditPending}


def getExternalSubmissionOutcome(settings, blockId, grantId, context) -> dict:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _grant(connection, grantId, blockId, context)
        if row["outcome_json"]:
            return {**json.loads(row["outcome_json"]), "audit_pending": row["status"] != "completed"}
        return {"id": grantId, "status": "prepared" if row["status"] == "prepared" else "unknown",
                "message": "尚未提交" if row["status"] == "prepared" else "未记录确定结果，请人工核查，不会重试",
                "candidates": [], "audit_pending": row["status"] != "prepared"}


def getLatestExternalSubmissionOutcome(settings, blockId, context) -> dict:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _checkActor(connection, context)
        row = connection.execute("SELECT id FROM external_submission_authorizations WHERE block_id=? AND actor_id=? "
                                 "AND status!='prepared' ORDER BY consumed_at DESC,id DESC LIMIT 1",
                                 (blockId, context.actor.id)).fetchone()
        if row is None:
            raise LookupError("未找到当前用户的提交结果，可能已按保留策略清理；旧记录不代表实际提交成功")
    return getExternalSubmissionOutcome(settings, blockId, row["id"], context)


def flushExternalSubmissionAudits(settings, limit: int = 200, afterId: str = "") -> dict:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        rows = connection.execute("SELECT id FROM external_submission_authorizations WHERE status='consumed' "
            "AND outcome_json IS NOT NULL AND id>? ORDER BY id LIMIT ?", (afterId, max(1, min(limit, 200)) + 1)).fetchall()
    selected = rows[:max(1, min(limit, 200))]
    completed = 0
    for (grantId,) in selected:
        completed += int(_finishAudit(settings, grantId))
    return {"completed": completed, "next_id": selected[-1][0] if selected else "", "has_more": len(rows) > len(selected)}
