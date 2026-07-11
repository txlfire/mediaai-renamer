"""多站点元数据源配置服务测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.bangumi_client import BangumiClient
from app.service.metadata_provider_service import (
    test_metadata_provider_config,
    update_metadata_provider_config,
)


class MetadataProviderServiceTest(unittest.TestCase):
    """验证真实 Provider 连接测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_bangumi_connection_test_uses_saved_config_and_decrypted_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_metadata_provider_config(
                settings,
                "bangumi",
                {
                    "enabled": True,
                    "api_key": "bangumi-token",
                    "timeout_seconds": 12,
                    "max_retries": 2,
                },
            )
            captured: dict[str, object] = {}

            def fake_test_connection(client):
                captured["base_url"] = client.base_url
                captured["access_token"] = client.access_token
                captured["timeout_seconds"] = client.timeout_seconds
                captured["max_retries"] = client.max_retries
                return True

            with patch.object(BangumiClient, "test_connection", fake_test_connection, create=True):
                result = test_metadata_provider_config(settings, "bangumi")

            self.assertEqual("success", result["status"])
            self.assertEqual("Bangumi 连接成功", result["message"])
            self.assertIsInstance(result["response_ms"], int)
            self.assertEqual("https://api.bgm.tv", captured["base_url"])
            self.assertEqual("bangumi-token", captured["access_token"])
            self.assertEqual(12, captured["timeout_seconds"])
            self.assertEqual(2, captured["max_retries"])

    def test_bangumi_connection_test_returns_failure_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            with patch.object(
                BangumiClient,
                "test_connection",
                side_effect=RuntimeError("Bangumi 鉴权失败，请检查 Access Token"),
                create=True,
            ):
                result = test_metadata_provider_config(settings, "bangumi")

            self.assertEqual("failed", result["status"])
            self.assertIn("Bangumi 鉴权失败", result["message"])
            self.assertIsInstance(result["response_ms"], int)

    def test_bangumi_rejects_non_official_base_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            with self.assertRaisesRegex(ValueError, "Bangumi Base URL 仅支持官方地址"):
                update_metadata_provider_config(
                    settings,
                    "bangumi",
                    {
                        "base_url": "https://metadata.example.test/bangumi",
                        "api_key": "must-not-leak",
                    },
                )


if __name__ == "__main__":
    unittest.main()
