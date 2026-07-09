"""审计日志 API 测试。"""

from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.main import create_app


class AuditApiTest(unittest.TestCase):
    """M9 审计日志行为。"""

    def build_client(self, root: Path) -> TestClient:
        settings = AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )
        return TestClient(create_app(settings))

    def bootstrap_and_login(self, client: TestClient) -> str:
        client.post(
            "/api/auth/bootstrap-admin",
            json={
                "username": "admin",
                "displayName": "系统管理员",
                "password": "ChangeMe123!",
            },
        )
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "ChangeMe123!"},
        )
        return str(response.json()["accessToken"])

    def test_login_success_and_failure_write_audit_events(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))
            token = self.bootstrap_and_login(client)
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )

            response = client.get(
                "/api/audit-events?event_type=auth.login&page_size=10",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertGreaterEqual(payload["total"], 2)
            results = {item["result"] for item in payload["items"]}
            self.assertIn("success", results)
            self.assertIn("failed", results)
            self.assertTrue(all(item["eventType"] == "auth.login" for item in payload["items"]))

    def test_audit_list_requires_audit_permission(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                connection.execute(
                    "UPDATE users SET permissions_json = ? WHERE username = 'admin'",
                    (json.dumps(["settings:write"], ensure_ascii=False),),
                )
                connection.commit()

            response = client.get(
                "/api/audit-events",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(403, response.status_code)

    def test_user_and_settings_changes_write_sanitized_audit_events(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))
            token = self.bootstrap_and_login(client)
            headers = {"Authorization": f"Bearer {token}"}

            create_response = client.post(
                "/api/users",
                json={
                    "username": "viewer",
                    "displayName": "只读用户",
                    "password": "Viewer123!",
                    "permissions": ["audit:read"],
                    "enabled": True,
                },
                headers=headers,
            )
            settings_response = client.put(
                "/api/settings",
                json={
                    "values": {
                        "tmdb.api_key": "plain-api-key",
                        "tmdb.timeout_ms": 15000,
                    }
                },
                headers=headers,
            )
            audit_response = client.get(
                "/api/audit-events?page_size=20",
                headers=headers,
            )

            self.assertEqual(201, create_response.status_code)
            self.assertEqual(200, settings_response.status_code)
            self.assertEqual(200, audit_response.status_code)
            items = audit_response.json()["items"]
            self.assertTrue(any(item["action"] == "create_user" for item in items))
            settings_events = [
                item for item in items if item["action"] == "update_settings"
            ]
            self.assertTrue(settings_events)
            settings_detail = settings_events[0]["detail"]
            self.assertIn("tmdb.api_key", settings_detail["keys"])
            self.assertEqual("******", settings_detail["values"]["tmdb.api_key"])
            self.assertNotIn("plain-api-key", json.dumps(settings_detail, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
