"""多项部分批次恢复与本地事务失败；无真实云端访问。"""

from hashlib import sha256
import json
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc
import test_cd2_directory_client as native
import test_cloud_directory_recovery as recoveryFixtures
from app.service import cd2_directory_protocol as wire
from app.service.cloud_directory_admission_service import approveDirectoryExecution, bindDirectoryWorker
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_inventory_service import inventoryDb
from app.utils.cloud_drive_paths import canonicalCd2Target


class PartialNativeFixture(native.Cd2DirectoryClientTest):
    def setUp(self):
        super().setUp()
        self.fixture.state.sql("DELETE FROM cloud_directory_executions")
        jobId = self.fixture.observed(["[论坛推广] 第一部 (2024)", "[论坛推广] 第二部 (2025)"])
        self.fixture.state.sql("UPDATE cloud_drive_read_sessions SET endpoint_hash=?", (sha256(canonicalCd2Target(self.settings.cloudDrive.endpoint).encode()).hexdigest(),))
        self.plan = self.fixture.plan(jobId)
        self.workerToken = approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.fixture.context)
        import os
        bindDirectoryWorker(self.settings, self.plan["id"], self.workerToken, os.getpid())

    def find(self, request, context):
        self.calls.append(("find", dict(context.invocation_metadata()).get("authorization")))
        objectId = "synthetic-parent"
        if request.path != "/":
            matches = [(index, item) for index, item in enumerate(self.plan["items"], 1)
                if request.path in {"/" + item["sourceName"], "/" + item["targetName"]}]
            if not matches:
                context.abort(grpc.StatusCode.NOT_FOUND, "synthetic")
            ordinal, item = matches[0]
            location = item["targetName"] if ordinal == 1 else item["sourceName"]
            if request.path != "/" + location:
                context.abort(grpc.StatusCode.NOT_FOUND, "synthetic")
            objectId = item["remoteId"]
        return wire.CloudDriveFile(id=objectId, name=request.path[1:], fullPathName=request.path, isDirectory=True, fileType=0)


# 仅作为夹具使用，不将其继承的原生测试再执行一遍。
PartialNativeFixture.__test__ = False


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(PartialRecoveryTest)


class PartialRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.helper = recoveryFixtures.DirectoryRecoveryTest()
        self.helper.fixtureType = PartialNativeFixture
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.sql = self.helper.sql

    def test_partial_batch_records_both_positions_and_only_moves_one_local_directory_revision(self):
        self.helper.runRecovery()
        self.assertEqual([(1, "at_target"), (2, "at_source")], self.sql("SELECT ordinal,position FROM cloud_directory_recovery_items ORDER BY ordinal"))
        self.assertEqual([(8, 8)], self.sql("SELECT used_rpc,max_rpc FROM cloud_directory_recoveries"))
        calls = len(self.helper.fixture.calls)
        self.helper.commit()
        self.assertEqual(calls, len(self.helper.fixture.calls))
        self.assertEqual([(1,)], self.sql("SELECT ordinal FROM cloud_directory_path_revisions"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE ordinal=2"))

    def test_audit_or_fallback_logger_failure_cannot_publish_uncommitted_position_or_erase_freeze(self):
        self.helper.runRecovery()
        with patch("app.service.cloud_directory_recovery_service.record_audit_event", side_effect=RuntimeError("synthetic-audit")):
            with self.assertRaises(RuntimeError):
                self.helper.commit()
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))
        self.assertEqual([(None,)], self.sql("SELECT reviewed_by_recovery FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        with patch("app.service.cloud_directory_log_service.record_operation_log", side_effect=RuntimeError("synthetic-log")), \
                patch("app.service.cloud_directory_log_service.get_logger", side_effect=RuntimeError("synthetic-fallback")):
            self.helper.commit()
        self.assertEqual([(0,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_expired_position_snapshot_cannot_be_synced_even_when_worker_recently_reaped(self):
        self.helper.runRecovery()
        self.sql("UPDATE cloud_directory_recovery_rpcs SET responded_at=? WHERE method='FindFileByPath'", (time.time() - 301,))
        with self.assertRaisesRegex(CloudDirectoryError, "recovery_not_ready_or_stale"):
            self.helper.commit()
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_read_recovery_slot_alone_still_blocks_normal_work(self):
        from app.service.cloud_directory_guard import directoryExecutionHeld
        self.helper.approve()
        self.sql("UPDATE cloud_directory_executions SET slot_held=0")
        with inventoryDb(self.helper.settings) as connection:
            self.assertTrue(directoryExecutionHeld(connection))
