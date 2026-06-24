"""数据库基础设施模块。

负责初始化 SQLite 数据库和基础表结构。业务层不得直接复制数据库初始化逻辑。
"""

from pathlib import Path
from contextlib import closing
import sqlite3

from app.core.config import AppSettings
from app.core.logger import get_logger

logger = get_logger(__name__)


def ensure_database(settings: AppSettings) -> Path:
    """确保 SQLite 数据库和基础元数据表存在。

    Args:
        settings: 应用运行配置。

    Returns:
        SQLite 数据库文件路径。
    """

    logger.info("开始检查数据库目录和基础表")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS media_sources "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, "
            "path TEXT NOT NULL UNIQUE, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scan_jobs "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_source_id INTEGER NOT NULL, "
            "status TEXT NOT NULL, "
            "batch_size INTEGER NOT NULL, "
            "batch_interval_seconds REAL NOT NULL, "
            "scanned_count INTEGER NOT NULL DEFAULT 0, "
            "video_count INTEGER NOT NULL DEFAULT 0, "
            "warning_count INTEGER NOT NULL DEFAULT 0, "
            "error_message TEXT, "
            "started_at TEXT, "
            "ended_at TEXT, "
            "created_at TEXT NOT NULL, "
            "FOREIGN KEY(media_source_id) REFERENCES media_sources(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS media_files "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_source_id INTEGER NOT NULL, "
            "scan_job_id INTEGER NOT NULL, "
            "file_path TEXT NOT NULL, "
            "file_name TEXT NOT NULL, "
            "extension TEXT NOT NULL, "
            "file_size INTEGER NOT NULL, "
            "modified_at TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "FOREIGN KEY(media_source_id) REFERENCES media_sources(id), "
            "FOREIGN KEY(scan_job_id) REFERENCES scan_jobs(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_previews "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "media_file_id INTEGER NOT NULL UNIQUE, "
            "media_type TEXT NOT NULL, "
            "parsed_title TEXT NOT NULL, "
            "parsed_year INTEGER, "
            "season INTEGER, "
            "episode INTEGER, "
            "original_extension TEXT NOT NULL, "
            "suggested_name TEXT NOT NULL, "
            "edited_name TEXT, "
            "status TEXT NOT NULL, "
            "message TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "FOREIGN KEY(media_file_id) REFERENCES media_files(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_operations "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "status TEXT NOT NULL, "
            "mode TEXT NOT NULL, "
            "total_count INTEGER NOT NULL DEFAULT 0, "
            "ready_count INTEGER NOT NULL DEFAULT 0, "
            "conflict_count INTEGER NOT NULL DEFAULT 0, "
            "renamed_count INTEGER NOT NULL DEFAULT 0, "
            "failed_count INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rename_operation_items "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "operation_id INTEGER NOT NULL, "
            "rename_preview_id INTEGER NOT NULL, "
            "source_path TEXT NOT NULL, "
            "target_path TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "message TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "FOREIGN KEY(operation_id) REFERENCES rename_operations(id), "
            "FOREIGN KEY(rename_preview_id) REFERENCES rename_previews(id))"
        )
        connection.commit()
    logger.info("数据库初始化完成: %s", settings.database_path)
    return settings.database_path
