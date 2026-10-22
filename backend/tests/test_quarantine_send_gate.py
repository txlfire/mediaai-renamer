"""发送前等待不得让30秒前检过期后仍发送MoveFile。"""

import time
import unittest
from unittest.mock import Mock, patch

import test_cloud_quarantine_execution as fixtures
from app.service import cloud_quarantine_execution_service as actions
from app.service import cloud_quarantine_item_service as items
from app.service import cd2_quarantine_protocol as wire
from app.service.cd2_quarantine_client import Cd2QuarantineClient, QuarantineRpcAdmission


class QuarantineSendGateTest(unittest.TestCase):
    def test_expired_durable_proof_after_wait_never_calls_network(self):
        f = fixtures.QuarantineExecutionTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        runId,token = f.bound()
        f.prechecks(runId,token)
        now = time.time()
        checks = f.state.sql("SELECT id FROM cloud_quarantine_rpcs WHERE run_id=? ORDER BY ordinal DESC LIMIT 4", (runId,))
        for row,age in zip(f.state.sql("SELECT id FROM cloud_quarantine_rpcs WHERE run_id=? ORDER BY ordinal LIMIT 3", (runId,)),(50,43,36)):
            f.state.sql("UPDATE cloud_quarantine_rpcs SET responded_at=?,sent_at=? WHERE id=?", (now-age,now-age,row[0]))
        for row,age in zip(reversed(checks),(28,21,14,0)):
            f.state.sql("UPDATE cloud_quarantine_rpcs SET responded_at=?,sent_at=? WHERE id=?", (now-age,now-age,row[0]))
        f.state.sql("UPDATE cloud_quarantine_runs SET created_at=? WHERE id=?", (now-60,runId))
        f.state.sql("UPDATE cloud_drive_budget_pool SET min_interval=7,last_started_at=NULL")
        clock = [now]
        client = object.__new__(Cd2QuarantineClient)
        client._closed,client._verified,client._metadata,client._lastSent = False,True,(),100
        client._channel = Mock()
        network = client._channel.unary_unary.return_value
        with patch("time.time",side_effect=lambda:clock[0]), patch("time.monotonic",side_effect=lambda:100+clock[0]-now), \
                patch("time.sleep",side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            items.prepareQuarantineItem(f.settings,runId,token,1)
            client.admission = QuarantineRpcAdmission(f.settings,runId,token)
            _,source,_,folder = actions.itemPaths(f.plan,1)
            request = wire.MoveFileRequest(theFilePaths=[source],destPath=folder,conflictPolicy=2,
                moveAcrossClouds=False,handleConflictRecursively=False)
            with self.assertRaisesRegex(actions.QuarantineError,"identity_precheck_expired"):
                client._call("MoveFile",request,wire.FileOperationResult,200,itemOrdinal=1,validate=lambda value:value.success)
        network.assert_not_called()
        self.assertGreaterEqual(clock[0]-now,7)
        self.assertEqual([("unknown",)],f.state.sql("SELECT phase FROM cloud_quarantine_rpcs WHERE method='MoveFile'"))
