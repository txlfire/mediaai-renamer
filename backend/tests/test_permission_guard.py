"""用户权限守卫测试。"""

from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.main import create_app


class PermissionGuardTest(unittest.TestCase):
    """敏感写接口的用户权限守卫。"""

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

    def update_permissions(self, root: Path, permissions: list[str]) -> None:
        with closing(sqlite3.connect(root / "mediaai.sqlite3")) as connection:
            connection.execute(
                "UPDATE users SET permissions_json = ? WHERE username = 'admin'",
                (json.dumps(permissions),),
            )
            connection.commit()

    def test_settings_write_stays_compatible_before_user_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            client = self.build_client(Path(temp_dir))

            response = client.put(
                "/api/settings",
                json={"values": {"tmdb.timeout_ms": "12000"}},
            )

            self.assertEqual(200, response.status_code)

    def test_settings_write_requires_matching_user_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)

            unauthenticated_response = client.put(
                "/api/settings",
                json={"values": {"tmdb.timeout_ms": "12000"}},
            )
            self.update_permissions(root, [])
            forbidden_response = client.put(
                "/api/settings",
                json={"values": {"tmdb.timeout_ms": "12000"}},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["settings:write"])
            allowed_response = client.put(
                "/api/settings",
                json={"values": {"tmdb.timeout_ms": "12000"}},
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertIn("权限不足", forbidden_response.json()["detail"])
            self.assertEqual(200, allowed_response.status_code)

    def test_media_source_write_requires_source_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            payload = {
                "name": "本地源",
                "path": str(root / "media"),
                "enabled": True,
            }

            unauthenticated_response = client.post("/api/media-sources", json=payload)
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.post(
                "/api/media-sources",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["source:write"])
            allowed_response = client.post(
                "/api/media-sources",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(200, allowed_response.status_code)

    def test_scan_creation_requires_scan_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            payload = {"media_source_id": 999, "scan_mode": "full"}

            unauthenticated_response = client.post("/api/scan-jobs", json=payload)
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.post(
                "/api/scan-jobs",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["scan:run"])
            allowed_response = client.post(
                "/api/scan-jobs",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(400, allowed_response.status_code)

    def test_metadata_submission_requires_metadata_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)

            unauthenticated_response = client.post("/api/rename-previews/999/metadata-match")
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.post(
                "/api/rename-previews/999/metadata-match",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["metadata:submit"])
            allowed_response = client.post(
                "/api/rename-previews/999/metadata-match",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(400, allowed_response.status_code)

    def test_rename_execute_requires_rename_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)

            unauthenticated_response = client.post("/api/rename-operations/999/execute")
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.post(
                "/api/rename-operations/999/execute",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["rename:execute"])
            allowed_response = client.post(
                "/api/rename-operations/999/execute",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(400, allowed_response.status_code)

    def test_rollback_execute_requires_rollback_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)

            unauthenticated_response = client.post("/api/rename-rollback-plans/999/execute")
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.post(
                "/api/rename-rollback-plans/999/execute",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["rollback:execute"])
            allowed_response = client.post(
                "/api/rename-rollback-plans/999/execute",
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(400, allowed_response.status_code)

    def test_external_submission_override_requires_metadata_permission_after_bootstrap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            client = self.build_client(root)
            token = self.bootstrap_and_login(client)
            payload = {
                "status": "ignored",
                "user_decision": "不进行匹配",
            }

            unauthenticated_response = client.patch("/api/external-submission-blocks/999", json=payload)
            self.update_permissions(root, ["settings:write"])
            forbidden_response = client.patch(
                "/api/external-submission-blocks/999",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.update_permissions(root, ["metadata:submit"])
            allowed_response = client.patch(
                "/api/external-submission-blocks/999",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

            self.assertEqual(401, unauthenticated_response.status_code)
            self.assertEqual(403, forbidden_response.status_code)
            self.assertEqual(404, allowed_response.status_code)


if __name__ == "__main__":
    unittest.main()
