"""目录日志复用清理和有界查询；安全证据不随普通日志删除。"""

import json
import sqlite3
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_cloud_directory_paths as fixtures
from app.core.cloud_directory_schema import ensureCloudDirectoryLogSchema
from app.main import create_app
from app.service.auth_service import create_user
from app.service.cloud_directory_log_service import recordDirectoryLog
from app.service.cloud_directory_web_service import getDirectoryExecution
from app.service.cloud_inventory_service import inventoryDb, CloudInventoryError
from app.service.operation_log_service import list_operation_logs, record_operation_log


class DirectoryLogTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudDirectoryPathTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.seedVerified()
        self.settings, self.planId, self.actorId = self.fixture.settings, self.fixture.plan["id"], self.fixture.actor.id
        self.fixture.state.sql("UPDATE users SET permissions_json=? WHERE id=?",
            (json.dumps(["scan:run", "cloud-directory:plan", "cloud-directory:execute", "audit:read"]), self.actorId))
        self.client = TestClient(create_app(self.settings))
        bindCloudConfiguration(self.settings)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/auth/login", json={"username": "cloud-operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + response.json()["accessToken"]}
        self.logId = self.fixture.state.sql("SELECT log_id FROM cloud_directory_executions")[0][0]
        self.url = f"/api/operation-logs?task_type=cloud_directory_execution&task_id={self.logId}"

    def test_stable_log_id_migration_and_events_do_not_contain_paths_or_tokens(self):
        with inventoryDb(self.settings, write=True) as connection:
            connection.execute("UPDATE cloud_directory_executions SET log_id=NULL")
            ensureCloudDirectoryLogSchema(connection)
            ensureCloudDirectoryLogSchema(connection)
        self.assertEqual([(self.logId,)], self.fixture.state.sql("SELECT log_id FROM cloud_directory_executions"))
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()["log_available"])
        self.assertNotIn(self.fixture.plan["parentPath"], response.text)
        self.assertNotIn(self.fixture.plan["items"][0]["remoteId"], response.text)
        self.assertNotIn("worker_token", response.text)

    def test_foreign_user_and_revoked_plan_permission_cannot_read_or_export(self):
        create_user(self.settings, "other-log-reader", "synthetic", "TestOnly123!", ["audit:read", "cloud-directory:plan"], True)
        login = self.client.post("/api/auth/login", json={"username": "other-log-reader", "password": "TestOnly123!"})
        other = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.assertEqual(404, self.client.get(self.url, headers=other).status_code)
        self.assertEqual(404, self.client.get(self.url.replace("operation-logs?", "operation-logs/export?"), headers=other).status_code)
        self.fixture.state.sql("UPDATE users SET permissions_json='[\"audit:read\"]' WHERE id=?", (self.actorId,))
        self.assertEqual(403, self.client.get(self.url, headers=self.headers).status_code)

    def test_cleanup_message_and_button_availability_do_not_erase_evidence(self):
        self.fixture.commit()
        before = self.fixture.state.sql("SELECT * FROM cloud_directory_rpc_intents")
        self.fixture.state.sql("DELETE FROM operation_logs WHERE task_type='cloud_directory_execution'")
        page = list_operation_logs(self.settings, task_type="cloud_directory_execution", task_id=self.logId)
        self.assertTrue(page.cleared)
        self.assertFalse(page.log_available)
        self.assertIn("清理", page.message)
        value = getDirectoryExecution(self.settings, self.planId, self.actorId)
        self.assertFalse(value["logAvailable"])
        self.assertEqual("completed", value["status"])
        self.assertEqual(before, self.fixture.state.sql("SELECT * FROM cloud_directory_rpc_intents"))
        self.assertEqual([(1,)], self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))

    def test_ordinary_log_failure_does_not_discard_path_commit_or_send_rpc(self):
        with patch("app.service.cloud_directory_log_service.record_operation_log", side_effect=sqlite3.OperationalError("synthetic-private-value")):
            self.fixture.commit()
        value = getDirectoryExecution(self.settings, self.planId, self.actorId)
        self.assertEqual("completed", value["status"])
        self.assertFalse(value["globalSlotHeld"])
        self.assertEqual(1, value["usedRpc"])

    def test_incremental_logs_are_bounded_and_unknown_stages_cannot_inject_text(self):
        with inventoryDb(self.settings, write=True) as connection:
            for ordinal in range(1, 6):
                recordDirectoryLog(self.settings, self.planId, "item_prechecked", connection=connection, ordinal=ordinal)
            recordDirectoryLog(self.settings, self.planId, "synthetic-private-message", connection=connection)
        first = self.client.get(self.url + "&limit=2", headers=self.headers).json()
        second = self.client.get(self.url + "&limit=2&after_id=" + str(first["latest_id"]), headers=self.headers).json()
        self.assertEqual(2, len(first["items"]))
        self.assertEqual(2, len(second["items"]))
        self.assertLess(first["latest_id"], second["items"][0]["id"])
        self.assertEqual(6, first["total"])

    def test_real_sqlite_full_rolls_back_directory_commit_instead_of_reporting_success(self):
        before=self.fixture.state.sql("SELECT status,slot_held,revision FROM cloud_directory_executions")
        def exhaust(settings,**values):
            connection=values["connection"]
            pages=connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={pages}")
            values["message"]="x"*(connection.execute("PRAGMA page_size").fetchone()[0]*32)
            return record_operation_log(settings,**values)
        with patch("app.service.cloud_directory_log_service.record_operation_log",side_effect=exhaust):
            with self.assertRaisesRegex(CloudInventoryError,"storage_unavailable"):
                self.fixture.commit()
        self.assertEqual(before,self.fixture.state.sql("SELECT status,slot_held,revision FROM cloud_directory_executions"))
        self.assertEqual([(0,)],self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.fixture.commit()
        self.assertEqual("completed",getDirectoryExecution(self.settings,self.planId,self.actorId)["status"])
