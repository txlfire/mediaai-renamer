"""媒体扫描相关数据模型。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MediaSource:
    """媒体源目录。"""

    id: int
    name: str
    path: str
    enabled: bool
    created_at: str
    updated_at: str
    path_type: str = "local"
    protocol: str = "local"
    host: str | None = None
    share_name: str | None = None
    domain: str | None = None
    username: str | None = None
    secret: str | None = None
    has_secret: bool = False
    port: int | None = None
    remark: str | None = None
    nfs_host: str | None = None
    nfs_export: str | None = None
    nfs_version: str | None = None
    nfs_options: str | None = None
    local_mount_path: str | None = None


@dataclass(frozen=True)
class LocalDirectoryEntry:
    """本地目录浏览条目。"""

    name: str
    path: str
    is_directory: bool
    readable: bool | None = None
    writable: bool | None = None


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
    scan_mode: str = "full"
    new_count: int = 0
    changed_count: int = 0
    skipped_count: int = 0
    missing_count: int = 0
    indexed_count: int = 0


@dataclass(frozen=True)
class ScanModeSuggestion:
    """扫描模式建议。"""

    media_source_id: int
    recommended_mode: str
    has_index: bool
    indexed_count: int
    last_scan_status: str | None
    reason: str


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
    extra: dict[str, str] = field(default_factory=dict)


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
    metadata_source: str | None
    metadata_match_status: str | None
    metadata_match_score: int
    metadata_message: str | None
    status: str
    message: str | None
    created_at: str
    updated_at: str
    metadata_candidate_count: int = 0
    title_source: str = "file_name"
    parent_folder_title: str | None = None
    recognition_mode: str = "parent_folder_fallback"
    title_conflict_message: str | None = None
    naming_template_type: str | None = None
    naming_template_version: int | None = None
    naming_template_updated_at: str | None = None
    current_naming_template_version: int | None = None
    is_naming_template_outdated: bool = False
    naming_template_status: str = "unknown"


@dataclass(frozen=True)
class PreviewGenerationSummary:
    """Rename preview generation summary."""

    generated_count: int
    needs_review_count: int
    edited_kept_count: int


@dataclass(frozen=True)
class RenameOperationItem:
    """One item in a safe rename operation."""

    id: int
    operation_id: int
    rename_preview_id: int
    source_path: str
    target_path: str
    status: str
    message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RenameOperation:
    """Safe rename operation with item results."""

    id: int
    status: str
    mode: str
    total_count: int
    ready_count: int
    conflict_count: int
    renamed_count: int
    failed_count: int
    created_at: str
    updated_at: str
    items: list[RenameOperationItem]


@dataclass(frozen=True)
class RenameRollbackItem:
    """One item in a rename rollback plan."""

    id: int
    plan_id: int
    operation_item_id: int
    current_path: str
    rollback_path: str
    status: str
    message: str | None
    executed_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RenameRollbackPlan:
    """Rollback plan for one completed rename operation."""

    id: int
    operation_id: int
    status: str
    item_count: int
    executable_count: int
    conflict_count: int
    created_by: str | None
    created_at: str
    updated_at: str
    items: list[RenameRollbackItem]
