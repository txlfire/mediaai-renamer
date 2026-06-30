"""扫描服务。

负责全量扫描媒体源目录、按批处理文件并保存扫描任务和媒体文件结果。
"""

from contextlib import closing
from datetime import datetime, timezone
import errno
from pathlib import Path
import sqlite3
import time

from app.core.config import AppSettings
from app.schema.media import MediaFile, ScanJob
from app.service.media_source_service import get_media_source, get_media_source_protocol_context
from app.service.pending_file_service import add_pending_file
from app.service.shared_protocols.registry import get_protocol
from app.service.settings_service import get_effective_settings
from app.utils.media_file import is_video_file


def _utc_now() -> str:
    """返回 ISO 8601 UTC 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


def _row_to_scan_job(row: sqlite3.Row) -> ScanJob:
    """将数据库行转换为扫描任务模型。"""

    return ScanJob(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        status=str(row["status"]),
        batch_size=int(row["batch_size"]),
        batch_interval_seconds=float(row["batch_interval_seconds"]),
        scanned_count=int(row["scanned_count"]),
        video_count=int(row["video_count"]),
        warning_count=int(row["warning_count"]),
        error_message=row["error_message"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        created_at=str(row["created_at"]),
    )


def _row_to_media_file(row: sqlite3.Row) -> MediaFile:
    """将数据库行转换为媒体文件模型。"""

    return MediaFile(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        scan_job_id=int(row["scan_job_id"]),
        file_path=str(row["file_path"]),
        file_name=str(row["file_name"]),
        extension=str(row["extension"]),
        file_size=int(row["file_size"]),
        modified_at=str(row["modified_at"]),
        created_at=str(row["created_at"]),
    )


def _fetch_scan_job(connection: sqlite3.Connection, job_id: int) -> ScanJob:
    """从当前连接读取扫描任务。"""

    row = connection.execute(
        "SELECT id, media_source_id, status, batch_size, batch_interval_seconds, "
        "scanned_count, video_count, warning_count, error_message, started_at, "
        "ended_at, created_at FROM scan_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError("扫描任务不存在")
    return _row_to_scan_job(row)


def recover_interrupted_scan_jobs(settings: AppSettings) -> int:
    """Mark scan jobs left running before process restart as failed."""

    ended_at = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        cursor = connection.execute(
            "UPDATE scan_jobs SET status = ?, error_message = ?, ended_at = ? "
            "WHERE status = ? AND ended_at IS NULL",
            (
                "failed",
                "服务重启后任务未完成，已标记为失败，请重新发起扫描",
                ended_at,
                "running",
            ),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def _is_hidden_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    return any(part.startswith(".") for part in relative_parts)


def _scan_failure_status(exc: Exception) -> str:
    error_number = getattr(exc, "errno", None)
    if error_number in {errno.ESTALE, errno.ETIMEDOUT}:
        return "connection_lost"
    message = str(exc).lower()
    if "stale" in message or "timed out" in message or "connection" in message:
        return "connection_lost"
    return "failed"


def _scan_error_message(exc: Exception) -> str:
    error_number = getattr(exc, "errno", None)
    if isinstance(exc, TimeoutError):
        return "共享目录连接中断：文件系统操作超时，请检查网络或 NAS 状态"
    if error_number == errno.ESTALE:
        return "共享目录连接中断：NFS 文件句柄过期，请检查挂载状态后重试"
    if error_number == errno.ETIMEDOUT:
        return "共享目录连接中断：文件系统操作超时，请检查网络或 NAS 状态"
    if error_number == errno.EACCES:
        return "权限不足：服务运行用户无法访问该文件或目录"
    return str(exc)


def _stat_with_nfs_retry(file_path: Path, retry_count: int, timeout_seconds: int) -> object:
    attempts = 0
    while True:
        try:
            started_at = time.monotonic()
            stat = file_path.stat()
            elapsed_seconds = time.monotonic() - started_at
            if elapsed_seconds > timeout_seconds:
                raise TimeoutError(f"NFS operation timed out after {timeout_seconds} seconds")
            return stat
        except OSError as exc:
            attempts += 1
            if getattr(exc, "errno", None) != errno.ESTALE or attempts > retry_count:
                raise


def run_full_scan(settings: AppSettings, media_source_id: int) -> ScanJob:
    """对媒体源执行全量扫描。

    Args:
        settings: 应用运行配置。
        media_source_id: 媒体源 ID。

    Returns:
        完成后的扫描任务。
    """

    source = get_media_source(settings, media_source_id)
    if not source.enabled:
        raise ValueError("媒体源已停用，无法发起扫描")
    protocol = get_protocol(source.path_type)
    scan_ready = protocol.check_scan_ready(
        source.path,
        get_media_source_protocol_context(settings, source.id),
    )
    if not scan_ready.success:
        raise ValueError(scan_ready.message)
    source_path = Path(protocol.normalize_path(source.path))
    created_at = _utc_now()
    effective_settings = get_effective_settings(settings)
    minimum_file_size = int(effective_settings.get("scan.minimum_file_size") or 0)
    batch_size = int(effective_settings.get("scan.batch_size") or settings.scan.batch_size)
    batch_interval_seconds = float(
        effective_settings.get("scan.batch_interval_seconds")
        if effective_settings.get("scan.batch_interval_seconds") is not None
        else settings.scan.batch_interval_seconds
    )
    skip_hidden_files = bool(effective_settings.get("scan.skip_hidden_files", True))
    recursive = bool(effective_settings.get("scan.recursive", True))
    nfs_retry_count = int(effective_settings.get("shared.nfs_retry_count") or 1)
    nfs_operation_timeout_seconds = int(effective_settings.get("shared.nfs_operation_timeout_seconds") or 30)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            "INSERT INTO scan_jobs "
            "(media_source_id, status, batch_size, batch_interval_seconds, "
            "started_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                media_source_id,
                "running",
                batch_size,
                batch_interval_seconds,
                created_at,
                created_at,
            ),
        )
        job_id = int(cursor.lastrowid)
        connection.commit()

        scanned_count = 0
        video_count = 0
        warning_count = 0
        warning_details: list[str] = []
        try:
            path_iterator = source_path.rglob("*") if recursive else source_path.iterdir()
            for file_path in sorted(path_iterator):
                if skip_hidden_files and _is_hidden_path(file_path, source_path):
                    continue
                try:
                    if not file_path.is_file():
                        continue
                except OSError as exc:
                    warning_count += 1
                    warning_details.append(f"{file_path.name}: {_scan_error_message(exc)}")
                    continue

                scanned_count += 1
                if is_video_file(file_path):
                    try:
                        stat = _stat_with_nfs_retry(file_path, nfs_retry_count, nfs_operation_timeout_seconds)
                    except (OSError, TimeoutError) as exc:
                        if getattr(exc, "errno", None) in {errno.EACCES, errno.ESTALE, errno.ETIMEDOUT}:
                            warning_count += 1
                            warning_details.append(f"{file_path.name}: {_scan_error_message(exc)}")
                            continue
                        if isinstance(exc, TimeoutError):
                            warning_count += 1
                            warning_details.append(f"{file_path.name}: {_scan_error_message(exc)}")
                            continue
                        raise
                    if stat.st_size < minimum_file_size:
                        add_pending_file(
                            connection,
                            media_source_id,
                            job_id,
                            file_path,
                            stat.st_size,
                            "size_filtered",
                        )
                        warning_count += 1
                        warning_details.append(f"{file_path.name}: 文件小于最小扫描大小")
                        continue
                    now = _utc_now()
                    connection.execute(
                        "INSERT INTO media_files "
                        "(media_source_id, scan_job_id, file_path, file_name, "
                        "extension, file_size, modified_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            media_source_id,
                            job_id,
                            str(file_path),
                            file_path.name,
                            file_path.suffix.lower(),
                            stat.st_size,
                            datetime.fromtimestamp(
                                stat.st_mtime, timezone.utc
                            ).isoformat(),
                            now,
                        ),
                    )
                    video_count += 1

                if (
                    batch_interval_seconds > 0
                    and scanned_count % batch_size == 0
                ):
                    time.sleep(batch_interval_seconds)

            ended_at = _utc_now()
            completed_status = "partial_completed" if warning_count else "completed"
            warning_message = None
            if warning_count:
                detail_text = "，".join(warning_details[:5])
                more_text = f" 等{warning_count}个文件" if warning_count > len(warning_details[:5]) else ""
                warning_message = f"部分完成：{warning_count}个文件扫描失败（{detail_text}{more_text}）"
            connection.execute(
                "UPDATE scan_jobs SET status = ?, scanned_count = ?, "
                "video_count = ?, warning_count = ?, error_message = ?, ended_at = ? WHERE id = ?",
                (
                    completed_status,
                    scanned_count,
                    video_count,
                    warning_count,
                    warning_message,
                    ended_at,
                    job_id,
                ),
            )
            connection.commit()
        except Exception as exc:
            ended_at = _utc_now()
            failure_status = _scan_failure_status(exc)
            connection.execute(
                "UPDATE scan_jobs SET status = ?, scanned_count = ?, "
                "video_count = ?, warning_count = ?, error_message = ?, "
                "ended_at = ? WHERE id = ?",
                (
                    failure_status,
                    scanned_count,
                    video_count,
                    warning_count,
                    _scan_error_message(exc),
                    ended_at,
                    job_id,
                ),
            )
            connection.commit()
            raise

        return _fetch_scan_job(connection, job_id)


def _list_scan_jobs_unfiltered(settings: AppSettings) -> list[ScanJob]:
    """查询全部扫描任务。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, status, batch_size, batch_interval_seconds, "
            "scanned_count, video_count, warning_count, error_message, started_at, "
            "ended_at, created_at FROM scan_jobs ORDER BY id"
        ).fetchall()
    return [_row_to_scan_job(row) for row in rows]


def _list_media_files_unfiltered(settings: AppSettings) -> list[MediaFile]:
    """查询全部媒体文件扫描结果。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, "
            "extension, file_size, modified_at, created_at "
            "FROM media_files ORDER BY id"
        ).fetchall()
    return [_row_to_media_file(row) for row in rows]


def list_scan_jobs(settings: AppSettings, media_source_id: int | None = None) -> list[ScanJob]:
    """List scan jobs, optionally limited to one media source."""

    conditions: list[str] = []
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, status, batch_size, batch_interval_seconds, "
            "scanned_count, video_count, warning_count, error_message, started_at, "
            f"ended_at, created_at FROM scan_jobs{where_clause} ORDER BY id",
            params,
        ).fetchall()
    return [_row_to_scan_job(row) for row in rows]


def list_media_files(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
) -> list[MediaFile]:
    """List scanned media files, optionally limited by source or scan job."""

    conditions: list[str] = []
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, "
            "extension, file_size, modified_at, created_at "
            f"FROM media_files{where_clause} ORDER BY id",
            params,
        ).fetchall()
    return [_row_to_media_file(row) for row in rows]
