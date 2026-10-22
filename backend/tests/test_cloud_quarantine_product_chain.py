"""产品HTTP入口→实际隐藏worker→本机TLS服务→本地同步及反向新计划。"""

import time
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
import test_cd2_quarantine_client as tlsFixtures
from app.main import create_app
from app.service.cloud_quarantine_web_service import CloudQuarantineRunner


class QuarantineProductChainTest(unittest.TestCase):
    def test_http_product_chain_uses_new_reverse_request_and_actual_worker(self):
        fixture=tlsFixtures.Cd2QuarantineClientTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # TLS夹具已经准入一个尚未启动的run；本例另从HTTP批准新计划。
        fixture.state.sql("DELETE FROM cloud_quarantine_runs WHERE id=?",(fixture.runId,))
        app=create_app(fixture.settings)
        runner=CloudQuarantineRunner(fixture.settings)
        runner.open()
        self.addCleanup(runner.close)
        app.state.cloudQuarantineRunner=runner
        client=TestClient(app,base_url="https://testserver")
        self.addCleanup(client.close)
        login=client.post("/api/auth/login",json={"username":"operator","password":"TestOnly123!"})
        self.assertEqual(200,login.status_code,login.text)
        headers={"Authorization":"Bearer "+login.json()["accessToken"]}

        def move(plan):
            requestId=str(uuid4())
            response=client.post("/api/cloud-drive/quarantine-plans/"+plan["id"]+"/execute",headers=headers,json={
                "requestId":requestId,"expectedRevision":plan["revision"],"policyHash":plan["policyHash"],
                "confirmMaintenance":True,"confirmGlobalFence":True,"apiToken":fixture.apiToken})
            self.assertEqual(202,response.status_code,response.text)
            self.assertNotIn(fixture.apiToken,response.text)
            deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                current=client.get("/api/cloud-drive/quarantine-run-requests/"+requestId,headers=headers)
                self.assertEqual(200,current.status_code,current.text)
                value=current.json()
                if value["status"]=="review" and not value["workerSlotHeld"]:
                    # 状态落库与监督线程退出之间有很短的收尾间隔；只等待，不重发动作。
                    while runner._thread is not None and time.monotonic()<deadline:
                        time.sleep(0.02)
                    return value
                time.sleep(0.1)
            self.fail("本机产品worker未在预算内完成")

        def commit(value):
            response=client.post("/api/cloud-drive/quarantine-runs/"+value["id"]+"/commit-local",headers=headers,
                json={"expectedRevision":value["revision"],"confirmLocalOnly":True})
            self.assertEqual(200,response.status_code,response.text)
            self.assertFalse(response.json()["globalSlotHeld"])
            return response.json()

        forward=move(fixture.plan)
        self.assertEqual("awaiting_path_sync",forward["reason"])
        self.assertTrue(forward["globalSlotHeld"])
        self.assertEqual(1,fixture.moves)
        committed=commit(forward)
        reverse=client.post("/api/cloud-drive/quarantine-plans/"+fixture.plan["id"]+"/reverse",headers=headers,
            json={"requestId":str(uuid4()),"expectedRevision":fixture.plan["revision"]})
        self.assertEqual(201,reverse.status_code,reverse.text)
        self.assertEqual("reverse",reverse.json()["kind"])
        backward=move(reverse.json())
        self.assertNotEqual(forward["requestId"],backward["requestId"])
        self.assertGreater(commit(backward)["committedEpoch"],committed["committedEpoch"])
        self.assertEqual(2,fixture.moves)
        item=fixture.plan["items"][0]
        self.assertEqual(item["remoteId"],fixture.objects["/"+item["sourceName"]][0])
        self.assertNotIn("/"+fixture.plan["targetName"]+"/"+item["sourceName"],fixture.objects)
        self.assertEqual([(0,)],fixture.state.sql("SELECT COUNT(*) FROM cloud_quarantine_rpcs WHERE phase!='responded'"))
