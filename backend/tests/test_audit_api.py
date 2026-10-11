"""审计日志 API 测试。"""

from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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

    def test_media_source_audit_preserves_actor_and_real_target_id(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempDir:
            root = Path(tempDir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post("/api/media-sources", json={"name": "synthetic", "path": str(root / "synthetic")}, headers=headers)
            self.assertEqual(200, created.status_code)
            sourceId = created.json()["id"]
            events = client.get("/api/audit-events?event_type=media_source.change", headers=headers).json()
            self.assertEqual(1, events["total"])
            event = events["items"][0]
            self.assertEqual(str(sourceId), event["targetId"])
            self.assertEqual("admin", event["actorName"])
            self.assertEqual("success", event["result"])
            self.assertEqual("synthetic", event["detail"]["name"])

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

    def test_user_changes_and_session_revocation_rollback_when_audit_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempDir:
            root = Path(tempDir)
            client = self.build_client(root)
            headers = {"Authorization": f"Bearer {self.bootstrap_and_login(client)}"}
            user = client.post("/api/users", headers=headers, json={
                "username": "viewer", "displayName": "viewer", "password": "Viewer123!",
                "permissions": ["audit:read"], "enabled": True,
            }).json()
            client.post("/api/auth/login", json={"username": "viewer", "password": "Viewer123!"})
            def snapshot():
                with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                    return (connection.execute("SELECT * FROM users ORDER BY id").fetchall(),
                            connection.execute("SELECT * FROM user_sessions ORDER BY id").fetchall(),
                            connection.execute("SELECT COUNT(*) FROM audit_events").fetchone())
            requests = (
                lambda: client.post("/api/users", headers=headers, json={
                    "username": "another", "displayName": "another", "password": "Another123!",
                    "permissions": [], "enabled": True,
                }),
                lambda: client.put(f"/api/users/{user['id']}", headers=headers, json={
                    "displayName": "changed", "permissions": [], "enabled": False,
                }),
                lambda: client.post(f"/api/users/{user['id']}/reset-password", headers=headers, json={"password": "Changed123!"}),
            )
            for request in requests:
                with self.subTest(request=request):
                    before = snapshot()
                    with patch("app.service.audit_service.record_audit_event", side_effect=sqlite3.OperationalError("controlled audit failure")):
                        with self.assertRaises(sqlite3.OperationalError):
                            request()
                    self.assertEqual(before, snapshot())
            events = client.get("/api/audit-events?event_type=user.manage", headers=headers).json()
            self.assertEqual(1, events["total"])
            self.assertEqual("admin", events["items"][0]["actorName"])
            self.assertEqual(str(user["id"]), events["items"][0]["targetId"])

    def test_settings_and_template_metadata_rollback_when_audit_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempDir:
            root = Path(tempDir)
            client = self.build_client(root)
            headers = {"Authorization": f"Bearer {self.bootstrap_and_login(client)}"}
            client.put("/api/settings", headers=headers, json={"values": {"tmdb.timeout_ms": 12000}})
            def snapshot():
                with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                    return (connection.execute("SELECT * FROM system_settings ORDER BY key").fetchall(),
                            connection.execute("SELECT * FROM page_test_results ORDER BY page_key").fetchall(),
                            connection.execute("SELECT COUNT(*) FROM audit_events").fetchone())
            before = snapshot()
            with patch("app.service.audit_service.record_audit_event", side_effect=sqlite3.OperationalError("controlled audit failure")):
                with self.assertRaises(sqlite3.OperationalError):
                    client.put("/api/settings", headers=headers, json={"values": {
                        "tmdb.timeout_ms": 18000, "tmdb.api_key": "synthetic-secret", "naming.movie_template": "{title} ({year})",
                    }})
            self.assertEqual(before, snapshot())

    def test_login_password_change_and_logout_rollback_when_audit_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempDir:
            root = Path(tempDir)
            client = self.build_client(root)
            headers = {"Authorization": f"Bearer {self.bootstrap_and_login(client)}"}
            def snapshot():
                with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                    return (connection.execute("SELECT * FROM users ORDER BY id").fetchall(),
                            connection.execute("SELECT * FROM user_sessions ORDER BY id").fetchall(),
                            connection.execute("SELECT COUNT(*) FROM audit_events").fetchone())
            requests = (
                lambda: client.post("/api/auth/login", json={"username": "admin", "password": "ChangeMe123!"}),
                lambda: client.post("/api/auth/change-password", headers=headers, json={
                    "currentPassword": "ChangeMe123!", "newPassword": "Changed123!",
                }),
                lambda: client.post("/api/auth/logout", headers=headers),
            )
            for request in requests:
                with self.subTest(request=request):
                    before = snapshot()
                    with patch("app.service.audit_service.record_audit_event", side_effect=sqlite3.OperationalError("controlled audit failure")):
                        with self.assertRaises(sqlite3.OperationalError):
                            request()
                    self.assertEqual(before, snapshot())
            self.assertEqual(200, client.get("/api/auth/me", headers=headers).status_code)

    def test_bootstrap_user_and_switch_rollback_when_audit_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempDir:
            root = Path(tempDir)
            client = self.build_client(root)
            payload = {"username": "admin", "displayName": "admin", "password": "ChangeMe123!"}
            with patch("app.service.audit_service.record_audit_event", side_effect=sqlite3.OperationalError("controlled audit failure")):
                with self.assertRaises(sqlite3.OperationalError):
                    client.post("/api/auth/bootstrap-admin", json=payload)
            with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM system_settings WHERE key = 'auth.admin_bootstrap_enabled'").fetchone()[0])
            self.assertTrue(client.get("/api/auth/bootstrap-status").json()["available"])
            self.assertEqual(201, client.post("/api/auth/bootstrap-admin", json=payload).status_code)


if __name__ == "__main__":
    unittest.main()
