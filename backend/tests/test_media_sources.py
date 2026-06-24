"""媒体源服务测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import (
    create_media_source,
    list_media_sources,
)


class MediaSourceServiceTest(unittest.TestCase):
    """媒体源保存和路径校验测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        """构建隔离的测试配置。"""

        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_create_media_source_persists_valid_directory(self):
        """有效目录应保存为媒体源并可查询。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(settings, "电影", media_dir, True)
            sources = list_media_sources(settings)

            self.assertEqual("电影", created.name)
            self.assertEqual(str(media_dir), created.path)
            self.assertTrue(created.enabled)
            self.assertEqual(1, len(sources))

    def test_create_media_source_rejects_missing_directory(self):
        """不存在的路径应返回明确错误。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with self.assertRaises(ValueError):
                create_media_source(settings, "缺失", root / "missing", True)

    def test_database_creates_m1_tables(self):
        """数据库初始化应创建 M1 所需表。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertIn("media_sources", table_names)
            self.assertIn("scan_jobs", table_names)
            self.assertIn("media_files", table_names)


if __name__ == "__main__":
    unittest.main()
