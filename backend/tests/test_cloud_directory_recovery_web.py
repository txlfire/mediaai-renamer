"""受控只读恢复HTTP入口与真实隐藏子进程；仅合成本机TLS端点。"""

import time
import json
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_cloud_directory_recovery as fixtures
from app.main import create_app
from app.service.auth_service import create_user


class DirectoryRecoveryWebTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DirectoryRecoveryTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings, self.sql = bindCloudConfiguration(self.fixture.settings), self.fixture.sql
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.requestId = str(uuid4())
        self.url = "/api/cloud-drive/directory-plans/" + self.fixture.plan["id"] + "/recoveries"
        self.payload = {"requestId": self.requestId, "expectedRevision": self.fixture.originalRevision,
            "confirmMaintenance": True, "confirmRemoteIdle": True, "apiToken": self.fixture.fixture.token}

    def waitFinished(self):
        deadline = time.monotonic() + 40
        value = None
        while time.monotonic() < deadline:
            response = self.client.get("/api/cloud-drive/directory-recovery-requests/" + self.requestId, headers=self.headers)
            self.assertEqual(200, response.status_code, response.text)
            value = response.json()
            if not value["workerSlotHeld"] and not value["recoverySlotHeld"]:
                return value
            time.sleep(1.5)
        self.fail("只读恢复未退出：" + str(value))

    def test_https_start_is_single_use_worker_reads_only_and_local_commit_is_explicit(self):
        self.fixture.fixture.renamed = True
        response = self.client.post(self.url, headers=self.headers, json=self.payload)
        self.assertEqual(202, response.status_code, response.text)
        self.assertNotIn(self.fixture.fixture.token, response.text)
        self.assertEqual(409, self.client.post(self.url, headers=self.headers, json=self.payload).status_code)
        value = self.waitFinished()
        self.assertEqual("ready", value["status"], value)
        self.assertEqual(6, value["usedRpc"])
        self.assertEqual("at_target", value["items"][0]["position"])
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))
        calls = len(self.fixture.fixture.calls)
        committed = self.client.post("/api/cloud-drive/directory-recoveries/" + value["id"] + "/commit-local", headers=self.headers,
            json={"expectedRevision": value["revision"], "confirmCurrentPositions": True})
        self.assertEqual(200, committed.status_code, committed.text)
        self.assertEqual("committed", committed.json()["status"])
        self.assertEqual(calls, len(self.fixture.fixture.calls))
        self.assertFalse(any(method == "rename" for method, _ in self.fixture.fixture.calls))
        self.assertEqual([("unknown", None)], self.sql("SELECT phase,response_code FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        self.assertEqual([(2, 0)], self.sql("SELECT used_rpc,slot_held FROM cloud_directory_executions"))
        original = self.client.get(self.url.removesuffix("/recoveries") + "/execution", headers=self.headers)
        self.assertEqual("committed", original.json()["latestRecovery"]["status"])
        self.assertEqual("positions_reconciled", original.json()["reason"])

    def test_http_bad_shapes_and_unconfirmed_requests_never_handoff(self):
        response = self.client.post("http://testserver" + self.url, headers=self.headers, json=self.payload)
        self.assertEqual(400, response.status_code)
        for fields in ({"confirmRemoteIdle": False}, {"confirmMaintenance": False}):
            self.assertEqual(400, self.client.post(self.url, headers=self.headers, json={**self.payload, **fields}).status_code)
        for fields in ({"expectedRevision": True}, {"confirmRemoteIdle": 1}, {"sourcePath": "/other"},
                       {"apiToken": "bad\nsynthetic-secret"}, {"maxRpc": 999}):
            response = self.client.post(self.url, headers=self.headers, json={**self.payload, **fields})
            self.assertEqual(422, response.status_code)
            self.assertNotIn("synthetic-secret", response.text)
        self.assertEqual([], self.fixture.fixture.calls)
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM cloud_directory_recoveries"))

    def test_local_lookup_is_owner_scoped_and_does_not_start_any_worker(self):
        url = "/api/cloud-drive/directory-recovery-requests/" + self.requestId
        self.assertEqual(404, self.client.get(url, headers=self.headers).status_code)
        recoveryId, _ = self.fixture.approve(requestId=self.requestId)
        found = self.client.get(url, headers=self.headers)
        self.assertEqual("accepted", found.json()["status"])
        self.assertFalse(found.json()["workerSlotHeld"])
        create_user(self.settings, "other-recovery-viewer", "synthetic", "TestOnly123!", ["cloud-directory:plan"], True)
        login = self.client.post("/api/auth/login", json={"username": "other-recovery-viewer", "password": "TestOnly123!"})
        other = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.assertEqual(404, self.client.get("/api/cloud-drive/directory-recoveries/" + recoveryId, headers=other).status_code)
        self.assertEqual(404, self.client.get(url, headers=other).status_code)
        self.assertEqual([], self.fixture.fixture.calls)

    def test_review_worker_endpoint_requires_permissions_and_stops_at_local_reaping(self):
        recoveryId, _ = self.fixture.approve()
        url = "/api/cloud-drive/directory-recoveries/" + recoveryId + "/review-worker"
        payload = {"expectedRevision": 0, "confirmClientsStopped": True, "confirmRemoteIdle": True}
        self.assertEqual(403, self.client.post(url, headers=self.headers, json=payload).status_code)
        self.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps(
            ["scan:run", "cloud-directory:plan", "cloud-directory:execute", "settings:write"]), self.fixture.actorId))
        self.assertEqual(400, self.client.post(url, headers=self.headers, json={**payload, "confirmRemoteIdle": False}).status_code)
        self.assertEqual(422, self.client.post(url, headers=self.headers, json={**payload, "apiToken": "synthetic-never-accepted"}).status_code)
        self.assertEqual(409, self.client.post(url, headers=self.headers, json=payload).status_code)
        with patch("app.service.cloud_directory_recovery_worker_service.isProcessAlive", return_value=False):
            response = self.client.post(url, headers=self.headers, json=payload)
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()["recoverySlotHeld"])
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))
        self.assertEqual([], self.fixture.fixture.calls)

    def test_cancel_during_read_reaps_child_but_original_unknown_and_freeze_remain(self):
        self.fixture.fixture.tokenDelay = 5
        started = self.client.post(self.url, headers=self.headers, json=self.payload)
        self.assertEqual(202, started.status_code)
        recoveryId = started.json()["id"]
        deadline = time.monotonic() + 15
        while not self.fixture.fixture.calls and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.fixture.fixture.calls)
        current = self.client.get("/api/cloud-drive/directory-recoveries/" + recoveryId, headers=self.headers).json()
        cancelled = self.client.post("/api/cloud-drive/directory-recoveries/" + recoveryId + "/cancel", headers=self.headers,
            json={"expectedRevision": current["revision"]})
        self.assertEqual(200, cancelled.status_code, cancelled.text)
        final = self.waitFinished()
        self.assertEqual("review", final["status"])
        self.assertFalse(final["workerSlotHeld"])
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_recovery_rpcs"))
        self.assertEqual([("unknown", None)], self.sql("SELECT phase,reviewed_by_recovery FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))
        self.assertFalse(any(method == "rename" for method, _ in self.fixture.fixture.calls))
