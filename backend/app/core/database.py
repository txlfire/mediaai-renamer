"""数据库基础设施模块。

负责初始化 SQLite 数据库和基础表结构。业务层不得直接复制数据库初始化逻辑。
"""

from pathlib import Path
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
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
    logger.info("数据库初始化完成: %s", settings.database_path)
    return settings.database_path
