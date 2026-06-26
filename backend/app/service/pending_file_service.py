"""Pending file queue service."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3

from app.core.config import AppSettings
from app.schema.pending_files import PendingFile


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_pending_file(row: sqlite3.Row) -> PendingFile:
    return PendingFile(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        scan_job_id=int(row["scan_job_id"]),
        file_path=str(row["file_path"]),
        file_name=str(row["file_name"]),
        extension=str(row["extension"]),
        file_size=int(row["file_size"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def add_pending_file(
    connection: sqlite3.Connection,
    media_source_id: int,
    scan_job_id: int,
    file_path: Path,
    file_size: int,
    reason: str,
) -> None:
    """Insert one file into the pending queue using the caller's transaction."""

    connection.execute(
        "INSERT INTO pending_files "
        "(media_source_id, scan_job_id, file_path, file_name, extension, file_size, reason, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            media_source_id,
            scan_job_id,
            str(file_path),
            file_path.name,
            file_path.suffix.lower(),
            file_size,
            reason,
            "pending",
            _utc_now(),
        ),
    )


def list_pending_files(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
) -> list[PendingFile]:
    """List pending files with optional source/job filters."""

    conditions = ["status = 'pending'"]
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)
    where_clause = f" WHERE {' AND '.join(conditions)}"

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
            f"file_size, reason, status, created_at FROM pending_files{where_clause} ORDER BY id",
            params,
        ).fetchall()
    return [_row_to_pending_file(row) for row in rows]


def remove_pending_file(settings: AppSettings, pending_file_id: int) -> PendingFile:
    """Mark one pending file task as removed without touching the real file."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
            "file_size, reason, status, created_at FROM pending_files WHERE id = ?",
            (pending_file_id,),
        ).fetchone()
        if row is None:
            raise ValueError("待处理文件不存在")
        connection.execute(
            "UPDATE pending_files SET status = ? WHERE id = ?",
            ("removed", pending_file_id),
        )
        connection.commit()
    return _row_to_pending_file(row)


def clear_pending_files(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
) -> int:
    """Mark matching pending file tasks as removed."""

    conditions = ["status = 'pending'"]
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        cursor = connection.execute(
            f"UPDATE pending_files SET status = 'removed' WHERE {' AND '.join(conditions)}",
            params,
        )
        connection.commit()
        return int(cursor.rowcount)


def move_pending_files(
    settings: AppSettings,
    pending_file_ids: list[int],
    target_directory: str,
) -> list[PendingFile]:
    """Move selected files to a target directory and mark tasks as moved."""

    target_path = Path(target_directory)
    if not target_path.exists() or not target_path.is_dir():
        raise ValueError("目标目录不存在")
    if not pending_file_ids:
        raise ValueError("请选择待迁移文件")

    moved: list[PendingFile] = []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        placeholders = ", ".join("?" for _ in pending_file_ids)
        rows = connection.execute(
            "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension, "
            f"file_size, reason, status, created_at FROM pending_files WHERE status = 'pending' AND id IN ({placeholders})",
            pending_file_ids,
        ).fetchall()
        for row in rows:
            source_path = Path(str(row["file_path"]))
            if not source_path.exists():
                raise ValueError(f"源文件不存在: {source_path}")
            destination = target_path / source_path.name
            if destination.exists():
                raise ValueError(f"目标文件已存在: {destination}")
            shutil.move(str(source_path), str(destination))
            connection.execute(
                "UPDATE pending_files SET status = ?, file_path = ? WHERE id = ?",
                ("moved", str(destination), row["id"]),
            )
            moved.append(_row_to_pending_file(row))
        connection.commit()
    return moved
