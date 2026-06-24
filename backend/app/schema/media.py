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
class LocalDirectoryEntry:
    """本地目录浏览条目。"""

    name: str
    path: str
    is_directory: bool


@dataclass(frozen=True)
class LocalDirectoryListing:
    """本地目录浏览结果。"""

    current_path: str | None
    parent_path: str | None
    entries: list[LocalDirectoryEntry]


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


@dataclass(frozen=True)
class ParsedMediaName:
    """Local filename parsing result."""

    media_type: str
    title: str
    year: int | None
    season: int | None
    episode: int | None
    message: str | None = None


@dataclass(frozen=True)
class RenamePreview:
    """Standard rename preview record."""

    id: int
    media_file_id: int
    file_path: str
    file_name: str
    media_type: str
    parsed_title: str
    parsed_year: int | None
    season: int | None
    episode: int | None
    original_extension: str
    suggested_name: str
    edited_name: str | None
    current_target_name: str
    status: str
    message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PreviewGenerationSummary:
    """Rename preview generation summary."""

    generated_count: int
    needs_review_count: int
    edited_kept_count: int
