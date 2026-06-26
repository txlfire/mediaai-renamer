"""System settings API tests."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.main import create_app
from app.api import settings as settings_api


class SettingsApiTest(unittest.TestCase):
    """HTTP API behavior for hot system settings."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_get_settings_returns_known_tmdb_values(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            response = client.get("/api/settings")

            self.assertEqual(200, response.status_code)
            values = {item["key"]: item for item in response.json()}
            self.assertEqual("zh-CN", values["tmdb.language"]["value"])
            self.assertEqual("CN", values["tmdb.region"]["value"])
            self.assertEqual(10000, values["tmdb.timeout_ms"]["value"])
            self.assertEqual(False, values["tmdb.enabled"]["value"])
            self.assertTrue(values["tmdb.api_key"]["sensitive"])

    def test_update_settings_persists_values_and_masks_secret(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            response = client.put(
                "/api/settings",
                json={
                    "values": {
                        "tmdb.api_key": "abcdef123456",
                        "tmdb.timeout_ms": "12000",
                        "tmdb.enabled": "true",
                    }
                },
            )

            self.assertEqual(200, response.status_code)
            values = {item["key"]: item for item in response.json()}
            self.assertEqual("********3456", values["tmdb.api_key"]["value"])
            self.assertEqual(12000, values["tmdb.timeout_ms"]["value"])
            self.assertEqual(True, values["tmdb.enabled"]["value"])

    def test_update_settings_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            response = client.put(
                "/api/settings",
                json={"values": {"tmdb.timeout_ms": "999"}},
            )

            self.assertEqual(400, response.status_code)
            values = {item["key"]: item for item in client.get("/api/settings").json()}
            self.assertEqual(10000, values["tmdb.timeout_ms"]["value"])

    def test_tmdb_connection_test_returns_success_message(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            original = settings_api.test_tmdb_connection
            settings_api.test_tmdb_connection = lambda app_settings: {
                "success": True,
                "message": "连接成功！信息有效！",
            }
            try:
                response = client.post("/api/settings/tmdb/test")
            finally:
                settings_api.test_tmdb_connection = original

            self.assertEqual(200, response.status_code)
            self.assertEqual(
                {"success": True, "message": "连接成功！信息有效！"},
                response.json(),
            )

    def test_tmdb_connection_test_returns_chinese_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            original = settings_api.test_tmdb_connection

            def fail_connection(app_settings):
                raise ValueError("TMDB 连接失败：HTTP 401。请检查 API Key、网络或代理配置。")

            settings_api.test_tmdb_connection = fail_connection
            try:
                response = client.post("/api/settings/tmdb/test")
            finally:
                settings_api.test_tmdb_connection = original

            self.assertEqual(400, response.status_code)
            self.assertEqual(
                "TMDB 连接失败：HTTP 401。请检查 API Key、网络或代理配置。",
                response.json()["detail"],
            )


if __name__ == "__main__":
    unittest.main()
