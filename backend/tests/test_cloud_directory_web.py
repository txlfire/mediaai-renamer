"""HTTPS请求语义与实际子进程连本机TLS合成服务；不代表真实代理/私有CD2验收。"""

import time
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import test_cd2_directory_client as fixtures
from app.main import create_app


class CloudDirectoryWebTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Cd2DirectoryClientTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = bindCloudConfiguration(self.fixture.settings)
        self.fixture.fixture.state.sql("DELETE FROM cloud_directory_executions")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        login = self.client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.url = "/api/cloud-drive/directory-plans/" + self.fixture.plan["id"]
        self.requestId = str(uuid4())
        self.payload = {"requestId": self.requestId, "expectedRevision": 0, "policyHash": self.fixture.plan["policyHash"],
            "confirmMaintenance": True, "confirmGlobalFence": True, "apiToken": self.fixture.token}

    def waitFinished(self, *, allowAwaitingSync=False):
        deadline = time.monotonic() + 40
        last = None
        while time.monotonic() < deadline:
            response = self.client.get(self.url + "/execution", headers=self.headers)
            self.assertEqual(200, response.status_code, response.text)
            last = response.json()
            if last["status"] in {"completed", "cancelled"} or (last["status"] == "review" and not last["workerSlotHeld"]
                    and (allowAwaitingSync or last["reason"] != "awaiting_path_sync")):
                return last
            time.sleep(1.5)
        self.fail("合成执行未结束：" + str(last))

    def test_https_entry_starts_once_and_commits_path_without_resetting_read_consumption(self):
        policy = self.client.get("/api/cloud-drive/directory-policy", headers=self.headers)
        self.assertTrue(policy.json()["available"])
        self.assertNotIn(self.settings.cloudDrive.endpoint, policy.text)
        started = self.client.post(self.url + "/execute", headers=self.headers, json=self.payload)
        self.assertEqual(202, started.status_code, started.text)
        self.assertNotIn(self.fixture.token, started.text)
        again = self.client.post(self.url + "/execute", headers=self.headers, json=self.payload)
        self.assertEqual(409, again.status_code)
        result = self.waitFinished()
        self.assertEqual("completed", result["status"], result)
        self.assertFalse(result["globalSlotHeld"])
        self.assertFalse(result["pollingRecommended"])
        self.assertEqual(10, result["usedRpc"])
        found = self.client.get("/api/cloud-drive/directory-execution-requests/" + self.requestId, headers=self.headers)
        self.assertEqual(200, found.status_code)
        self.assertEqual(result["planId"], found.json()["planId"])
        self.assertEqual(1, sum(method == "rename" for method, _ in self.fixture.calls))
        self.assertEqual([(0,)], self.fixture.fixture.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_local_sync_retry_never_sends_another_remote_request(self):
        with patch("app.service.cloud_directory_web_service.commitDirectoryPathRevision", side_effect=RuntimeError("synthetic-local-failure")):
            self.assertEqual(202, self.client.post(self.url + "/execute", headers=self.headers, json=self.payload).status_code)
            result = self.waitFinished(allowAwaitingSync=True)
        self.assertEqual("awaiting_path_sync", result["reason"])
        count = len(self.fixture.calls)
        synced = self.client.post(self.url + "/commit-local", headers=self.headers,
            json={"expectedRevision": result["revision"], "confirmLocalOnly": True})
        self.assertEqual(200, synced.status_code, synced.text)
        self.assertEqual("completed", synced.json()["status"])
        self.assertEqual(count, len(self.fixture.calls))

    def test_http_and_strict_shapes_cannot_handoff_secrets_or_start_worker(self):
        response = self.client.post("http://testserver" + self.url + "/execute", headers=self.headers, json=self.payload)
        self.assertEqual(400, response.status_code)
        for patchBody in ({"apiToken": "bad\nsynthetic-secret"}, {"expectedRevision": True}, {"confirmMaintenance": 1},
                          {"maxRpc": 999}, {"sourcePath": "/other"}, {"confirmGlobalFence": True, "apiToken": ""}):
            response = self.client.post(self.url + "/execute", headers=self.headers, json={**self.payload, **patchBody})
            self.assertEqual(422, response.status_code)
            self.assertNotIn("synthetic-secret", response.text)
        self.assertEqual([], self.fixture.calls)
        self.assertEqual([(0,)], self.fixture.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_executions"))

    def test_confirmation_policy_and_current_direct_permissions_are_required(self):
        for values in ({"confirmGlobalFence": False}, {"confirmMaintenance": False}, {"policyHash": "b" * 64}):
            response = self.client.post(self.url + "/execute", headers=self.headers, json={**self.payload, **values})
            self.assertIn(response.status_code, {400, 409})
        self.fixture.fixture.state.sql("UPDATE users SET permissions_json='[\"scan:run\",\"cloud-directory:plan\"]'")
        self.assertEqual(403, self.client.post(self.url + "/execute", headers=self.headers, json=self.payload).status_code)
        self.assertEqual([], self.fixture.calls)

    def test_request_lookup_is_local_owner_scoped_and_does_not_prepare_or_execute(self):
        response = self.client.get("/api/cloud-drive/directory-execution-requests/" + self.requestId, headers=self.headers)
        self.assertEqual(404, response.status_code)
        self.assertEqual([], self.fixture.calls)
        self.assertEqual([(0,)], self.fixture.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_executions"))
