"""认证 API 测试。"""

from datetime import datetime, timedelta, timezone
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, AuthSettings, LoggingSettings
from app.main import create_app


class AuthApiTest(unittest.TestCase):
    """M9 本地认证 API 行为。"""

    def build_client(self, root: Path) -> TestClient:
        settings = AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )
        return TestClient(create_app(settings))

    def build_client_with_auth(self, root: Path, auth: AuthSettings) -> TestClient:
        settings = AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            auth=auth,
        )
        return TestClient(create_app(settings))

    def test_bootstrap_admin_login_me_and_logout(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)

            bootstrap_response = client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "admin",
                    "displayName": "系统管理员",
                    "password": "ChangeMe123!",
                },
            )

            self.assertEqual(201, bootstrap_response.status_code)
            bootstrap_payload = bootstrap_response.json()
            self.assertEqual("admin", bootstrap_payload["username"])
            self.assertEqual("系统管理员", bootstrap_payload["displayName"])
            self.assertNotIn("role", bootstrap_payload)
            self.assertIn("settings:write", bootstrap_payload["permissions"])
            self.assertFalse(bootstrap_payload["mustChangePassword"])
            self.assertNotIn("passwordHash", bootstrap_payload)

            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "ChangeMe123!"},
            )

            self.assertEqual(200, login_response.status_code)
            login_payload = login_response.json()
            self.assertEqual("bearer", login_payload["tokenType"])
            self.assertTrue(login_payload["accessToken"])
            self.assertEqual("admin", login_payload["user"]["username"])
            self.assertIn("rename:execute", login_payload["user"]["permissions"])
            self.assertFalse(login_payload["user"]["mustChangePassword"])

            headers = {"Authorization": f"Bearer {login_payload['accessToken']}"}
            me_response = client.get("/api/auth/me", headers=headers)

            self.assertEqual(200, me_response.status_code)
            self.assertEqual("admin", me_response.json()["username"])

            logout_response = client.post("/api/auth/logout", headers=headers)

            self.assertEqual(204, logout_response.status_code)
            expired_response = client.get("/api/auth/me", headers=headers)
            self.assertEqual(401, expired_response.status_code)

            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                password_hash = connection.execute(
                    "SELECT password_hash FROM users WHERE username = 'admin'"
                ).fetchone()[0]
            self.assertNotEqual("ChangeMe123!", password_hash)
            self.assertTrue(password_hash.startswith("pbkdf2_sha256$"))

    def test_default_admin_is_created_with_default_password_when_enabled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client_with_auth(
                root,
                AuthSettings(default_admin_enabled=True),
            )

            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )
            bootstrap_response = client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "other",
                    "displayName": "其他管理员",
                    "password": "ChangeMe123!",
                },
            )

            self.assertEqual(200, login_response.status_code)
            self.assertEqual("admin", login_response.json()["user"]["username"])
            self.assertTrue(login_response.json()["user"]["mustChangePassword"])
            self.assertEqual(409, bootstrap_response.status_code)

    def test_change_password_clears_default_password_prompt(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client_with_auth(
                Path(temp_dir),
                AuthSettings(default_admin_enabled=True),
            )
            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )
            token = login_response.json()["accessToken"]

            change_response = client.post(
                "/api/auth/change-password",
                json={"currentPassword": "123456", "newPassword": "ChangeMe123!"},
                headers={"Authorization": f"Bearer {token}"},
            )
            old_login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )
            new_login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "ChangeMe123!"},
            )

            self.assertEqual(200, change_response.status_code)
            self.assertFalse(change_response.json()["mustChangePassword"])
            self.assertEqual(401, old_login_response.status_code)
            self.assertEqual(200, new_login_response.status_code)
            self.assertFalse(new_login_response.json()["user"]["mustChangePassword"])

    def test_change_password_rejects_default_password(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client_with_auth(
                Path(temp_dir),
                AuthSettings(default_admin_enabled=True),
            )
            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )
            token = login_response.json()["accessToken"]

            response = client.post(
                "/api/auth/change-password",
                json={"currentPassword": "123456", "newPassword": "123456"},
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(400, response.status_code)
            self.assertIn("新密码不能继续使用默认密码", response.json()["detail"])

    def test_reset_admin_password_is_hidden_by_config_switch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            disabled_client = self.build_client_with_auth(
                Path(temp_dir) / "disabled",
                AuthSettings(default_admin_enabled=True, admin_password_reset_enabled=False),
            )
            disabled_response = disabled_client.post("/api/auth/reset-admin-password")

            self.assertEqual(403, disabled_response.status_code)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client_with_auth(
                Path(temp_dir),
                AuthSettings(default_admin_enabled=True, admin_password_reset_enabled=True),
            )
            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )
            token = login_response.json()["accessToken"]
            client.post(
                "/api/auth/change-password",
                json={"currentPassword": "123456", "newPassword": "ChangeMe123!"},
                headers={"Authorization": f"Bearer {token}"},
            )

            reset_response = client.post("/api/auth/reset-admin-password")
            reset_login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "123456"},
            )

            self.assertEqual(200, reset_response.status_code)
            self.assertTrue(reset_response.json()["mustChangePassword"])
            self.assertEqual(200, reset_login_response.status_code)
            self.assertTrue(reset_login_response.json()["user"]["mustChangePassword"])

    def test_bootstrap_admin_is_allowed_only_when_user_table_is_empty(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))

            first_response = client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "admin",
                    "displayName": "系统管理员",
                    "password": "ChangeMe123!",
                },
            )
            second_response = client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "other",
                    "displayName": "其他管理员",
                    "password": "ChangeMe123!",
                },
            )

            self.assertEqual(201, first_response.status_code)
            self.assertEqual(409, second_response.status_code)
            self.assertIn("已存在用户", second_response.json()["detail"])

    def test_login_rejects_wrong_password(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))
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
                json={"username": "admin", "password": "wrong-password"},
            )

            self.assertEqual(401, response.status_code)
            self.assertIn("用户名或密码错误", response.json()["detail"])

    def test_login_rejects_disabled_user(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "admin",
                    "displayName": "系统管理员",
                    "password": "ChangeMe123!",
                },
            )
            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                connection.execute("UPDATE users SET enabled = 0 WHERE username = 'admin'")
                connection.commit()

            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "ChangeMe123!"},
            )

            self.assertEqual(401, response.status_code)
            self.assertIn("用户名或密码错误", response.json()["detail"])

    def test_expired_session_cannot_read_current_user(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            client.post(
                "/api/auth/bootstrap-admin",
                json={
                    "username": "admin",
                    "displayName": "系统管理员",
                    "password": "ChangeMe123!",
                },
            )
            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "ChangeMe123!"},
            )
            token = login_response.json()["accessToken"]
            past_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                connection.execute("UPDATE user_sessions SET expires_at = ?", (past_time,))
                connection.commit()

            response = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, response.status_code)

    def test_me_requires_valid_bearer_token(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))

            response = client.get("/api/auth/me")
            invalid_response = client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer invalid-token"},
            )

            self.assertEqual(401, response.status_code)
            self.assertEqual(401, invalid_response.status_code)


if __name__ == "__main__":
    unittest.main()
