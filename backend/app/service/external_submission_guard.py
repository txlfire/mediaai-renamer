"""External submission protection service."""

from contextlib import closing
from datetime import datetime, timezone
from dataclasses import dataclass, field
from threading import Lock
import hashlib
import json
import re
import sqlite3

from app.core.config import AppSettings
from app.service.audit_service import AuditWriteContext, record_audit_event
from app.schema.external_submission import (
    ExternalSubmissionBlockList,
    ExternalSubmissionBlockRecord,
    ExternalSubmissionCheckResult,
)
from app.service.settings_service import DEFAULT_MEDIA_RISK_SENSITIVE_WORDS, get_effective_settings


BLOCK_RULE_TYPE_SENSITIVE_WORD = "sensitive_word"
BLOCK_STATUS_BLOCKED = "blocked"
BLOCK_STATUS_RENAMED = "renamed"
BLOCK_STATUS_IGNORED = "ignored"
BLOCK_STATUS_OVERRIDE_SUBMITTED = "override_submitted"
BLOCK_STATUS_ARCHIVED = "archived"
BLOCK_DECISION_STATUSES = {
    BLOCK_STATUS_BLOCKED,
    BLOCK_STATUS_RENAMED,
    BLOCK_STATUS_IGNORED,
    BLOCK_STATUS_OVERRIDE_SUBMITTED,
    BLOCK_STATUS_ARCHIVED,
}
BLOCK_EXECUTION_STATUSES = {"override_submitting", "override_completed", "override_failed", "override_unknown"}


def submissionPayloadFingerprint(sourceModule, sourceId, fileName, filePath, title, target, parsed) -> str:
    payload = [sourceModule, sourceId, fileName, filePath, title, target, parsed]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class ExternalSubmissionPermit:
    """仅由已消费授权构造的进程内能力，不接受客户端直接提供。"""
    fingerprint: str
    used: bool = False
    lock: Lock = field(default_factory=Lock, repr=False)

    def consume(self, fingerprint: str) -> bool:
        with self.lock:
            if self.used or fingerprint != self.fingerprint:
                raise ValueError("外部提交授权范围不匹配或已使用")
            self.used = True
            return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_words(words: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for word in words:
        trimmed = str(word).strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key not in seen:
            normalized.append(trimmed)
            seen.add(key)
    return normalized


def get_sensitive_words(settings: AppSettings) -> list[str]:
    """Return default and custom sensitive words in matching order."""

    return sensitiveWordsFromEffective(get_effective_settings(settings))


def sensitiveWordsFromEffective(effective: dict[str, object]) -> list[str]:
    """Return words from one already captured effective-settings snapshot."""

    default_enabled = bool(effective.get("privacy.default_sensitive_words_enabled"))
    default_value = effective.get("privacy.default_sensitive_words")
    default_words = default_value if isinstance(default_value, list) else DEFAULT_MEDIA_RISK_SENSITIVE_WORDS
    custom = effective.get("privacy.custom_sensitive_words")
    custom_words = custom if isinstance(custom, list) else []
    return _normalize_words([*(default_words if default_enabled else []), *custom_words])


def _mask_word(word: str) -> str:
    if len(word) <= 2:
        return f"{word[0]}*" if word else ""
    if word.isascii() and len(word) > 4:
        return f"{word[:2]}***{word[-2:]}"
    return f"{word[0]}***{word[-1]}"


def _row_to_block_record(row: sqlite3.Row) -> ExternalSubmissionBlockRecord:
    return ExternalSubmissionBlockRecord(
        id=int(row["id"]),
        source_module=str(row["source_module"]),
        source_record_id=int(row["source_record_id"]),
        file_name=str(row["file_name"]),
        file_path=str(row["file_path"]),
        match_title=row["match_title"],
        target_service=str(row["target_service"]),
        block_rule_type=str(row["block_rule_type"]),
        block_rule_name=str(row["block_rule_name"]),
        matched_value_masked=str(row["matched_value_masked"]),
        status=str(row["status"]),
        user_decision=row["user_decision"],
        override_reason=row["override_reason"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        decided_at=row["decided_at"],
        operator=row["operator"],
    )


def _get_block_record(connection: sqlite3.Connection, block_id: int) -> ExternalSubmissionBlockRecord:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id, source_module, source_record_id, file_name, file_path, match_title, "
        "target_service, block_rule_type, block_rule_name, matched_value_masked, status, "
        "user_decision, override_reason, created_at, updated_at, decided_at, operator "
        "FROM external_submission_blocks WHERE id = ?",
        (block_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("外部提交拦截记录写入失败")
    return _row_to_block_record(row)


def _find_existing_block(
    connection: sqlite3.Connection,
    source_module: str,
    source_record_id: int,
    target_service: str,
    block_rule_type: str,
) -> ExternalSubmissionBlockRecord | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id, source_module, source_record_id, file_name, file_path, match_title, "
        "target_service, block_rule_type, block_rule_name, matched_value_masked, status, "
        "user_decision, override_reason, created_at, updated_at, decided_at, operator "
        "FROM external_submission_blocks "
        "WHERE source_module = ? AND source_record_id = ? AND target_service = ? "
        "AND block_rule_type = ? AND status = ? "
        "ORDER BY id DESC LIMIT 1",
        (
            source_module,
            source_record_id,
            target_service,
            block_rule_type,
            BLOCK_STATUS_BLOCKED,
        ),
    ).fetchone()
    return _row_to_block_record(row) if row else None


def _save_block_record(
    settings: AppSettings,
    source_module: str,
    source_record_id: int,
    file_name: str,
    file_path: str,
    match_title: str | None,
    target_service: str,
    matched_words: list[str],
) -> ExternalSubmissionBlockRecord:
    now = _utc_now()
    matched_value_masked = "、".join(_mask_word(word) for word in matched_words)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        existing = _find_existing_block(
            connection,
            source_module,
            source_record_id,
            target_service,
            BLOCK_RULE_TYPE_SENSITIVE_WORD,
        )
        if existing is not None:
            connection.execute(
                "UPDATE external_submission_blocks "
                "SET file_name = ?, file_path = ?, match_title = ?, matched_value_masked = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    file_name,
                    file_path,
                    match_title,
                    matched_value_masked,
                    now,
                    existing.id,
                ),
            )
            connection.commit()
            return _get_block_record(connection, existing.id)

        cursor = connection.execute(
            "INSERT INTO external_submission_blocks "
            "(source_module, source_record_id, file_name, file_path, match_title, "
            "target_service, block_rule_type, block_rule_name, matched_value_masked, "
            "status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_module,
                source_record_id,
                file_name,
                file_path,
                match_title,
                target_service,
                BLOCK_RULE_TYPE_SENSITIVE_WORD,
                "敏感词过滤",
                matched_value_masked,
                BLOCK_STATUS_BLOCKED,
                now,
                now,
            ),
        )
        connection.commit()
        return _get_block_record(connection, int(cursor.lastrowid))


def _matches_sensitive_word(combined_text: str, word: str) -> bool:
    normalizedWord = word.casefold()
    if normalizedWord.isascii() and normalizedWord.isalnum() and len(normalizedWord) <= 3:
        pattern = rf"(?<![a-z0-9]){re.escape(normalizedWord)}(?![a-z0-9])"
        return re.search(pattern, combined_text) is not None
    return normalizedWord in combined_text


def _match_sensitive_words(settings: AppSettings, values: list[str | None]) -> list[str]:
    return matchSensitiveWordsFromEffective(get_effective_settings(settings), values)


def matchSensitiveWordsFromEffective(
    effective: dict[str, object],
    values: list[str | None],
) -> list[str]:
    """Match values against one immutable effective-settings snapshot."""

    combined_text = "\n".join(str(value or "") for value in values).casefold()
    return [word for word in sensitiveWordsFromEffective(effective) if _matches_sensitive_word(combined_text, word)]


def maskSensitiveWords(words: list[str]) -> list[str]:
    """Return display-safe evidence without exposing configured words."""

    return [_mask_word(word) for word in words]


def check_external_submission(
    settings: AppSettings,
    source_module: str,
    source_record_id: int,
    file_name: str,
    file_path: str,
    match_title: str | None,
    target_service: str,
    *,
    authorization: ExternalSubmissionPermit | None = None,
    parsedPayload: dict | None = None,
) -> ExternalSubmissionCheckResult:
    """Check and record whether one external submission should be blocked."""

    if authorization is not None:
        authorization.consume(submissionPayloadFingerprint(source_module, source_record_id, file_name, file_path,
                                                            match_title, target_service, parsedPayload))
        return ExternalSubmissionCheckResult(allowed=True, matched_words=[], message=None)
    matched_words = _match_sensitive_words(settings, [file_name, file_path, match_title])
    if not matched_words:
        return ExternalSubmissionCheckResult(allowed=True, matched_words=[], message=None)

    record = _save_block_record(
        settings,
        source_module=source_module,
        source_record_id=source_record_id,
        file_name=file_name,
        file_path=file_path,
        match_title=match_title,
        target_service=target_service,
        matched_words=matched_words,
    )
    return ExternalSubmissionCheckResult(
        allowed=False,
        matched_words=matched_words,
        message="已阻止外部提交，请手动处理",
        block_record=record,
    )


def list_external_submission_blocks(
    settings: AppSettings,
    status: str | None = None,
    target_service: str | None = None,
    limit: int = 200,
) -> ExternalSubmissionBlockList:
    """List external submission block records for later user decisions."""

    conditions: list[str] = []
    params: list[object] = []
    if status:
        if status not in BLOCK_DECISION_STATUSES | BLOCK_EXECUTION_STATUSES:
            raise ValueError(f"不支持的拦截记录状态: {status}")
        conditions.append("status = ?")
        params.append(status)
    if target_service:
        conditions.append("target_service = ?")
        params.append(target_service)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query_params = list(params)
    list_params = [*params, max(1, min(limit, 1000))]

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        total_row = connection.execute(
            f"SELECT COUNT(*) FROM external_submission_blocks {where_sql}",
            query_params,
        ).fetchone()
        total = int(total_row[0]) if total_row else 0
        rows = connection.execute(
            "SELECT id, source_module, source_record_id, file_name, file_path, match_title, "
            "target_service, block_rule_type, block_rule_name, matched_value_masked, status, "
            "user_decision, override_reason, created_at, updated_at, decided_at, operator "
            f"FROM external_submission_blocks {where_sql} "
            "ORDER BY id DESC LIMIT ?",
            list_params,
        ).fetchall()
    return ExternalSubmissionBlockList(
        items=[_row_to_block_record(row) for row in rows],
        total=total,
    )


def update_external_submission_block_decision(
    settings: AppSettings,
    block_id: int,
    status: str,
    user_decision: str | None,
    override_reason: str | None,
    operator: str,
    *,
    auditContext: AuditWriteContext | None = None,
) -> ExternalSubmissionBlockRecord:
    """Persist a user's decision for one external submission block record."""

    if status not in BLOCK_DECISION_STATUSES:
        raise ValueError(f"不支持的拦截记录状态: {status}")
    if status == BLOCK_STATUS_OVERRIDE_SUBMITTED and not (override_reason or "").strip():
        raise ValueError("无视规则继续提交必须填写风险确认原因")
    if status == BLOCK_STATUS_OVERRIDE_SUBMITTED:
        raise ValueError("仅修改状态不会提交；请使用一次性确认提交接口")

    now = _utc_now()
    context = auditContext or AuditWriteContext()
    decision_text = (user_decision or "").strip() or _default_decision_text(status)
    reason_text = (override_reason or "").strip() or None
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute(
            "SELECT id,target_service,status FROM external_submission_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        if exists is None:
            raise LookupError("外部提交拦截记录不存在")
        if exists["status"] in {"override_submitting", "override_unknown"}:
            raise ValueError("执行中或未知结果的记录不可修改，请先人工核查")

        connection.execute(
            "UPDATE external_submission_blocks "
            "SET status = ?, user_decision = ?, override_reason = ?, operator = ?, "
            "decided_at = ?, updated_at = ? WHERE id = ?",
            (
                status,
                decision_text,
                reason_text,
                operator,
                now,
                now,
                block_id,
            ),
        )
        record_audit_event(settings, event_type="external_submission.decision", action="update_external_submission_block",
            result="success", summary=f"保存外部提交拦截决定：{status}", target_type="external_submission_block",
            target_id=block_id, actor=context.actor, actor_name=operator if context.actor is None else None,
            ip_address=context.ipAddress, user_agent=context.userAgent,
            detail={"status": status, "user_decision": decision_text, "override_reason": reason_text,
                    "target_service": exists["target_service"], "submission_executed": False}, connection=connection)
        connection.commit()
        return _get_block_record(connection, block_id)


def _default_decision_text(status: str) -> str:
    if status == BLOCK_STATUS_RENAMED:
        return "用户已修改名称后重新处理"
    if status == BLOCK_STATUS_IGNORED:
        return "用户选择不进行匹配并忽略"
    if status == BLOCK_STATUS_OVERRIDE_SUBMITTED:
        return "用户确认风险后继续提交"
    if status == BLOCK_STATUS_ARCHIVED:
        return "用户归档拦截记录"
    return "恢复为待处理"
