"""盘点本地结果及取消；受控创建/启动须另外显式启用。"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings
from app.main import create_app
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cloud_drive_budget_service import createCloudReadSession
from app.service import cloud_inventory_service as inventory


class CloudInventoryApiTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False), cloudDrive=CloudDriveSettings("/cloud/movies"))
        self.client = TestClient(create_app(self.settings))
        bindCloudConfiguration(self.settings)
        self.addCleanup(self.client.close)
        self.actor = create_user(self.settings, "operator", "test", "TestOnly123!", ["scan:run"], True)
        self.context = AuditWriteContext(self.actor)
        response = self.client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + response.json()["accessToken"]}
        session = createCloudReadSession(self.settings, requestId=str(uuid4()), endpoint="https://cd2.example",
            rootPath="/cloud/movies", maxRpc=10, durationSeconds=900, auditContext=self.context)
        self.jobId = inventory.createCloudInventoryJob(self.settings, requestId=str(uuid4()), sessionId=session["id"],
            selectedPaths=["/cloud/movies"], excludedPaths=[], auditContext=self.context)
        self.url = "/api/cloud-drive/inventory-jobs/" + self.jobId

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(query, args)
            connection.commit()

    def test_summary_paging_cancel_and_no_implicit_execution(self):
        result = self.client.get(self.url, headers=self.headers)
        self.assertEqual(200, result.status_code)
        self.assertFalse(result.json()["executionAvailable"])
        for forbidden in ("workerToken", "worker_token", "worker_pid", "endpoint", "apiToken"):
            self.assertNotIn(forbidden, result.text)
        page = self.client.get(self.url + "/entries", headers=self.headers).json()
        self.assertEqual([], page["items"])
        self.assertFalse(page["networkAccess"])
        self.assertEqual(422, self.client.get(self.url + "/entries?limit=201", headers=self.headers).status_code)
        cancelled = self.client.post(self.url + "/cancel", headers=self.headers)
        self.assertEqual(200, cancelled.status_code)
        self.assertEqual("cancelled", cancelled.json()["status"])
        self.assertEqual(422, self.client.post("/api/cloud-drive/inventory-jobs", headers=self.headers, json={}).status_code)
        self.assertFalse(self.client.get("/api/cloud-drive/inventory-policy", headers=self.headers).json()["available"])

    def test_local_job_selector_is_bounded_and_owner_isolated(self):
        url = "/api/cloud-drive/inventory-jobs"
        result = self.client.get(url, headers=self.headers).json()
        self.assertEqual(1, result["total"])
        self.assertEqual(self.jobId, result["items"][0]["id"])
        self.assertNotIn("worker", str(result))
        self.assertFalse(result["networkAccess"])
        self.assertEqual([], self.client.get(url + "?page=2", headers=self.headers).json()["items"])
        self.assertEqual(422, self.client.get(url + "?pageSize=51", headers=self.headers).status_code)
        self.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        self.assertEqual(0, self.client.get(url, headers=self.headers).json()["total"])

    def test_cancel_returns_pending_while_worker_slot_is_held(self):
        token = inventory.claimCloudInventoryJob(self.settings, self.jobId)
        inventory.persistCloudInventoryPid(self.settings, self.jobId, token, os.getpid())
        response = self.client.post(self.url + "/cancel", headers=self.headers)
        self.assertEqual(202, response.status_code)
        self.assertTrue(response.json()["cancelRequested"])
        self.assertTrue(response.json()["workerSlotHeld"])

    def test_anonymous_foreign_owner_and_revoked_user_cannot_read_or_cancel(self):
        self.assertEqual(401, self.client.get(self.url).status_code)
        self.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=? WHERE id=?", (self.actor.id + 1, self.jobId))
        for method, suffix in ((self.client.get, ""), (self.client.get, "/entries"), (self.client.post, "/cancel")):
            self.assertEqual(404, method(self.url + suffix, headers=self.headers).status_code)
        self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.actor.id,))
        self.assertEqual(403, self.client.get(self.url, headers=self.headers).status_code)

    def test_invalid_id_and_storage_error_never_echo_database_path(self):
        self.assertEqual(400, self.client.get("/api/cloud-drive/inventory-jobs/not-uuid", headers=self.headers).status_code)
        self.sql("DROP TABLE cloud_drive_inventory_jobs")
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertNotIn(str(self.settings.database_path), response.text)

    def test_local_budget_summary_keeps_usage_and_expiry_without_exposing_session_identity(self):
        self.sql("UPDATE cloud_drive_read_sessions SET used_rpc=7")
        self.sql("UPDATE cloud_drive_inventory_jobs SET received_entries=10001,received_bytes=max_bytes+100")
        result = self.client.get(self.url, headers=self.headers).json()
        self.assertEqual({"maxRpc": 10, "usedRpc": 7, "remainingRpc": 3, "status": "active",
                          "expiresAt": result["budget"]["expiresAt"], "expired": False}, result["budget"])
        self.assertEqual(0, result["remainingEntries"])
        self.assertEqual(0, result["remainingReceiveBytes"])
        self.assertGreater(result["createdAt"], 0)
        self.assertGreaterEqual(result["updatedAt"], result["createdAt"])
        self.assertNotIn("sessionId", result)
        self.assertNotIn("endpoint", result)
        self.sql("UPDATE cloud_drive_read_sessions SET expires_at=1")
        self.assertTrue(self.client.get(self.url, headers=self.headers).json()["budget"]["expired"])

    def test_directory_coverage_is_local_bounded_relative_and_cleared_aware(self):
        for index in range(205):
            self.sql("INSERT INTO cloud_drive_inventory_directories(job_id,path,depth,status) VALUES(?,?,1,?)",
                (self.jobId, f"/cloud/movies/category-{index:03d}", "partial" if index % 2 else "pending"))
        url = self.url + "/directories"
        first = self.client.get(url + "?limit=200", headers=self.headers)
        self.assertEqual(200, first.status_code)
        data = first.json()
        self.assertTrue(data["hasMore"])
        self.assertFalse(data["networkAccess"])
        self.assertEqual("", data["items"][0]["relativePath"])
        self.assertNotIn("/cloud/movies", first.text)
        second = self.client.get(url + f"?limit=200&afterId={data['nextId']}", headers=self.headers).json()
        self.assertEqual(6, len(second["items"]))
        self.assertEqual(422, self.client.get(url + "?limit=201", headers=self.headers).status_code)
        self.assertEqual(401, self.client.get(url).status_code)
        self.sql("UPDATE cloud_drive_inventory_jobs SET cache_state='cleared'")
        self.assertEqual(409, self.client.get(url, headers=self.headers).status_code)
        self.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        self.assertEqual(404, self.client.get(url, headers=self.headers).status_code)
