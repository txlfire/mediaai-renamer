"""隔离产品路由只在明确HTTPS请求交接Token；合成准入不发真实网络。"""

import json
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import test_cloud_quarantine_execution as fixtures
from app.main import create_app
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_quarantine_web_service import CloudQuarantineRunner
from app.service.operation_log_service import list_operation_logs


class QuarantineWebTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuarantineExecutionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state,self.settings,self.actor = self.fixture.state,bindCloudConfiguration(self.fixture.settings),self.fixture.actor
        self.state.sql("UPDATE users SET permissions_json=?", (json.dumps(["scan:run","cloud-quarantine:plan","cloud-quarantine:execute","audit:read","settings:write"]),))
        app = create_app(self.settings)
        self.client = TestClient(app,base_url="https://testserver")
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login",json={"username":"operator","password":"TestOnly123!"})
        self.headers = {"Authorization":"Bearer "+login.json()["accessToken"]}
        self.plan = self.fixture.plan
        self.base = "/api/cloud-drive/quarantine-plans/"+self.plan["id"]
        self.runner = Mock()
        def start(planId,**values):
            values.pop("apiToken")
            return actions.approveQuarantineRun(self.settings,planId,**values)[0]
        self.runner.start.side_effect = start
        app.state.cloudQuarantineRunner = self.runner
        self.payload = {"requestId":str(uuid4()),"expectedRevision":0,"policyHash":self.plan["policyHash"],
            "confirmMaintenance":True,"confirmGlobalFence":True,"apiToken":"synthetic-private-token"}

    def test_https_explicit_start_original_uuid_query_and_no_post_replay(self):
        response = self.client.post(self.base+"/execute",headers=self.headers,json=self.payload)
        self.assertEqual(202,response.status_code,response.text)
        run = response.json()
        self.assertEqual("accepted",run["status"])
        self.assertNotIn(self.payload["apiToken"],response.text)
        self.assertEqual(run,self.client.get("/api/cloud-drive/quarantine-run-requests/"+self.payload["requestId"],headers=self.headers).json())
        self.assertEqual(409,self.client.post(self.base+"/execute",headers=self.headers,json=self.payload).status_code)
        self.assertEqual([(1,)],self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_runs"))
        self.assertNotIn(self.payload["apiToken"],json.dumps(self.state.sql("SELECT * FROM cloud_quarantine_runs")))

    def test_http_missing_confirmation_extra_fields_and_c2_permissions_cannot_execute(self):
        url = "http://testserver"+self.base+"/execute"
        self.assertEqual(400,self.client.post(url,headers=self.headers,json=self.payload).status_code)
        self.runner.start.assert_not_called()
        self.assertEqual(422,self.client.post(self.base+"/execute",headers=self.headers,json={**self.payload,"destPath":"/other"}).status_code)
        self.assertEqual(400,self.client.post(self.base+"/execute",headers=self.headers,json={**self.payload,"confirmMaintenance":False}).status_code)
        self.state.sql("UPDATE users SET permissions_json='[\"scan:run\",\"cloud-directory:execute\"]'")
        self.assertEqual(403,self.client.post(self.base+"/execute",headers=self.headers,json=self.payload).status_code)

    def test_log_owner_scope_clear_hint_and_safety_ledger_preservation(self):
        run = self.client.post(self.base+"/execute",headers=self.headers,json=self.payload).json()
        url = f"/api/operation-logs?task_type=cloud_quarantine_run&task_id={run['logId']}"
        page = self.client.get(url,headers=self.headers)
        self.assertEqual(200,page.status_code,page.text)
        self.assertTrue(page.json()["log_available"])
        self.state.sql("UPDATE cloud_quarantine_runs SET status='cancelled',slot_held=0")
        self.state.sql("DELETE FROM operation_logs WHERE task_type='cloud_quarantine_run'")
        cleared = self.client.get(url,headers=self.headers).json()
        self.assertTrue(cleared["cleared"])
        self.assertFalse(cleared["log_available"])
        self.assertEqual([(1,)],self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_runs"))
        self.state.sql("UPDATE cloud_quarantine_runs SET actor_id=actor_id+1")
        self.assertEqual(404,self.client.get(url,headers=self.headers).status_code)

    def test_target_lookup_is_indexed_local_and_owner_review_bound(self):
        review = self.fixture.fixture.review
        id = self.fixture.fixture.ids[0]
        url = f"/api/cloud-drive/quarantine-targets/{review['id']}?entryId={id}"
        before = self.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions")
        result = self.client.get(url,headers=self.headers)
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual(self.fixture.fixture.ids[3],result.json()["items"][0]["id"])
        self.assertEqual(before,self.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.state.sql("UPDATE cloud_ad_reviews SET actor_id=actor_id+1")
        self.assertEqual(404,self.client.get(url,headers=self.headers).status_code)

    def test_thread_start_failure_releases_only_unstarted_approval(self):
        runner = CloudQuarantineRunner(self.settings)
        runner.open()
        with patch("threading.Thread.start",side_effect=RuntimeError("synthetic")):
            with self.assertRaisesRegex(actions.QuarantineError,"worker_start_failed"):
                runner.start(self.plan["id"],apiToken=self.payload["apiToken"],requestId=self.payload["requestId"],
                    expectedRevision=0,policyHash=self.plan["policyHash"],confirmMaintenance=True,confirmGlobalFence=True,
                    auditContext=self.fixture.context)
        runner.close()
        self.assertEqual([("cancelled",0,0)],self.state.sql("SELECT status,slot_held,used_rpc FROM cloud_quarantine_runs"))
