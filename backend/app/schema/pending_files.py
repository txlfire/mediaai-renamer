"""Pending file operation models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingFile:
    """File routed to the pending queue instead of normal preview generation."""

    id: int
    media_source_id: int
    scan_job_id: int
    file_path: str
    file_name: str
    extension: str
    file_size: int
    reason: str
    status: str
    created_at: str
