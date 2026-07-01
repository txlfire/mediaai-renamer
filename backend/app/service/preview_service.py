"""Rename preview service."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import PurePath
import sqlite3

from app.core.config import AppSettings
from app.schema.media import ParsedMediaName, PreviewGenerationSummary, RenamePreview
from app.schema.metadata import MetadataCandidate, MetadataMatchResult
from app.service.metadata_matcher import MATCH_STATUS_HIGH
from app.service.metadata_service import MetadataProvider, match_metadata_candidates
from app.service.settings_service import get_effective_settings
from app.utils.filename_parser import parse_media_filename
from app.utils.naming_template import build_preview_name_with_settings, template_variables_for_media_type

METADATA_MATCH_SOURCE_PARSED_TITLE = "parsed_title"
METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME = "original_file_name"
METADATA_MATCH_SOURCES = {
    METADATA_MATCH_SOURCE_PARSED_TITLE,
    METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME,
}


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
        metadata_source=row["metadata_source"],
        metadata_match_status=row["metadata_match_status"],
        metadata_match_score=int(row["metadata_match_score"] or 0),
        metadata_message=row["metadata_message"],
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
    effective_settings = get_effective_settings(settings)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = _query_media_files(connection, media_source_id, scan_job_id, media_file_ids)
        for row in rows:
            parsed = parse_media_filename(str(row["file_name"]))
            suggested_name = build_preview_name_with_settings(parsed, str(row["extension"]), effective_settings)
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
            "rp.suggested_name, rp.edited_name, rp.metadata_source, rp.metadata_match_status, "
            "rp.metadata_match_score, rp.metadata_message, rp.status, rp.message, "
            "rp.created_at, rp.updated_at "
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


def _get_preview_row(connection: sqlite3.Connection, preview_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT rp.id, rp.media_file_id, mf.file_path, mf.file_name, rp.media_type, "
        "rp.parsed_title, rp.parsed_year, rp.season, rp.episode, rp.original_extension, "
        "rp.suggested_name, rp.edited_name, rp.metadata_source, rp.metadata_match_status, "
        "rp.metadata_match_score, rp.metadata_message, rp.status, rp.message, "
        "rp.created_at, rp.updated_at "
        "FROM rename_previews rp "
        "JOIN media_files mf ON mf.id = rp.media_file_id "
        "WHERE rp.id = ?",
        (preview_id,),
    ).fetchone()
    if row is None:
        raise ValueError("命名预览不存在")
    return row


def _parsed_from_preview(
    row: sqlite3.Row,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> ParsedMediaName:
    if metadata_match_source not in METADATA_MATCH_SOURCES:
        raise ValueError("涓嶆敮鎸佺殑 TMDB 鍖归厤鏉ユ簮")
    if metadata_match_source == METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME:
        return parse_media_filename(str(row["file_name"]))
    return ParsedMediaName(
        media_type=str(row["media_type"]),
        title=str(row["parsed_title"]),
        year=row["parsed_year"],
        season=row["season"],
        episode=row["episode"],
        message=row["message"],
    )


def _preview_by_id(settings: AppSettings, preview_id: int) -> RenamePreview:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _row_to_preview(_get_preview_row(connection, preview_id))


def _candidate_title(candidate: MetadataCandidate, fallback: str) -> str:
    return candidate.title or candidate.original_title or fallback


def _merged_candidate_parsed(
    row: sqlite3.Row,
    candidate: MetadataCandidate,
    effective_settings: dict[str, object],
) -> ParsedMediaName:
    media_type = candidate.media_type or str(row["media_type"])
    variables = template_variables_for_media_type(media_type, effective_settings)
    needs_title = not variables or "title" in variables or "original_title" in variables
    needs_year = "year" in variables
    needs_episode_fields = bool(variables.intersection({"season", "episode", "season_episode", "seasonEpisode"}))

    title = _candidate_title(candidate, str(row["parsed_title"])) if needs_title else _candidate_title(candidate, "")
    year = candidate.year
    if needs_year:
        year = year if year is not None else row["parsed_year"]

    season = candidate.season
    episode = candidate.episode
    if media_type == "episode" and needs_episode_fields:
        season = season if season is not None else row["season"]
        episode = episode if episode is not None else row["episode"]

    return ParsedMediaName(
        media_type=media_type,
        title=title or str(row["parsed_title"]),
        year=year,
        season=season,
        episode=episode,
    )


def _candidate_preview_name(parsed: ParsedMediaName, extension: str, effective_settings: dict[str, object]) -> str:
    return build_preview_name_with_settings(parsed, extension, effective_settings)


def _metadata_message(match_status: str, score: int, message: str | None) -> str | None:
    if message:
        return message
    if match_status == "low_confidence":
        return f"匹配度 {score}%，低于自动回填阈值 85%，请人工确认候选。"
    if match_status == "manual_selected":
        return f"已选择候选回填，匹配度 {score}%。"
    if match_status == MATCH_STATUS_HIGH:
        return f"匹配度 {score}%，已达到自动回填阈值。"
    if match_status == "failed":
        return "未找到可用候选或候选匹配度过低。"
    return message


def _update_preview_metadata(
    settings: AppSettings,
    preview_id: int,
    candidate: MetadataCandidate | None,
    match_status: str,
    score: int,
    message: str | None,
    auto_backfill: bool,
    preview_status: str,
    source_override: str | None = None,
) -> RenamePreview:
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
        suggested_name = row["suggested_name"]
        edited_name = row["edited_name"]
        media_type = row["media_type"]
        parsed_title = row["parsed_title"]
        parsed_year = row["parsed_year"]
        season = row["season"]
        episode = row["episode"]
        if candidate is not None and auto_backfill:
            effective_settings = get_effective_settings(settings)
            candidate_parsed = _merged_candidate_parsed(row, candidate, effective_settings)
            suggested_name = _candidate_preview_name(candidate_parsed, str(row["original_extension"]), effective_settings)
            edited_name = None
            media_type = candidate_parsed.media_type
            parsed_title = candidate_parsed.title
            parsed_year = candidate_parsed.year
            season = candidate_parsed.season
            episode = candidate_parsed.episode
        final_message = _metadata_message(match_status, score, message)

        connection.execute(
            "UPDATE rename_previews SET "
            "media_type = ?, "
            "parsed_title = ?, "
            "parsed_year = ?, "
            "season = ?, "
            "episode = ?, "
            "suggested_name = ?, "
            "edited_name = ?, "
            "metadata_source = ?, "
            "metadata_match_status = ?, "
            "metadata_match_score = ?, "
            "metadata_message = ?, "
            "status = ?, "
            "message = ?, "
            "updated_at = ? "
            "WHERE id = ?",
            (
                media_type,
                parsed_title,
                parsed_year,
                season,
                episode,
                suggested_name,
                edited_name,
                source_override or (candidate.provider if candidate else None),
                match_status,
                score,
                final_message,
                preview_status,
                final_message,
                now,
                preview_id,
            ),
        )
        connection.commit()

    return _preview_by_id(settings, preview_id)


def list_metadata_candidates(
    settings: AppSettings,
    preview_id: int,
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> list[MetadataMatchResult]:
    """Return sorted metadata candidates for one rename preview."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
    result = match_metadata_candidates(
        settings,
        _parsed_from_preview(row, metadata_match_source),
        provider=provider,
    )
    return result.matches


def match_rename_preview_metadata(
    settings: AppSettings,
    preview_id: int,
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> RenamePreview:
    """Match one preview against TMDB and auto-backfill high confidence results."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
    result = match_metadata_candidates(
        settings,
        _parsed_from_preview(row, metadata_match_source),
        provider=provider,
    )
    if not result.matches:
        return _update_preview_metadata(
            settings,
            preview_id,
            None,
            result.status,
            0,
            result.message,
            auto_backfill=False,
            preview_status="needs_review",
            source_override=result.metadata_source,
        )

    best = result.matches[0]
    is_high_confidence = best.status == MATCH_STATUS_HIGH
    return _update_preview_metadata(
        settings,
        preview_id,
        best.candidate,
        best.status,
        best.score,
        result.message,
        auto_backfill=is_high_confidence,
        preview_status="tmdb_matched" if is_high_confidence else "needs_review",
        source_override=result.metadata_source if result.metadata_source != "custom" else None,
    )


def apply_metadata_candidate(
    settings: AppSettings,
    preview_id: int,
    candidate: MetadataCandidate,
    score: int,
) -> RenamePreview:
    """Apply a user-selected metadata candidate to one preview."""

    return _update_preview_metadata(
        settings,
        preview_id,
        candidate,
        "manual_selected",
        score,
        None,
        auto_backfill=True,
        preview_status="tmdb_selected",
    )


def match_rename_previews_metadata(
    settings: AppSettings,
    preview_ids: list[int],
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """Batch match selected rename previews against TMDB."""

    unique_ids = list(dict.fromkeys(preview_ids))
    updated: list[RenamePreview] = []
    failed: list[dict[str, object]] = []
    for preview_id in unique_ids:
        try:
            updated.append(
                match_rename_preview_metadata(
                    settings,
                    preview_id,
                    provider=provider,
                    metadata_match_source=metadata_match_source,
                )
            )
        except ValueError as exc:
            failed.append({"id": preview_id, "message": str(exc)})
    return {
        "total_count": len(unique_ids),
        "success_count": len(updated),
        "failed_count": len(failed),
        "items": updated,
        "failed_items": failed,
    }


def match_all_unmatched_metadata(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """Batch match all previews that have no metadata match result."""

    conditions = ["(rp.metadata_match_status IS NULL OR rp.metadata_match_status = '')"]
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("mf.media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("mf.scan_job_id = ?")
        params.append(scan_job_id)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        rows = connection.execute(
            "SELECT rp.id FROM rename_previews rp "
            "JOIN media_files mf ON mf.id = rp.media_file_id "
            f"WHERE {' AND '.join(conditions)} ORDER BY rp.id",
            params,
        ).fetchall()
    return match_rename_previews_metadata(
        settings,
        [int(row[0]) for row in rows],
        provider=provider,
        metadata_match_source=metadata_match_source,
    )
