"""扫描服务。

负责全量扫描媒体源目录、按批处理文件并保存扫描任务和媒体文件结果。
"""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

from app.core.config import AppSettings
from app.schema.media import MediaFile, ScanJob
from app.service.media_source_service import get_media_source
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


def run_full_scan(settings: AppSettings, media_source_id: int) -> ScanJob:
    """对媒体源执行全量扫描。

    Args:
        settings: 应用运行配置。
        media_source_id: 媒体源 ID。

    Returns:
        完成后的扫描任务。
    """

    source = get_media_source(settings, media_source_id)
    source_path = Path(source.path)
    created_at = _utc_now()

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            "INSERT INTO scan_jobs "
            "(media_source_id, status, batch_size, batch_interval_seconds, "
            "started_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                media_source_id,
                "running",
                settings.scan.batch_size,
                settings.scan.batch_interval_seconds,
                created_at,
                created_at,
            ),
        )
        job_id = int(cursor.lastrowid)
        connection.commit()

        scanned_count = 0
        video_count = 0
        warning_count = 0
        try:
            for file_path in sorted(source_path.rglob("*")):
                if not file_path.is_file():
                    continue

                scanned_count += 1
                if is_video_file(file_path):
                    stat = file_path.stat()
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
                    settings.scan.batch_interval_seconds > 0
                    and scanned_count % settings.scan.batch_size == 0
                ):
                    time.sleep(settings.scan.batch_interval_seconds)

            ended_at = _utc_now()
            connection.execute(
                "UPDATE scan_jobs SET status = ?, scanned_count = ?, "
                "video_count = ?, warning_count = ?, ended_at = ? WHERE id = ?",
                (
                    "completed",
                    scanned_count,
                    video_count,
                    warning_count,
                    ended_at,
                    job_id,
                ),
            )
            connection.commit()
        except Exception as exc:
            ended_at = _utc_now()
            connection.execute(
                "UPDATE scan_jobs SET status = ?, scanned_count = ?, "
                "video_count = ?, warning_count = ?, error_message = ?, "
                "ended_at = ? WHERE id = ?",
                (
                    "failed",
                    scanned_count,
                    video_count,
                    warning_count,
                    str(exc),
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
