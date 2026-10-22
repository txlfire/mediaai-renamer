"""本机严格TLS合成端点上验证新反向计划和新隔离worker，不接真实云盘。"""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cd2_directory_client as fixtures
from app.service.cloud_directory_worker_service import executeDirectoryBatch, finishDirectoryWorker, superviseDirectoryWorker
from app.service.cloud_directory_path_service import commitDirectoryPathRevision
from app.service.cloud_directory_reverse_service import createReverseDirectoryPlan
from app.service.cloud_directory_admission_service import approveDirectoryExecution


class ReverseWorkerTest(unittest.TestCase):
    def test_native_rename_then_reverse_uses_two_budgets_and_restores_same_identity(self):
        fixture = fixtures.Cd2DirectoryClientTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        settings, original = fixture.settings, fixture.plan
        state, context = fixture.fixture.state, fixture.fixture.context
        actorId = state.actor.id
        executeDirectoryBatch(settings, original["id"], fixture.workerToken, fixture.token)
        with patch("app.service.cloud_directory_worker_service.isProcessAlive", return_value=False):
            finishDirectoryWorker(settings, original["id"], fixture.workerToken, os.getpid())
        commitDirectoryPathRevision(settings, original["id"], actorId=actorId)
        revision = state.sql("SELECT revision FROM cloud_directory_executions WHERE plan_id=?", (original["id"],))[0][0]
        inverse = createReverseDirectoryPlan(settings, original["id"], requestId=str(uuid4()), expectedRevision=revision, auditContext=context)
        self.assertTrue(inverse["valid"], inverse)
        self.assertEqual(original["items"][0]["remoteId"], inverse["items"][0]["remoteId"])
        # 合成端点从新计划的源名（原目标名）继续定位同一对象。
        fixture.plan, fixture.renamed = inverse, False
        token = approveDirectoryExecution(settings, inverse["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=context)
        superviseDirectoryWorker(settings, inverse["id"], token, fixture.token)
        self.assertEqual([("review", "awaiting_path_sync", None)], state.sql("SELECT status,reason,worker_pid FROM cloud_directory_executions WHERE plan_id=?", (inverse["id"],)))
        self.assertEqual(2, commitDirectoryPathRevision(settings, inverse["id"], actorId=actorId))
        self.assertEqual([(10,), (10,)], state.sql("SELECT used_rpc FROM cloud_directory_executions ORDER BY created_at"))
        self.assertEqual(2, sum(method == "rename" for method, _ in fixture.calls))
        self.assertEqual([(0,)], state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertEqual(original["items"][0]["sourceName"], inverse["items"][0]["targetName"])
