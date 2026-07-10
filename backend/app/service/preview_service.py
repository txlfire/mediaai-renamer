"""Rename preview service."""

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePath
import json
import os
import sqlite3

from app.core.config import AppSettings
from app.schema.ai_parse import AiParseCandidate, AiParseResult
from app.schema.media import ParsedMediaName, PreviewGenerationSummary, RenamePreview
from app.schema.metadata import MetadataCandidate, MetadataMatchResult
from app.service.ai_provider import AiProvider
from app.service.ai_parse_service import parse_media_with_ai
from app.service.external_submission_guard import check_external_submission
from app.service.metadata_matcher import MATCH_STATUS_HIGH
from app.service.metadata_multi_source_service import (
    MULTI_MATCH_MODE_FALLBACK,
    MULTI_MATCH_MODES,
    match_multi_source_metadata_candidates,
    provider_search_results_to_dict,
)
from app.service.metadata_service import MetadataProvider, match_metadata_candidates
from app.service.pending_file_service import add_pending_file
from app.service.settings_service import get_effective_settings
from app.utils.filename_parser import parse_media_filename
from app.utils.naming_template import build_preview_name_with_settings, template_variables_for_media_type

METADATA_MATCH_SOURCE_PARSED_TITLE = "parsed_title"
METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME = "original_file_name"
METADATA_MATCH_SOURCE_PARENT_FOLDER_TITLE = "parent_folder_title"
METADATA_MATCH_SOURCES = {
    METADATA_MATCH_SOURCE_PARSED_TITLE,
    METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME,
    METADATA_MATCH_SOURCE_PARENT_FOLDER_TITLE,
}
TITLE_RECOGNITION_MODE_FILE_NAME_FIRST = "file_name_first"
TITLE_RECOGNITION_MODE_PARENT_FOLDER_FALLBACK = "parent_folder_fallback"
TITLE_RECOGNITION_MODE_PARENT_FOLDER_FIRST = "parent_folder_first"
TITLE_RECOGNITION_MODE_MANUAL_ONLY = "manual_only"
TITLE_RECOGNITION_MODES = {
    TITLE_RECOGNITION_MODE_FILE_NAME_FIRST,
    TITLE_RECOGNITION_MODE_PARENT_FOLDER_FALLBACK,
    TITLE_RECOGNITION_MODE_PARENT_FOLDER_FIRST,
    TITLE_RECOGNITION_MODE_MANUAL_ONLY,
}
TEMPLATE_STATUS_CURRENT = "current"
TEMPLATE_STATUS_OUTDATED = "outdated"
TEMPLATE_STATUS_UNKNOWN = "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _template_type_for_media_type(media_type: str | None) -> str:
    return "episode" if media_type == "episode" else "movie"


def _template_metadata(
    effective_settings: dict[str, object],
    media_type: str | None,
) -> tuple[str, int, str]:
    template_type = _template_type_for_media_type(media_type)
    prefix = f"naming.{template_type}_template"
    version = int(effective_settings.get(f"{prefix}_version") or 1)
    updated_at = str(effective_settings.get(f"{prefix}_updated_at") or "")
    return template_type, version, updated_at


def _current_template_version(
    effective_settings: dict[str, object],
    template_type: str | None,
    media_type: str | None,
) -> int:
    normalized_type = template_type if template_type in {"movie", "episode"} else _template_type_for_media_type(media_type)
    return int(effective_settings.get(f"naming.{normalized_type}_template_version") or 1)


def _template_status(snapshot_version: int | None, current_version: int | None) -> tuple[str, bool]:
    if snapshot_version is None or current_version is None:
        return TEMPLATE_STATUS_UNKNOWN, False
    if snapshot_version < current_version:
        return TEMPLATE_STATUS_OUTDATED, True
    return TEMPLATE_STATUS_CURRENT, False


def _row_to_preview(row: sqlite3.Row, effective_settings: dict[str, object]) -> RenamePreview:
    edited_name = row["edited_name"]
    suggested_name = str(row["suggested_name"])
    metadata_candidates_json = row["metadata_candidates_json"] if "metadata_candidates_json" in row.keys() else None
    naming_template_type = row["naming_template_type"] if "naming_template_type" in row.keys() else None
    naming_template_version = row["naming_template_version"] if "naming_template_version" in row.keys() else None
    current_naming_template_version = _current_template_version(
        effective_settings,
        naming_template_type,
        str(row["media_type"]),
    )
    naming_template_status, is_naming_template_outdated = _template_status(
        int(naming_template_version) if naming_template_version is not None else None,
        current_naming_template_version,
    )
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
        metadata_candidate_count=len(_deserialize_metadata_matches(metadata_candidates_json)),
        title_source=str(row["title_source"]) if "title_source" in row.keys() else "file_name",
        parent_folder_title=row["parent_folder_title"] if "parent_folder_title" in row.keys() else None,
        recognition_mode=str(row["recognition_mode"]) if "recognition_mode" in row.keys() else "parent_folder_fallback",
        title_conflict_message=row["title_conflict_message"] if "title_conflict_message" in row.keys() else None,
        naming_template_type=naming_template_type,
        naming_template_version=int(naming_template_version) if naming_template_version is not None else None,
        naming_template_updated_at=row["naming_template_updated_at"] if "naming_template_updated_at" in row.keys() else None,
        current_naming_template_version=current_naming_template_version,
        is_naming_template_outdated=is_naming_template_outdated,
        naming_template_status=naming_template_status,
    )


def _serialize_metadata_matches(matches: list[MetadataMatchResult]) -> str | None:
    if not matches:
        return None
    return json.dumps([asdict(match) for match in matches], ensure_ascii=False)


def _deserialize_metadata_matches(value: str | None) -> list[MetadataMatchResult]:
    if not value:
        return []
    try:
        raw_items = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_items, list):
        return []
    matches: list[MetadataMatchResult] = []
    for item in raw_items:
        if not isinstance(item, dict) or not isinstance(item.get("candidate"), dict):
            continue
        try:
            matches.append(
                MetadataMatchResult(
                    candidate=MetadataCandidate(**item["candidate"]),
                    score=int(item.get("score") or 0),
                    status=str(item.get("status") or ""),
                )
            )
        except TypeError:
            continue
    return matches


def _status_for_target(file_path: str, original_name: str, target_name: str, fallback_status: str) -> str:
    if original_name != target_name:
        return fallback_status
    if fallback_status == "needs_review":
        return fallback_status
    path = Path(file_path)
    if path.exists() and os.access(path, os.R_OK | os.W_OK):
        return "no_rename"
    return "unable_rename"


def _has_usable_title(parsed: ParsedMediaName) -> bool:
    return bool(parsed.title and parsed.title.strip())


def _message_without_title_warning(message: str | None) -> str | None:
    if message == "无法识别标题":
        return None
    return message


def _title_recognition_mode(effective_settings: dict[str, object]) -> str:
    value = str(
        effective_settings.get("naming.title_recognition_mode")
        or TITLE_RECOGNITION_MODE_PARENT_FOLDER_FALLBACK
    ).strip()
    if value not in TITLE_RECOGNITION_MODES:
        return TITLE_RECOGNITION_MODE_PARENT_FOLDER_FALLBACK
    return value


def _normalized_title_key(title: str) -> str:
    return "".join(char for char in title.casefold() if char.isalnum())


def _is_title_conflict(file_title: str, parent_title: str) -> bool:
    if not file_title.strip() or not parent_title.strip():
        return False
    return _normalized_title_key(file_title) != _normalized_title_key(parent_title)


def _parsed_with_parent_title(parsed: ParsedMediaName, parent_parsed: ParsedMediaName, parent_title: str) -> ParsedMediaName:
    media_type = parsed.media_type if parsed.media_type != "unknown" else parent_parsed.media_type
    if parsed.season is not None or parsed.episode is not None:
        media_type = "episode"
    return ParsedMediaName(
        media_type=media_type,
        title=parent_title,
        year=parsed.year if parsed.year is not None else parent_parsed.year,
        season=parsed.season,
        episode=parsed.episode,
        message=_message_without_title_warning(parsed.message),
        extra=parsed.extra,
    )


def _parent_title_conflict_message(file_title: str, parent_title: str) -> str:
    return f"文件名标题“{file_title}”与上级文件夹标题“{parent_title}”不一致，请人工确认。"


def _manual_only_candidate_message(parent_title: str) -> str:
    return f"当前识别模式为手动确认，可参考上级文件夹标题“{parent_title}”。"


def _merge_parent_folder_title(
    row: sqlite3.Row,
    parsed: ParsedMediaName,
    recognition_mode: str,
) -> tuple[ParsedMediaName, str, str | None, str | None]:
    """根据当前识别模式决定是否使用上级文件夹标题。"""

    file_parent = Path(str(row["file_path"])).parent
    media_source_path = Path(str(row["media_source_path"])) if row["media_source_path"] else None
    if media_source_path is not None and file_parent == media_source_path:
        return parsed, "file_name", None, None

    parent_name = file_parent.name
    if not parent_name:
        return parsed, "file_name", None, None

    parent_parsed = parse_media_filename(parent_name)
    parent_title = parent_parsed.title.strip()
    if not parent_title:
        return parsed, "file_name", None, None

    has_file_title = _has_usable_title(parsed)
    if has_file_title and _is_title_conflict(parsed.title, parent_title):
        return parsed, "file_name", parent_title, _parent_title_conflict_message(parsed.title, parent_title)

    if recognition_mode == TITLE_RECOGNITION_MODE_MANUAL_ONLY:
        message = _manual_only_candidate_message(parent_title) if not has_file_title else None
        return parsed, "file_name", parent_title, message

    if recognition_mode == TITLE_RECOGNITION_MODE_FILE_NAME_FIRST:
        return parsed, "file_name", parent_title, None

    if recognition_mode == TITLE_RECOGNITION_MODE_PARENT_FOLDER_FIRST and parent_title:
        return _parsed_with_parent_title(parsed, parent_parsed, parent_title), "parent_folder", parent_title, None

    if recognition_mode == TITLE_RECOGNITION_MODE_PARENT_FOLDER_FALLBACK and not has_file_title:
        return _parsed_with_parent_title(parsed, parent_parsed, parent_title), "parent_folder", parent_title, None

    return parsed, "file_name", parent_title, None


def _query_media_files(
    connection: sqlite3.Connection,
    media_source_id: int | None,
    scan_job_id: int | None,
    media_file_ids: list[int] | None,
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    params: list[object] = []
    if media_source_id is not None:
        conditions.append("mf.media_source_id = ?")
        params.append(media_source_id)
    if scan_job_id is not None:
        conditions.append("mf.scan_job_id = ?")
        params.append(scan_job_id)
    if media_file_ids:
        placeholders = ", ".join("?" for _ in media_file_ids)
        conditions.append(f"mf.id IN ({placeholders})")
        params.extend(media_file_ids)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return connection.execute(
        "SELECT mf.id, mf.media_source_id, mf.scan_job_id, mf.file_path, mf.file_name, mf.extension, "
        "ms.path AS media_source_path "
        "FROM media_files mf "
        "JOIN media_sources ms ON ms.id = mf.media_source_id"
        f"{where_clause} ORDER BY mf.id",
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
    recognition_mode = _title_recognition_mode(effective_settings)

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = _query_media_files(connection, media_source_id, scan_job_id, media_file_ids)
        for row in rows:
            parsed = parse_media_filename(str(row["file_name"]))
            parsed, title_source, parent_folder_title, title_conflict_message = _merge_parent_folder_title(
                row,
                parsed,
                recognition_mode,
            )
            template_type, template_version, template_updated_at = _template_metadata(
                effective_settings,
                parsed.media_type,
            )
            suggested_name = build_preview_name_with_settings(parsed, str(row["extension"]), effective_settings)
            status = "needs_review" if parsed.media_type == "unknown" or parsed.message else "generated"
            message = title_conflict_message or parsed.message
            if title_conflict_message:
                status = "needs_review"
            status = _status_for_target(str(row["file_path"]), str(row["file_name"]), suggested_name, status)
            if status == "needs_review":
                needs_review_count += 1
            elif status == "generated":
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
                "original_extension, suggested_name, edited_name, title_source, parent_folder_title, "
                "recognition_mode, title_conflict_message, naming_template_type, naming_template_version, "
                "naming_template_updated_at, status, message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(media_file_id) DO UPDATE SET "
                "media_type = excluded.media_type, "
                "parsed_title = excluded.parsed_title, "
                "parsed_year = excluded.parsed_year, "
                "season = excluded.season, "
                "episode = excluded.episode, "
                "original_extension = excluded.original_extension, "
                "suggested_name = excluded.suggested_name, "
                "edited_name = excluded.edited_name, "
                "title_source = excluded.title_source, "
                "parent_folder_title = excluded.parent_folder_title, "
                "recognition_mode = excluded.recognition_mode, "
                "title_conflict_message = excluded.title_conflict_message, "
                "naming_template_type = excluded.naming_template_type, "
                "naming_template_version = excluded.naming_template_version, "
                "naming_template_updated_at = excluded.naming_template_updated_at, "
                "metadata_candidates_json = NULL, "
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
                    title_source,
                    parent_folder_title,
                    recognition_mode,
                    title_conflict_message,
                    template_type,
                    template_version,
                    template_updated_at,
                    status,
                    message,
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
    else:
        conditions.append("rp.status != ?")
        params.append("excluded")
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
            "rp.metadata_match_score, rp.metadata_message, rp.metadata_candidates_json, rp.status, rp.message, "
            "rp.title_source, rp.parent_folder_title, rp.recognition_mode, rp.title_conflict_message, "
            "rp.naming_template_type, rp.naming_template_version, rp.naming_template_updated_at, "
            "rp.created_at, rp.updated_at "
            "FROM rename_previews rp "
            "JOIN media_files mf ON mf.id = rp.media_file_id"
            f"{where_clause} ORDER BY rp.id",
            params,
        ).fetchall()
    effective_settings = get_effective_settings(settings)
    return [_row_to_preview(row, effective_settings) for row in rows]


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
            "SELECT rp.original_extension, mf.file_path, mf.file_name "
            "FROM rename_previews rp JOIN media_files mf ON mf.id = rp.media_file_id "
            "WHERE rp.id = ?",
            (preview_id,),
        ).fetchone()
        if row is None:
            raise ValueError("命名预览不存在")

        extension = str(row["original_extension"])
        if extension and not normalized.lower().endswith(extension.lower()):
            normalized = f"{normalized}{extension}"
        status = _status_for_target(str(row["file_path"]), str(row["file_name"]), normalized, "edited")

        connection.execute(
            "UPDATE rename_previews SET edited_name = ?, status = ?, updated_at = ? WHERE id = ?",
            (normalized, status, now, preview_id),
        )
        connection.commit()

    previews = list_rename_previews(settings)
    for preview in previews:
        if preview.id == preview_id:
            return preview
    raise ValueError("命名预览不存在")


def exclude_rename_preview(settings: AppSettings, preview_id: int, reason: str = "manual_excluded") -> RenamePreview:
    """把预览项手动排除到待处理列表，不删除真实文件。"""

    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT rp.id, mf.media_source_id, mf.scan_job_id, mf.file_path, mf.file_size "
            "FROM rename_previews rp JOIN media_files mf ON mf.id = rp.media_file_id "
            "WHERE rp.id = ?",
            (preview_id,),
        ).fetchone()
        if row is None:
            raise ValueError("命名预览不存在")

        add_pending_file(
            connection,
            int(row["media_source_id"]),
            int(row["scan_job_id"]),
            Path(str(row["file_path"])),
            int(row["file_size"]),
            reason,
        )
        connection.execute(
            "UPDATE rename_previews SET status = ?, message = ?, updated_at = ? WHERE id = ?",
            ("excluded", "用户手动排除到待处理列表", now, preview_id),
        )
        connection.commit()

    return _preview_by_id(settings, preview_id)


def exclude_rename_previews(
    settings: AppSettings,
    preview_ids: list[int],
    reason: str = "manual_excluded",
) -> dict[str, object]:
    """批量把预览项排除到待处理列表。"""

    unique_ids = list(dict.fromkeys(preview_ids))
    items: list[RenamePreview] = []
    failed_items: list[dict[str, object]] = []
    for preview_id in unique_ids:
        try:
            items.append(exclude_rename_preview(settings, preview_id, reason))
        except ValueError as exc:
            failed_items.append({"id": preview_id, "message": str(exc)})

    return {
        "total_count": len(unique_ids),
        "success_count": len(items),
        "failed_count": len(failed_items),
        "items": items,
        "failed_items": failed_items,
    }


def _get_preview_row(connection: sqlite3.Connection, preview_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT rp.id, rp.media_file_id, mf.file_path, mf.file_name, rp.media_type, "
        "rp.parsed_title, rp.parsed_year, rp.season, rp.episode, rp.original_extension, "
        "rp.suggested_name, rp.edited_name, rp.metadata_source, rp.metadata_match_status, "
        "rp.metadata_match_score, rp.metadata_message, rp.metadata_candidates_json, rp.status, rp.message, "
        "rp.title_source, rp.parent_folder_title, rp.recognition_mode, rp.title_conflict_message, "
        "rp.naming_template_type, rp.naming_template_version, rp.naming_template_updated_at, "
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
        raise ValueError("不支持的 TMDB 匹配来源")
    if metadata_match_source == METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME:
        return parse_media_filename(str(row["file_name"]))
    if metadata_match_source == METADATA_MATCH_SOURCE_PARENT_FOLDER_TITLE:
        return ParsedMediaName(
            media_type=str(row["media_type"]),
            title=str(row["parent_folder_title"] or "").strip(),
            year=row["parsed_year"],
            season=row["season"],
            episode=row["episode"],
            message=row["title_conflict_message"] or row["message"],
        )
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
        return _row_to_preview(_get_preview_row(connection, preview_id), get_effective_settings(settings))


def _candidate_title(candidate: MetadataCandidate, fallback: str) -> str:
    return candidate.title or candidate.original_title or fallback


def _candidate_extra(row: sqlite3.Row, candidate: MetadataCandidate) -> dict[str, str]:
    title = _candidate_title(candidate, str(row["parsed_title"]))
    english_title = candidate.english_title or candidate.original_title
    chinese_title = candidate.chinese_title or candidate.localized_title or candidate.title
    values = {
        "title": title,
        "localized_title": candidate.localized_title or candidate.title,
        "chinese_title": chinese_title,
        "english_title": english_title,
        "original_title": candidate.original_title or english_title,
        "parsed_title": str(row["parsed_title"]),
        "year": str(candidate.year or row["parsed_year"] or ""),
        "tmdb_id": candidate.tmdb_id or (candidate.provider_id if candidate.provider.upper() == "TMDB" else ""),
        "imdb_id": (candidate.imdb_id or "").removeprefix("tt"),
        "rating": str(candidate.vote_average or ""),
        "overview": candidate.overview,
        "poster_path": candidate.poster_path,
        "language": candidate.original_language,
        "genres": ".".join(candidate.genres),
        "cast": ".".join(candidate.cast),
        "director": ".".join(candidate.directors),
    }
    return {key: value for key, value in values.items() if value not in ("", None)}


def _merged_candidate_parsed(
    row: sqlite3.Row,
    candidate: MetadataCandidate,
    effective_settings: dict[str, object],
    selected_fields: set[str] | None = None,
) -> ParsedMediaName:
    media_type = candidate.media_type or str(row["media_type"])
    variables = template_variables_for_media_type(media_type, effective_settings)
    fillable_variables = variables if selected_fields is None else variables.intersection(selected_fields)
    needs_title = not variables or "title" in variables or "original_title" in variables
    needs_year = "year" in fillable_variables
    needs_episode_fields = bool(fillable_variables.intersection({"season", "episode", "season_episode", "seasonEpisode"}))

    title = _candidate_title(candidate, str(row["parsed_title"])) if needs_title and (selected_fields is None or "title" in selected_fields or "original_title" in selected_fields) else str(row["parsed_title"])
    year = candidate.year
    if needs_year:
        year = year if year is not None else row["parsed_year"]
    elif selected_fields is not None:
        year = row["parsed_year"]

    season = candidate.season
    episode = candidate.episode
    if media_type == "episode" and needs_episode_fields:
        season = season if season is not None else row["season"]
        episode = episode if episode is not None else row["episode"]

    extra = _candidate_extra(row, candidate)
    if selected_fields is not None:
        extra = {key: value for key, value in extra.items() if key in selected_fields or key == "parsed_title"}

    return ParsedMediaName(
        media_type=media_type,
        title=title or str(row["parsed_title"]),
        year=year,
        season=season,
        episode=episode,
        extra=extra,
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
    candidate_matches: list[MetadataMatchResult] | None = None,
    selected_fields: set[str] | None = None,
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
        final_status = str(row["status"])
        naming_template_type = row["naming_template_type"]
        naming_template_version = row["naming_template_version"]
        naming_template_updated_at = row["naming_template_updated_at"]
        if candidate is not None and auto_backfill:
            effective_settings = get_effective_settings(settings)
            candidate_parsed = _merged_candidate_parsed(row, candidate, effective_settings, selected_fields)
            suggested_name = _candidate_preview_name(candidate_parsed, str(row["original_extension"]), effective_settings)
            edited_name = None
            media_type = candidate_parsed.media_type
            parsed_title = candidate_parsed.title
            parsed_year = candidate_parsed.year
            season = candidate_parsed.season
            episode = candidate_parsed.episode
            naming_template_type, naming_template_version, naming_template_updated_at = _template_metadata(
                effective_settings,
                media_type,
            )
            final_status = _status_for_target(str(row["file_path"]), str(row["file_name"]), suggested_name, "generated")
        elif candidate is None and preview_status:
            final_status = preview_status
        final_message = _metadata_message(match_status, score, message)
        serialized_matches = _serialize_metadata_matches(candidate_matches or ([] if candidate is None else [MetadataMatchResult(candidate, score, match_status)]))

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
            "metadata_candidates_json = ?, "
            "naming_template_type = ?, "
            "naming_template_version = ?, "
            "naming_template_updated_at = ?, "
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
                serialized_matches,
                naming_template_type,
                naming_template_version,
                naming_template_updated_at,
                final_status,
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
        cached = _deserialize_metadata_matches(row["metadata_candidates_json"])
    if cached and provider is None:
        return cached
    guard = _check_metadata_external_submission(settings, row, metadata_match_source)
    if not guard.allowed:
        return []
    result = match_metadata_candidates(
        settings,
        _parsed_from_preview(row, metadata_match_source),
        provider=provider,
    )
    return result.matches


def parse_rename_preview_with_ai(
    settings: AppSettings,
    preview_id: int,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
    provider: AiProvider | None = None,
) -> AiParseResult:
    """Run AI structured parsing for one rename preview without mutating the preview."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
    return parse_media_with_ai(
        settings,
        source_module="rename_preview",
        source_record_id=int(row["id"]),
        file_name=str(row["file_name"]),
        file_path=str(row["file_path"]),
        parsed=_parsed_from_preview(row, metadata_match_source),
        provider=provider,
    )


def _merge_ai_usage(total_usage: dict[str, object], usage: dict[str, object]) -> dict[str, object]:
    """合并 AI 用量统计，仅累加数值字段。"""

    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            current = total_usage.get(key, 0)
            total_usage[key] = (current if isinstance(current, (int, float)) else 0) + value
    return total_usage


def parse_rename_previews_with_ai(
    settings: AppSettings,
    preview_ids: list[int],
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """Batch AI parse selected previews without mutating preview records."""

    unique_ids = list(dict.fromkeys(preview_ids))
    items: list[dict[str, object]] = []
    failed_items: list[dict[str, object]] = []
    success_count = 0
    failed_count = 0
    blocked_count = 0
    skipped_count = 0
    usage: dict[str, object] = {}

    for preview_id in unique_ids:
        try:
            result = parse_rename_preview_with_ai(settings, preview_id, metadata_match_source=metadata_match_source)
        except ValueError as exc:
            failed_count += 1
            failed_items.append({"id": preview_id, "message": str(exc)})
            continue

        if result.status == "success":
            success_count += 1
            _merge_ai_usage(usage, result.usage)
        elif result.status == "blocked":
            blocked_count += 1
        elif result.status == "skipped":
            skipped_count += 1
        else:
            failed_count += 1

        items.append({"id": preview_id, "result": result})

    return {
        "total_count": len(unique_ids),
        "success_count": success_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "skipped_count": skipped_count,
        "usage": usage,
        "items": items,
        "failed_items": failed_items,
    }


def match_rename_preview_metadata(
    settings: AppSettings,
    preview_id: int,
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
    auto_backfill: bool = True,
) -> RenamePreview:
    """Match one preview against TMDB and auto-backfill high confidence results."""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
    guard = _check_metadata_external_submission(settings, row, metadata_match_source)
    if not guard.allowed:
        return _update_preview_metadata(
            settings,
            preview_id,
            None,
            "blocked",
            0,
            guard.message or "已阻止外部提交，请手动处理",
            auto_backfill=False,
            preview_status="needs_review",
            source_override="external_submission_guard",
            candidate_matches=[],
        )
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
            candidate_matches=[],
        )

    best = result.matches[0]
    return _update_preview_metadata(
        settings,
        preview_id,
        best.candidate,
        best.status,
        best.score,
        result.message,
        auto_backfill=auto_backfill,
        preview_status="generated",
        source_override=result.metadata_source if result.metadata_source != "custom" else None,
        candidate_matches=result.matches,
    )


def match_rename_preview_multi_source_metadata(
    settings: AppSettings,
    preview_id: int,
    mode: str = MULTI_MATCH_MODE_FALLBACK,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """执行多源元数据匹配并缓存候选，不自动回填目标文件名。"""

    if mode not in MULTI_MATCH_MODES:
        raise ValueError("不支持的多源匹配模式")

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = _get_preview_row(connection, preview_id)
    guard = _check_metadata_external_submission(settings, row, metadata_match_source)
    if not guard.allowed:
        preview = _update_preview_metadata(
            settings,
            preview_id,
            None,
            "blocked",
            0,
            guard.message or "已阻止外部提交，请手动处理",
            auto_backfill=False,
            preview_status="needs_review",
            source_override="external_submission_guard",
            candidate_matches=[],
        )
        return {"preview": preview, "provider_results": []}

    result = match_multi_source_metadata_candidates(
        settings,
        _parsed_from_preview(row, metadata_match_source),
        mode=mode,
    )
    if not result.summary.matches:
        preview = _update_preview_metadata(
            settings,
            preview_id,
            None,
            result.summary.status,
            0,
            result.summary.message,
            auto_backfill=False,
            preview_status="needs_review",
            source_override=result.summary.metadata_source,
            candidate_matches=[],
        )
        return {
            "preview": preview,
            "provider_results": provider_search_results_to_dict(result.provider_results),
        }

    best = result.summary.matches[0]
    preview = _update_preview_metadata(
        settings,
        preview_id,
        best.candidate,
        best.status,
        best.score,
        result.summary.message,
        auto_backfill=False,
        preview_status="generated",
        source_override=result.summary.metadata_source,
        candidate_matches=result.summary.matches,
    )
    return {
        "preview": preview,
        "provider_results": provider_search_results_to_dict(result.provider_results),
    }


def apply_metadata_candidate(
    settings: AppSettings,
    preview_id: int,
    candidate: MetadataCandidate,
    score: int,
    selected_fields: set[str] | None = None,
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
        preview_status="generated",
        selected_fields=selected_fields,
    )


def apply_ai_parse_candidate(
    settings: AppSettings,
    preview_id: int,
    candidate: AiParseCandidate,
) -> RenamePreview:
    """Apply a user-selected AI parse candidate to one preview."""

    metadata_candidate = MetadataCandidate(
        provider="AI",
        provider_id=str(candidate.raw_data.get("provider_id") or "ai"),
        media_type=candidate.media_type,
        title=candidate.title,
        original_title=str(candidate.raw_data.get("original_title") or candidate.title),
        year=candidate.year,
        season=candidate.season,
        episode=candidate.episode,
        overview=str(candidate.raw_data.get("overview") or candidate.reason),
        raw_data=candidate.raw_data,
    )
    return _update_preview_metadata(
        settings,
        preview_id,
        metadata_candidate,
        "manual_selected",
        candidate.confidence,
        candidate.reason,
        auto_backfill=True,
        preview_status="generated",
        source_override="AI",
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
                    auto_backfill=False,
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


def match_rename_previews_multi_source_metadata(
    settings: AppSettings,
    preview_ids: list[int],
    mode: str = MULTI_MATCH_MODE_FALLBACK,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """批量执行多源元数据匹配并保留逐条回填入口。"""

    unique_ids = list(dict.fromkeys(preview_ids))
    items: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    success_count = 0
    failed_count = 0
    blocked_count = 0

    for preview_id in unique_ids:
        try:
            item = match_rename_preview_multi_source_metadata(
                settings,
                preview_id,
                mode=mode,
                metadata_match_source=metadata_match_source,
            )
        except ValueError as exc:
            failed_count += 1
            failed.append({"id": preview_id, "message": str(exc)})
            continue

        preview = item["preview"]
        if isinstance(preview, RenamePreview) and preview.metadata_match_status == "blocked":
            blocked_count += 1
        elif isinstance(preview, RenamePreview) and preview.metadata_match_status == "failed":
            failed_count += 1
        else:
            success_count += 1
        items.append(item)

    return {
        "total_count": len(unique_ids),
        "success_count": success_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "items": items,
        "failed_items": failed,
    }


def _should_run_ai_fallback(preview: RenamePreview) -> bool:
    """判断 TMDB 结果是否需要进入 AI 后备匹配。"""

    return preview.metadata_match_status in {"failed", "low_confidence", "blocked"}


def match_rename_previews_metadata_with_ai_fallback(
    settings: AppSettings,
    preview_ids: list[int],
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """先执行 TMDB 匹配，再对失败或低置信条目执行 AI 解析。"""

    metadata_result = match_rename_previews_metadata(
        settings,
        preview_ids,
        provider=provider,
        metadata_match_source=metadata_match_source,
    )
    fallback_ids = [
        int(preview.id)
        for preview in metadata_result["items"]
        if isinstance(preview, RenamePreview) and _should_run_ai_fallback(preview)
    ]
    ai_result = parse_rename_previews_with_ai(
        settings,
        fallback_ids,
        metadata_match_source=metadata_match_source,
    ) if fallback_ids else {
        "total_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
        "skipped_count": 0,
        "usage": {},
        "items": [],
        "failed_items": [],
    }
    return {
        "total_count": metadata_result["total_count"],
        "metadata": metadata_result,
        "ai": ai_result,
        "fallback_count": len(fallback_ids),
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


def match_all_unmatched_metadata_with_ai_fallback(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    provider: MetadataProvider | None = None,
    metadata_match_source: str = METADATA_MATCH_SOURCE_PARSED_TITLE,
) -> dict[str, object]:
    """对当前范围未匹配条目执行 TMDB，并对未匹配或低置信条目追加 AI 后备解析。"""

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
    return match_rename_previews_metadata_with_ai_fallback(
        settings,
        [int(row[0]) for row in rows],
        provider=provider,
        metadata_match_source=metadata_match_source,
    )


def _check_metadata_external_submission(
    settings: AppSettings,
    row: sqlite3.Row,
    metadata_match_source: str,
):
    parsed = _parsed_from_preview(row, metadata_match_source)
    return check_external_submission(
        settings,
        source_module="rename_preview",
        source_record_id=int(row["id"]),
        file_name=str(row["file_name"]),
        file_path=str(row["file_path"]),
        match_title=parsed.title,
        target_service="tmdb",
    )
