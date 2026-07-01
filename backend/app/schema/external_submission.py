"""External submission protection data models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalSubmissionBlockRecord:
    """One blocked external submission record."""

    id: int
    source_module: str
    source_record_id: int
    file_name: str
    file_path: str
    match_title: str | None
    target_service: str
    block_rule_type: str
    block_rule_name: str
    matched_value_masked: str
    status: str
    user_decision: str | None
    override_reason: str | None
    created_at: str
    updated_at: str
    decided_at: str | None
    operator: str | None


@dataclass(frozen=True)
class ExternalSubmissionCheckResult:
    """Result of checking whether an external submission is allowed."""

    allowed: bool
    matched_words: list[str]
    message: str | None
    block_record: ExternalSubmissionBlockRecord | None = None
