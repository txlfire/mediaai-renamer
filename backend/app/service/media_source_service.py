"""Media source service."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import string

from app.core.config import AppSettings
from app.schema.media import LocalDirectoryEntry, LocalDirectoryListing, MediaSource


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_media_source(row: sqlite3.Row) -> MediaSource:
    return MediaSource(
        id=int(row["id"]),
        name=str(row["name"]),
        path=str(row["path"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _fetch_media_source(connection: sqlite3.Connection, source_id: int) -> MediaSource:
    row = connection.execute(
        "SELECT id, name, path, enabled, created_at, updated_at "
        "FROM media_sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise ValueError("媒体源不存在")
    return _row_to_media_source(row)


def _validate_media_source_path(path: Path) -> Path:
    if not str(path).strip():
        raise ValueError("媒体源路径不能为空")
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        raise ValueError("媒体源路径不存在")
    if not resolved_path.is_dir():
        raise ValueError("媒体源路径必须是目录")
    return resolved_path


def _validate_media_source_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("媒体源名称不能为空")
    return normalized


def _empty_cleanup_summary() -> dict[str, int]:
    return {
        "rename_operation_items": 0,
        "rename_operations": 0,
        "rename_previews": 0,
        "media_files": 0,
        "scan_jobs": 0,
    }


def _merge_cleanup_summary(
    target: dict[str, int],
    source: dict[str, int],
) -> dict[str, int]:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value
    return target


def _delete_related_history(
    connection: sqlite3.Connection,
    source_id: int,
) -> dict[str, int]:
    summary = _empty_cleanup_summary()
    media_file_rows = connection.execute(
        "SELECT id FROM media_files WHERE media_source_id = ?",
        (source_id,),
    ).fetchall()
    media_file_ids = [int(row[0]) for row in media_file_rows]

    preview_ids: list[int] = []
    operation_ids: list[int] = []
    if media_file_ids:
        file_placeholders = ", ".join("?" for _ in media_file_ids)
        preview_rows = connection.execute(
            f"SELECT id FROM rename_previews WHERE media_file_id IN ({file_placeholders})",
            media_file_ids,
        ).fetchall()
        preview_ids = [int(row[0]) for row in preview_rows]

    if preview_ids:
        preview_placeholders = ", ".join("?" for _ in preview_ids)
        operation_rows = connection.execute(
            "SELECT DISTINCT operation_id FROM rename_operation_items "
            f"WHERE rename_preview_id IN ({preview_placeholders})",
            preview_ids,
        ).fetchall()
        operation_ids = [int(row[0]) for row in operation_rows]

        summary["rename_operation_items"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM rename_operation_items "
                f"WHERE rename_preview_id IN ({preview_placeholders})",
                preview_ids,
            ).fetchone()[0]
        )
        connection.execute(
            "DELETE FROM rename_operation_items "
            f"WHERE rename_preview_id IN ({preview_placeholders})",
            preview_ids,
        )

        summary["rename_previews"] = int(
            connection.execute(
                f"SELECT COUNT(*) FROM rename_previews WHERE id IN ({preview_placeholders})",
                preview_ids,
            ).fetchone()[0]
        )
        connection.execute(
            f"DELETE FROM rename_previews WHERE id IN ({preview_placeholders})",
            preview_ids,
        )

    if operation_ids:
        operation_placeholders = ", ".join("?" for _ in operation_ids)
        removable_rows = connection.execute(
            "SELECT ro.id FROM rename_operations ro "
            f"WHERE ro.id IN ({operation_placeholders}) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM rename_operation_items roi WHERE roi.operation_id = ro.id"
            ")",
            operation_ids,
        ).fetchall()
        removable_ids = [int(row[0]) for row in removable_rows]
        if removable_ids:
            removable_placeholders = ", ".join("?" for _ in removable_ids)
            summary["rename_operations"] = len(removable_ids)
            connection.execute(
                f"DELETE FROM rename_operations WHERE id IN ({removable_placeholders})",
                removable_ids,
            )

    summary["media_files"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM media_files WHERE media_source_id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    connection.execute("DELETE FROM media_files WHERE media_source_id = ?", (source_id,))

    summary["scan_jobs"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM scan_jobs WHERE media_source_id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    connection.execute("DELETE FROM scan_jobs WHERE media_source_id = ?", (source_id,))

    return summary


def _windows_drive_entries() -> list[LocalDirectoryEntry]:
    entries: list[LocalDirectoryEntry] = []
    for letter in string.ascii_uppercase:
        drive_path = Path(f"{letter}:\\")
        if drive_path.exists():
            entries.append(
                LocalDirectoryEntry(name=f"{letter}:", path=str(drive_path), is_directory=True)
            )
    return entries


def list_local_directories(path: str | None = None) -> LocalDirectoryListing:
    if path is None or not path.strip():
        return LocalDirectoryListing(
            current_path=None,
            parent_path=None,
            entries=_windows_drive_entries(),
        )

    current_path = Path(path).expanduser().resolve()
    if not current_path.exists():
        raise ValueError("目录不存在")
    if not current_path.is_dir():
        raise ValueError("路径不是目录")

    entries = [
        LocalDirectoryEntry(name=item.name, path=str(item), is_directory=True)
        for item in current_path.iterdir()
        if item.is_dir()
    ]
    entries.sort(key=lambda item: item.name.lower())

    parent_path = str(current_path.parent) if current_path.parent != current_path else None
    return LocalDirectoryListing(
        current_path=str(current_path),
        parent_path=parent_path,
        entries=entries,
    )


def create_media_source(
    settings: AppSettings, name: str, path: str | Path, enabled: bool = True
) -> MediaSource:
    source_path = _validate_media_source_path(Path(path))
    source_name = _validate_media_source_name(name)
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(
                "INSERT INTO media_sources "
                "(name, path, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, str(source_path), int(enabled), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("媒体源路径已存在") from exc
        row = connection.execute(
            "SELECT id, name, path, enabled, created_at, updated_at "
            "FROM media_sources WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        connection.commit()
    return _row_to_media_source(row)


def list_media_sources(settings: AppSettings) -> list[MediaSource]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, path, enabled, created_at, updated_at "
            "FROM media_sources ORDER BY id"
        ).fetchall()
    return [_row_to_media_source(row) for row in rows]


def get_media_source(settings: AppSettings, source_id: int) -> MediaSource:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return _fetch_media_source(connection, source_id)


def update_media_source(
    settings: AppSettings,
    source_id: int,
    name: str,
    path: str | Path,
    enabled: bool,
    clear_history_on_path_change: bool = False,
) -> dict[str, object]:
    source_name = _validate_media_source_name(name)
    source_path = _validate_media_source_path(Path(path))
    cleanup_summary = _empty_cleanup_summary()
    now = _utc_now()

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        current = _fetch_media_source(connection, source_id)
        path_changed = str(source_path) != current.path
        if path_changed and not clear_history_on_path_change:
            raise ValueError("修改路径将清空历史数据，请确认后重试")
        if path_changed:
            cleanup_summary = _delete_related_history(connection, source_id)
        try:
            connection.execute(
                "UPDATE media_sources SET name = ?, path = ?, enabled = ?, updated_at = ? "
                "WHERE id = ?",
                (source_name, str(source_path), int(enabled), now, source_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("媒体源路径已存在") from exc
        source = _fetch_media_source(connection, source_id)
        connection.commit()

    return {"source": source, "cleanup_summary": cleanup_summary}


def set_media_source_enabled(
    settings: AppSettings,
    source_id: int,
    enabled: bool,
) -> MediaSource:
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _fetch_media_source(connection, source_id)
        connection.execute(
            "UPDATE media_sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), now, source_id),
        )
        source = _fetch_media_source(connection, source_id)
        connection.commit()
    return source


def delete_media_source(settings: AppSettings, source_id: int) -> dict[str, object]:
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        _fetch_media_source(connection, source_id)
        cleanup_summary = _delete_related_history(connection, source_id)
        connection.execute("DELETE FROM media_sources WHERE id = ?", (source_id,))
        connection.commit()
    return {"deleted_ids": [source_id], "cleanup_summary": cleanup_summary}


def bulk_delete_media_sources(
    settings: AppSettings,
    source_ids: list[int],
) -> dict[str, object]:
    normalized_ids = list(dict.fromkeys(source_ids))
    if not normalized_ids:
        raise ValueError("请选择要删除的媒体源")

    cleanup_summary = _empty_cleanup_summary()
    deleted_ids: list[int] = []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for source_id in normalized_ids:
            _fetch_media_source(connection, source_id)
            _merge_cleanup_summary(
                cleanup_summary,
                _delete_related_history(connection, source_id),
            )
            connection.execute("DELETE FROM media_sources WHERE id = ?", (source_id,))
            deleted_ids.append(source_id)
        connection.commit()
    return {"deleted_ids": deleted_ids, "cleanup_summary": cleanup_summary}
