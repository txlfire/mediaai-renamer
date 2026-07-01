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
            self.assertEqual(15000, values["tmdb.timeout_ms"]["value"])
            self.assertEqual(False, values["tmdb.enabled"]["value"])
            self.assertTrue(values["tmdb.api_key"]["sensitive"])
            self.assertEqual(False, values["imdb.enabled"]["value"])
            self.assertEqual("tmdb_first", values["imdb.priority"]["value"])
            self.assertEqual(10000, values["imdb.timeout_ms"]["value"])
            self.assertEqual(True, values["privacy.default_sensitive_words_enabled"]["value"])
            self.assertIn("暴力", values["privacy.default_sensitive_words"]["value"])
            self.assertEqual([], values["privacy.custom_sensitive_words"]["value"])
            self.assertEqual(False, values["ai.enabled"]["value"])
            self.assertEqual("deepseek", values["ai.provider"]["value"])
            self.assertEqual("deepseek-chat", values["ai.model"]["value"])
            self.assertTrue(values["ai.api_key"]["sensitive"])
            self.assertEqual("https://api.deepseek.com", values["ai.base_url"]["value"])
            self.assertEqual(30000, values["ai.timeout_ms"]["value"])
            self.assertEqual(2, values["ai.max_retries"]["value"])

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

    def test_update_ai_settings_persists_values_and_masks_secret(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            response = client.put(
                "/api/settings",
                json={
                    "values": {
                        "ai.enabled": "true",
                        "ai.provider": "deepseek",
                        "ai.model": "deepseek-reasoner",
                        "ai.api_key": "sk-ai123456",
                        "ai.base_url": "https://api.deepseek.com/v1",
                        "ai.timeout_ms": "45000",
                        "ai.max_retries": "3",
                    }
                },
            )

            self.assertEqual(200, response.status_code)
            values = {item["key"]: item for item in response.json()}
            self.assertEqual(True, values["ai.enabled"]["value"])
            self.assertEqual("deepseek-reasoner", values["ai.model"]["value"])
            self.assertEqual("********3456", values["ai.api_key"]["value"])
            self.assertEqual("https://api.deepseek.com/v1", values["ai.base_url"]["value"])
            self.assertEqual(45000, values["ai.timeout_ms"]["value"])
            self.assertEqual(3, values["ai.max_retries"]["value"])

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
            self.assertEqual(15000, values["tmdb.timeout_ms"]["value"])

    def test_tmdb_connection_test_returns_success_message(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            original = settings_api.test_tmdb_connection
            settings_api.test_tmdb_connection = lambda app_settings: {
                "v4": {"status": "success", "message": "V4 ok"},
                "v3": {"status": "skipped", "message": "V3 skipped"},
                "effective": "v4",
            }
            try:
                response = client.post("/api/settings/tmdb/test")
            finally:
                settings_api.test_tmdb_connection = original

            self.assertEqual(200, response.status_code)
            self.assertEqual("success", response.json()["v4"]["status"])
            self.assertEqual("v4", response.json()["effective"])

    def test_tmdb_connection_test_returns_chinese_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            original = settings_api.test_tmdb_connection

            def fail_connection(app_settings):
                return {
                    "v4": {"status": "failed", "message": "V4 连接失败：HTTP 401"},
                    "v3": {"status": "skipped", "message": "未配置 V3 API 密钥"},
                    "effective": "none",
                }

            settings_api.test_tmdb_connection = fail_connection
            try:
                response = client.post("/api/settings/tmdb/test")
            finally:
                settings_api.test_tmdb_connection = original

            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual("failed", data["v4"]["status"])
            self.assertEqual("none", data["effective"])

    def test_imdb_connection_result_can_be_saved_and_loaded(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            save_response = client.post(
                "/api/settings/imdb/test-result",
                json={
                    "status": "success",
                    "message": "IMDb连接成功",
                    "response_ms": 88,
                },
            )
            history_response = client.get("/api/settings/imdb/test-result")

            self.assertEqual(200, save_response.status_code)
            self.assertEqual(200, history_response.status_code)
            history = history_response.json()
            self.assertEqual("success", history["result"]["connection_status"])
            self.assertEqual(88, history["result"]["response_time"])
            self.assertTrue(history["matches_current"])

            client.put("/api/settings", json={"values": {"imdb.timeout_ms": "15000"}})
            invalidated = client.get("/api/settings/imdb/test-result").json()
            self.assertFalse(invalidated["result"]["is_valid"])
            self.assertFalse(invalidated["matches_current"])



if __name__ == "__main__":
    unittest.main()
