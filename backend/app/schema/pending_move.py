"""待处理迁移批次及只读逐项结果。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingMoveItem:
    id: int
    pending_file_id: int
    source_path: str
    target_path: str
    status: str
    message: str | None


@dataclass(frozen=True)
class PendingMoveTask:
    id: int
    status: str
    target_directory: str
    total_count: int
    moved_count: int
    failed_count: int
    error_message: str | None
    created_at: str
    updated_at: str
    items: list[PendingMoveItem]
