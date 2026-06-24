"""媒体扫描相关数据模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaSource:
    """媒体源目录。"""

    id: int
    name: str
    path: str
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ScanJob:
    """扫描任务。"""

    id: int
    media_source_id: int
    status: str
    batch_size: int
    batch_interval_seconds: float
    scanned_count: int
    video_count: int
    warning_count: int
    error_message: str | None
    started_at: str | None
    ended_at: str | None
    created_at: str


@dataclass(frozen=True)
class MediaFile:
    """扫描识别出的媒体文件。"""

    id: int
    media_source_id: int
    scan_job_id: int
    file_path: str
    file_name: str
    extension: str
    file_size: int
    modified_at: str
    created_at: str
