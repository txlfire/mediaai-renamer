"""M10 元数据源 Provider 注册表测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.metadata_provider_registry import build_metadata_provider_registry
from app.service.metadata_provider_service import update_metadata_provider_config


class MetadataProviderRegistryTest(unittest.TestCase):
    """验证真实 Provider 按配置进入注册表。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_enabled_bangumi_uses_real_provider_and_decrypted_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_metadata_provider_config(
                settings,
                "bangumi",
                {
                    "enabled": True,
                    "priority": 25,
                    "base_url": "https://api.bgm.tv",
                    "api_key": "bangumi-token",
                    "timeout_seconds": 12,
                    "max_retries": 2,
                },
            )

            registry = build_metadata_provider_registry(settings)
            item = next(entry for entry in registry if entry.provider == "bangumi")

            self.assertTrue(item.enabled)
            self.assertTrue(item.real_search_available)
            self.assertIsNotNone(item.searcher)
            self.assertEqual("Bangumi", item.searcher.label)
            self.assertEqual(25, item.searcher.priority)
            self.assertEqual("https://api.bgm.tv", item.searcher.base_url)
            self.assertEqual("bangumi-token", item.searcher.access_token)
            self.assertEqual(12, item.searcher.timeout_seconds)
            self.assertEqual(2, item.searcher.max_retries)

    def test_enabled_tvdb_uses_real_provider_and_decrypted_api_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_metadata_provider_config(
                settings,
                "tvdb",
                {
                    "enabled": True,
                    "priority": 35,
                    "base_url": "https://api4.thetvdb.com/v4",
                    "api_key": "tvdb-key",
                    "timeout_seconds": 14,
                    "max_retries": 1,
                },
            )

            registry = build_metadata_provider_registry(settings)
            item = next(entry for entry in registry if entry.provider == "tvdb")

            self.assertTrue(item.enabled)
            self.assertTrue(item.real_search_available)
            self.assertIsNotNone(item.searcher)
            self.assertEqual("TVDB", item.searcher.label)
            self.assertEqual(35, item.searcher.priority)
            self.assertEqual("https://api4.thetvdb.com/v4", item.searcher.base_url)
            self.assertEqual("tvdb-key", item.searcher.api_key)
            self.assertEqual(14, item.searcher.timeout_seconds)
            self.assertEqual(1, item.searcher.max_retries)


if __name__ == "__main__":
    unittest.main()
