"""用户维护 API 测试。"""

from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.main import create_app


class UsersApiTest(unittest.TestCase):
    """本地用户维护接口行为。"""

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

    def test_user_management_requires_settings_permission(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                connection.execute(
                    "UPDATE users SET permissions_json = ? WHERE username = 'admin'",
                    (json.dumps([], ensure_ascii=False),),
                )
                connection.commit()

            response = client.get(
                "/api/users",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(403, response.status_code)

    def test_create_update_and_reset_user(self):
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
                    "permissions": ["source:write"],
                    "enabled": True,
                },
                headers=headers,
            )
            list_response = client.get("/api/users", headers=headers)
            user_id = create_response.json()["id"]
            update_response = client.put(
                f"/api/users/{user_id}",
                json={
                    "displayName": "媒体源维护",
                    "permissions": ["source:write", "scan:run"],
                    "enabled": False,
                },
                headers=headers,
            )
            reset_response = client.post(
                f"/api/users/{user_id}/reset-password",
                json={"password": "Viewer456!"},
                headers=headers,
            )
            login_response = client.post(
                "/api/auth/login",
                json={"username": "viewer", "password": "Viewer456!"},
            )

            self.assertEqual(201, create_response.status_code)
            self.assertEqual("viewer", create_response.json()["username"])
            self.assertTrue(create_response.json()["mustChangePassword"])
            self.assertNotIn("role", create_response.json())
            self.assertEqual(200, list_response.status_code)
            self.assertIn("settings:write", list_response.json()["permissions"])
            self.assertGreaterEqual(len(list_response.json()["items"]), 2)
            self.assertEqual(200, update_response.status_code)
            self.assertFalse(update_response.json()["enabled"])
            self.assertEqual(["source:write", "scan:run"], update_response.json()["permissions"])
            self.assertEqual(200, reset_response.status_code)
            self.assertTrue(reset_response.json()["mustChangePassword"])
            self.assertEqual(401, login_response.status_code)

            enable_response = client.put(
                f"/api/users/{user_id}",
                json={
                    "displayName": "媒体源维护",
                    "permissions": ["source:write", "scan:run"],
                    "enabled": True,
                },
                headers=headers,
            )
            login_enabled_response = client.post(
                "/api/auth/login",
                json={"username": "viewer", "password": "Viewer456!"},
            )

            self.assertEqual(200, enable_response.status_code)
            self.assertEqual(200, login_enabled_response.status_code)
            self.assertTrue(login_enabled_response.json()["user"]["mustChangePassword"])

    def test_update_user_keeps_one_settings_writer(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))
            token = self.bootstrap_and_login(client)
            headers = {"Authorization": f"Bearer {token}"}
            users_response = client.get("/api/users", headers=headers)
            admin_id = users_response.json()["items"][0]["id"]

            response = client.put(
                f"/api/users/{admin_id}",
                json={
                    "displayName": "系统管理员",
                    "permissions": [],
                    "enabled": True,
                },
                headers=headers,
            )

            self.assertEqual(400, response.status_code)
            self.assertIn("至少需要保留", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
