"""计划记录列表只读、所有者隔离、有界分页，不校验或启动云端。"""

from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient

import test_cloud_directory_reverse as fixtures
from app.main import create_app
from app.service.auth_service import create_user


class DirectoryHistoryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DirectoryReverseTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = self.fixture.settings
        self.fixture.reverse()
        self.client = TestClient(create_app(self.settings))
        bindCloudConfiguration(self.settings)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "cloud-operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + login.json()["accessToken"]}

    def test_list_is_bounded_and_includes_original_lookup_markers_without_rpcs(self):
        response = self.client.get("/api/cloud-drive/directory-plans?page=1&pageSize=1", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        value = response.json()
        self.assertEqual(2, value["total"])
        self.assertEqual(1, len(value["items"]))
        self.assertFalse(value["networkAccess"])
        self.assertFalse(value["replayed"])
        self.assertEqual("reverse", value["items"][0]["kind"])
        page = self.client.get("/api/cloud-drive/directory-plans?page=100&pageSize=1", headers=self.headers).json()
        self.assertEqual(2, page["page"])
        self.assertTrue(page["items"][0]["executionRequestId"])
        self.assertNotIn("worker_token", response.text)
        self.assertEqual(1, self.fixture.sql("SELECT COUNT(*) FROM cloud_directory_executions")[0][0])

    def test_other_owner_is_empty_and_reverse_endpoint_rejects_extra_scope(self):
        create_user(self.settings, "other-history", "synthetic", "TestOnly123!", ["cloud-directory:plan"], True)
        login = self.client.post("/api/auth/login", json={"username": "other-history", "password": "TestOnly123!"})
        other = {"Authorization": "Bearer " + login.json()["accessToken"]}
        value = self.client.get("/api/cloud-drive/directory-plans", headers=other).json()
        self.assertEqual([], value["items"])
        url = "/api/cloud-drive/directory-plans/" + self.fixture.plan["id"] + "/reverse"
        payload = {"requestId": str(uuid4()), "expectedRevision": self.fixture.revision}
        self.assertEqual(404, self.client.post(url, headers=other, json=payload).status_code)
        self.assertEqual(422, self.client.post(url, headers=self.headers, json={**payload, "targetName": "different"}).status_code)
        prepared = self.client.post(url, headers=self.headers, json=payload)
        self.assertEqual(201, prepared.status_code, prepared.text)
        self.assertEqual("reverse", prepared.json()["kind"])
