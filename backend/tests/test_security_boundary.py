"""审查 A1 回归：默认拒绝、写权限、会话撤销及恢复授权。"""

from contextlib import closing
import inspect
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.admin_recovery import recoverAdminPassword
from app.core.config import AppSettings, AuthSettings, LoggingSettings
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.auth_service import bootstrap_admin, change_password, login


class SecurityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempDir.name)
        self.settings = AppSettings(
            data_dir=self.root, database_path=self.root / "review.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            auth=AuthSettings(admin_password_reset_enabled=True),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        shutdown_logging()
        self.tempDir.cleanup()

    def authenticate(self):
        self.user = bootstrap_admin(self.settings, "admin", "管理员", "TemporaryReview123!")
        session = login(self.settings, "admin", "TemporaryReview123!")
        self.headers = {"Authorization": f"Bearer {session.access_token}"}
        return session

    def routePath(self, route):
        values = {name: {"channel": "v4", "provider": "bangumi", "task_type": "scan_job"}.get(name, "1")
                  for name in route.param_convertors}
        return route.path.format(**values)

    def privateRoutes(self):
        return [route for route in self.app.routes if isinstance(route, APIRoute)
                and route.path.startswith("/api/")
                and not route.path.startswith("/api/auth/") and route.path != "/api/health"]

    def setPermissions(self, permissions):
        import json
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE users SET permissions_json = ? WHERE id = ?",
                               (json.dumps(list(permissions)), self.user.id))
            connection.commit()

    def test_every_private_route_rejects_anonymous_before_and_after_bootstrap(self):
        for initialized in (False, True):
            if initialized:
                self.authenticate()
            for route in self.privateRoutes():
                for method in route.methods:
                    with self.subTest(initialized=initialized, method=method, path=route.path):
                        response = self.client.request(method, self.routePath(route), json={})
                        self.assertEqual(401, response.status_code)

    def test_every_mutating_route_has_permission_dependency(self):
        for route in self.privateRoutes():
            if not route.methods.intersection({"POST", "PUT", "PATCH", "DELETE"}):
                continue
            if route.path == "/api/remote-operations/{item_id}/recover":
                continue  # 恢复权限依赖持久化的操作类型，单独做四组合矩阵。
            permissionDependencies = [dependency for dependency in route.dependant.dependencies
                                      if dependency.call and "permission" in inspect.getclosurevars(dependency.call).nonlocals]
            with self.subTest(path=route.path):
                self.assertTrue(permissionDependencies)

    def test_mutating_permissions_reject_unprivileged_authenticated_user(self):
        self.authenticate()
        self.setPermissions([])
        for route in self.privateRoutes():
            if route.path == "/api/remote-operations/{item_id}/recover":
                continue
            for method in route.methods.intersection({"POST", "PUT", "PATCH", "DELETE"}):
                with self.subTest(method=method, path=route.path):
                    response = self.client.request(method, self.routePath(route), json={}, headers=self.headers)
                    self.assertEqual(403, response.status_code)

    def test_read_only_user_can_read_lists_but_not_logs(self):
        self.authenticate()
        self.setPermissions([])
        self.assertEqual(200, self.client.get("/api/media-sources", headers=self.headers).status_code)
        self.assertEqual(403, self.client.get("/api/logs", headers=self.headers).status_code)
        self.assertEqual(403, self.client.get("/api/operation-logs?task_type=scan_job&task_id=1", headers=self.headers).status_code)
        self.setPermissions(["audit:read"])
        self.assertEqual(200, self.client.get("/api/logs", headers=self.headers).status_code)
        self.assertEqual(200, self.client.get("/api/operation-logs?task_type=scan_job&task_id=1", headers=self.headers).status_code)
        self.assertEqual(403, self.client.post("/api/logs/cleanup", headers=self.headers).status_code)
        self.setPermissions(["settings:write"])
        self.assertEqual(200, self.client.post("/api/logs/cleanup", headers=self.headers).status_code)

    def test_audit_intent_close_requires_both_audit_and_task_permissions(self):
        self.authenticate()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            cursor = connection.execute(
                "INSERT INTO file_action_audit_intents "
                "(task_type,task_id,context_json,status,request_phase,created_at,updated_at) "
                "VALUES ('rename_operation',1,'{}','review_required','process_interrupted','now','now')"
            )
            intentId = int(cursor.lastrowid)
            connection.commit()
        payload = {'reason': '已核对文件事实', 'evidence': '维护工单包含目录摘要'}
        for permissions in ([], ['audit:read'], ['task:manage']):
            self.setPermissions(permissions)
            response = self.client.post(
                f"/api/tasks/audit-intents/{intentId}/close",
                json=payload,
                headers=self.headers,
            )
            self.assertEqual(403, response.status_code)
        self.setPermissions(['audit:read', 'task:manage'])
        self.assertEqual(
            200,
            self.client.get('/api/tasks/audit-intents/capacity', headers=self.headers).status_code,
        )
        response = self.client.post(
            f"/api/tasks/audit-intents/{intentId}/close",
            json=payload,
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual('manually_closed', response.json()['status'])

    def test_change_password_revokes_all_sessions_and_allows_new_login(self):
        first = self.authenticate()
        second = login(self.settings, "admin", "TemporaryReview123!")
        change_password(self.settings, self.user.id, "TemporaryReview123!", "UpdatedReview123!")
        for token in (first.access_token, second.access_token):
            self.assertEqual(401, self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code)
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "UpdatedReview123!"})
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["user"]["mustChangePassword"])

    def test_local_recovery_revokes_sessions_and_http_never_resets(self):
        self.authenticate()
        self.assertEqual(403, self.client.post("/api/auth/reset-admin-password").status_code)
        recoverAdminPassword(self.settings)
        self.assertEqual(401, self.client.get("/api/auth/me", headers=self.headers).status_code)
        result = self.client.post("/api/auth/login", json={"username": "admin", "password": self.settings.auth.default_admin_password})
        self.assertEqual(200, result.status_code)
        self.assertTrue(result.json()["user"]["mustChangePassword"])

    def test_remote_recovery_checks_operation_type_for_all_permission_combinations(self):
        self.authenticate()
        for operationType, required in (("rename", "rename:execute"), ("rollback", "rollback:execute")):
            for permissions in ([], ["rename:execute"], ["rollback:execute"], ["rename:execute", "rollback:execute"]):
                self.setPermissions(permissions)
                item = SimpleNamespace(operation_type=operationType, status="completed")
                with self.subTest(operationType=operationType, permissions=permissions):
                    with patch("app.api.remote_operations.get_remote_operation_item", return_value=item):
                        with patch("app.api.remote_operations.recover_remote_operation", return_value={"authorized": True}) as recover:
                            with patch("app.api.remote_operations.runAuditedFileAction",
                                       side_effect=lambda settings, **kwargs: kwargs["execute"]()) as audited:
                                if required in permissions:
                                    # 返回值需符合真实服务结果结构。
                                    recover.return_value = SimpleNamespace(item=item, action="already_completed")
                                response = self.client.post("/api/remote-operations/1/recover", headers=self.headers)
                    self.assertEqual(200 if required in permissions else 403, response.status_code)
                    self.assertEqual(required in permissions, recover.called)
                    self.assertEqual(required in permissions, audited.called)
