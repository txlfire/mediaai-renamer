"""Rename preview service."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import PurePath
import sqlite3

from app.core.config import AppSettings
from app.schema.media import PreviewGenerationSummary, RenamePreview
from app.utils.filename_parser import parse_media_filename
from app.utils.naming_template import build_preview_name


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_preview(row: sqlite3.Row) -> RenamePreview:
    edited_name = row["edited_name"]
    suggested_name = str(row["suggested_name"])
    return RenamePreview(
        id=int(row["id"]),
        media_file_id=int(row["media_file_id"]),
        file_path=str(row["file_path"]),
        file_name=str(row["file_name"]),
        media_type=str(row["media_type"]),
        parsed_title=str(row["parsed_title"]),
        parsed_year=row["parsed_year"],
        season=row["season"],
        episode=row["episode"],
        original_extension=str(row["original_extension"]),
        suggested_name=suggested_name,
        edited_name=edited_name,
        current_target_name=edited_name or suggested_name,
        status=str(row["status"]),
        message=row["message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _query_media_files(
    connection: sqlite3.Connection,
    media_source_id: int | None,
    scan_job_id: int | None,
    media_file_ids: list[int] | None,
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("scan_job_id = ?")
        params.append(scan_job_id)
    if media_file_ids:
        placeholders = ", ".join("?" for _ in media_file_ids)
        conditions.append(f"id IN ({placeholders})")
        params.extend(media_file_ids)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return connection.execute(
        "SELECT id, media_source_id, scan_job_id, file_path, file_name, extension "
        f"FROM media_files{where_clause} ORDER BY id",
        params,
    ).fetchall()


def generate_rename_previews(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    media_file_ids: list[int] | None = None,
    overwrite_edited: bool = False,
) -> PreviewGenerationSummary:
    """Generate or update rename preview records from scanned media files."""

    generated_count = 0
    needs_review_count = 0
    edited_kept_count = 0
    now = _utc_now()

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = _query_media_files(connection, media_source_id, scan_job_id, media_file_ids)
        for row in rows:
            parsed = parse_media_filename(str(row["file_name"]))
            suggested_name = build_preview_name(parsed, str(row["extension"]))
            status = "needs_review" if parsed.media_type == "unknown" or parsed.message else "generated"
            if status == "needs_review":
                needs_review_count += 1
            else:
                generated_count += 1

            existing = connection.execute(
                "SELECT edited_name FROM rename_previews WHERE media_file_id = ?",
                (row["id"],),
            ).fetchone()
            edited_name = None
            if existing and existing["edited_name"] and not overwrite_edited:
                edited_name = existing["edited_name"]
                status = "edited"
                edited_kept_count += 1

            connection.execute(
                "INSERT INTO rename_previews "
                "(media_file_id, media_type, parsed_title, parsed_year, season, episode, "
                "original_extension, suggested_name, edited_name, status, message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(media_file_id) DO UPDATE SET "
                "media_type = excluded.media_type, "
                "parsed_title = excluded.parsed_title, "
                "parsed_year = excluded.parsed_year, "
                "season = excluded.season, "
                "episode = excluded.episode, "
                "original_extension = excluded.original_extension, "
                "suggested_name = excluded.suggested_name, "
                "edited_name = excluded.edited_name, "
                "status = excluded.status, "
                "message = excluded.message, "
                "updated_at = excluded.updated_at",
                (
                    row["id"],
                    parsed.media_type,
                    parsed.title,
                    parsed.year,
                    parsed.season,
                    parsed.episode,
                    str(row["extension"]).lower(),
                    suggested_name,
                    edited_name,
                    status,
                    parsed.message,
                    now,
                    now,
                ),
            )
        connection.commit()

    return PreviewGenerationSummary(generated_count, needs_review_count, edited_kept_count)


def list_rename_previews(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    status: str | None = None,
    media_type: str | None = None,
    keyword: str | None = None,
) -> list[RenamePreview]:
    """List rename previews with optional filters."""

    conditions: list[str] = []
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("mf.media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("mf.scan_job_id = ?")
        params.append(scan_job_id)
    if status:
        conditions.append("rp.status = ?")
        params.append(status)
    if media_type:
        conditions.append("rp.media_type = ?")
        params.append(media_type)
    if keyword:
        conditions.append(
            "(mf.file_name LIKE ? OR mf.file_path LIKE ? OR rp.parsed_title LIKE ? "
            "OR rp.suggested_name LIKE ? OR rp.edited_name LIKE ?)"
        )
        pattern = f"%{keyword}%"
        params.extend([pattern, pattern, pattern, pattern, pattern])

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT rp.id, rp.media_file_id, mf.file_path, mf.file_name, rp.media_type, "
            "rp.parsed_title, rp.parsed_year, rp.season, rp.episode, rp.original_extension, "
            "rp.suggested_name, rp.edited_name, rp.status, rp.message, rp.created_at, rp.updated_at "
            "FROM rename_previews rp "
            "JOIN media_files mf ON mf.id = rp.media_file_id"
            f"{where_clause} ORDER BY rp.id",
            params,
        ).fetchall()
    return [_row_to_preview(row) for row in rows]


def update_rename_preview(
    settings: AppSettings,
    preview_id: int,
    target_name: str,
) -> RenamePreview:
    """Save an edited target filename for a preview record."""

    normalized = target_name.strip()
    if not normalized:
        raise ValueError("目标文件名不能为空")
    if PurePath(normalized).name != normalized or "/" in normalized or "\\" in normalized:
        raise ValueError("目标文件名不能包含目录路径")

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT original_extension FROM rename_previews WHERE id = ?",
            (preview_id,),
        ).fetchone()
        if row is None:
            raise ValueError("命名预览不存在")

        extension = str(row["original_extension"])
        if extension and not normalized.lower().endswith(extension.lower()):
            normalized = f"{normalized}{extension}"

        connection.execute(
            "UPDATE rename_previews SET edited_name = ?, status = ?, updated_at = ? WHERE id = ?",
            (normalized, "edited", now, preview_id),
        )
        connection.commit()

    previews = list_rename_previews(settings)
    for preview in previews:
        if preview.id == preview_id:
            return preview
    raise ValueError("命名预览不存在")
