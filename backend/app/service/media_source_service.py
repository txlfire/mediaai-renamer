"""媒体源服务。

负责媒体目录的保存、查询和基础路径校验。
"""

from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
import sqlite3

from app.core.config import AppSettings
from app.schema.media import MediaSource


def _utc_now() -> str:
    """返回 ISO 8601 UTC 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


def _row_to_media_source(row: sqlite3.Row) -> MediaSource:
    """将数据库行转换为媒体源模型。"""

    return MediaSource(
        id=int(row["id"]),
        name=str(row["name"]),
        path=str(row["path"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _validate_media_source_path(path: Path) -> Path:
    """校验媒体源路径并返回解析后的绝对路径。"""

    if not str(path).strip():
        raise ValueError("媒体源路径不能为空")
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        raise ValueError("媒体源路径不存在")
    if not resolved_path.is_dir():
        raise ValueError("媒体源路径必须是目录")
    return resolved_path


def create_media_source(
    settings: AppSettings, name: str, path: str | Path, enabled: bool = True
) -> MediaSource:
    """创建媒体源目录。

    Args:
        settings: 应用运行配置。
        name: 媒体源名称。
        path: 本地或已挂载目录路径。
        enabled: 是否启用。

    Returns:
        已保存的媒体源。

    Raises:
        ValueError: 路径为空、不存在或不是目录。
    """

    source_path = _validate_media_source_path(Path(path))
    now = _utc_now()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            "INSERT INTO media_sources "
            "(name, path, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name.strip(), str(source_path), int(enabled), now, now),
        )
        row = connection.execute(
            "SELECT id, name, path, enabled, created_at, updated_at "
            "FROM media_sources WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        connection.commit()
    return _row_to_media_source(row)


def list_media_sources(settings: AppSettings) -> list[MediaSource]:
    """查询全部媒体源目录。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, path, enabled, created_at, updated_at "
            "FROM media_sources ORDER BY id"
        ).fetchall()
    return [_row_to_media_source(row) for row in rows]


def get_media_source(settings: AppSettings, source_id: int) -> MediaSource:
    """按 ID 查询媒体源目录。"""

    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, name, path, enabled, created_at, updated_at "
            "FROM media_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
    if row is None:
        raise ValueError("媒体源不存在")
    return _row_to_media_source(row)
