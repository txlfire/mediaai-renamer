"""C2预算与公共门禁仅使用临时库；没有连接器、令牌或真实RPC。"""

from dataclasses import replace
import json
import os
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_directory_plans as fixtures
from app.core.config import CloudDriveSettings, cloudDriveSettingsFromDict
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service import cloud_directory_admission_service as admission
from app.service.cloud_directory_guard import directoryExecutionHeld, requireNoDirectoryExecution
from app.service.cloud_directory_plan_service import CloudDirectoryError, cancelDirectoryPlan
from app.service.cloud_drive_budget_service import CloudDriveAdmission, CloudBudgetError
from app.service.cloud_inventory_service import claimCloudInventoryJob, inventoryDb
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache, setCloudInventoryKeep
from app.service.file_task_queue_service import assertFileTaskSlotFree
from app.service.scan_queue_service import claimScan


class DirectoryAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudDirectoryPlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = replace(self.fixture.settings, cloudDrive=replace(self.fixture.settings.cloudDrive,
            directoryRenameEnabled=True, directoryMappingRevision="synthetic-v1"))
        self.fixture.settings = self.settings
        self.context = self.fixture.context
        self.fixture.state.sql("UPDATE users SET permissions_json=?", (json.dumps(["scan:run", "cloud-directory:plan", "cloud-directory:execute"]),))
        self.clock = time.time() + 1
        self.addCleanup(patch.stopall)
        patch.object(admission, "_wallNow", side_effect=lambda: self.clock).start()

    def approve(self, planId=None):
        self.plan = self.fixture.plan() if planId is None else self.fixture.get(planId)
        return admission.approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()),
            expectedRevision=0, confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)

    def running(self):
        token = self.approve()
        admission.bindDirectoryWorker(self.settings, self.plan["id"], token, os.getpid())
        return token

    def send(self, token, method="GetRuntimeInfo", **options):
        return admission.reserveDirectoryRpc(self.settings, self.plan["id"], token, method, **options)

    def test_default_disabled_and_config_requires_explicit_mapping(self):
        self.assertFalse(CloudDriveSettings().directoryRenameEnabled)
        for values in ({"directory_rename_enabled": 1}, {"directory_rename_enabled": True},
                       {"directory_mapping_revision": "bad/epoch"}, {"directory_mapping_revision": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                cloudDriveSettingsFromDict(values)
        plan = self.fixture.plan()
        with self.assertRaisesRegex(CloudDirectoryError, "directory_execution_disabled"):
            admission.approveDirectoryExecution(replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, directoryRenameEnabled=False)),
                plan["id"], requestId=str(uuid4()), expectedRevision=0, confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
        self.assertEqual([(0,)], self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_executions"))

    def test_approval_is_local_single_use_and_does_not_spend_read_budget(self):
        self.plan = self.fixture.plan()
        before = self.fixture.state.sql("SELECT * FROM cloud_drive_read_sessions")
        token = self.approve(self.plan["id"])
        self.assertTrue(token)
        self.assertEqual(before, self.fixture.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual([("accepted", 10, 0, 1048576, 1)], self.fixture.state.sql("SELECT status,max_rpc,used_rpc,max_bytes,slot_held FROM cloud_directory_executions"))
        with self.assertRaisesRegex(CloudDirectoryError, "execution_already_accepted"):
            self.approve(self.plan["id"])
        with self.assertRaisesRegex(CloudDirectoryError, "execution_accepted"):
            cancelDirectoryPlan(self.settings, self.plan["id"], expectedRevision=0, auditContext=self.context)

    def test_near_expiry_plan_is_rejected_before_slot_or_rpc(self):
        plan = self.fixture.plan()
        self.fixture.state.sql("UPDATE cloud_directory_plans SET expires_at=? WHERE id=?", (self.clock + 20, plan["id"]))
        with self.assertRaisesRegex(CloudDirectoryError, "batch_exceeds_time_budget"):
            self.approve(plan["id"])
        self.assertEqual([(0,)], self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_executions"))
        self.assertEqual([(0,)], self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_rpc_intents"))

    def test_remaining_lifetime_includes_initial_spacing_wait(self):
        plan = self.fixture.plan()
        self.fixture.state.sql("UPDATE cloud_directory_plans SET expires_at=? WHERE id=?", (self.clock + 34, plan["id"]))
        self.fixture.state.sql("UPDATE cloud_drive_budget_pool SET last_started_at=?", (self.clock,))
        with self.assertRaisesRegex(CloudDirectoryError, "batch_exceeds_time_budget"):
            self.approve(plan["id"])
        self.assertEqual([(0,)], self.fixture.state.sql("SELECT COUNT(*) FROM cloud_directory_executions"))

    def test_permission_maintenance_and_mapping_changes_are_checked_before_approval(self):
        plan = self.fixture.plan()
        with self.assertRaises(ValueError):
            admission.approveDirectoryExecution(self.settings, plan["id"], requestId=str(uuid4()),
                expectedRevision=0, confirmMaintenance=1, confirmGlobalFence=True, auditContext=self.context)
        self.fixture.state.sql("UPDATE users SET permissions_json='[\"cloud-directory:plan\"]'")
        with self.assertRaisesRegex(CloudDirectoryError, "permission_denied"):
            self.approve(plan["id"])
        self.fixture.state.sql("UPDATE users SET permissions_json='[\"cloud-directory:plan\",\"cloud-directory:execute\"]'")
        changed = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, directoryMappingRevision="synthetic-v2"))
        with self.assertRaisesRegex(CloudDirectoryError, "policy_changed"):
            admission.approveDirectoryExecution(changed, plan["id"], requestId=str(uuid4()),
                expectedRevision=0, confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)

    def test_shared_gate_blocks_file_tasks_scans_inventory_and_read_admission(self):
        pending = self.fixture.state.job()
        token = self.approve()
        with inventoryDb(self.settings, write=True) as connection:
            self.assertTrue(directoryExecutionHeld(connection))
            with self.assertRaisesRegex(ValueError, "目录改名"):
                assertFileTaskSlotFree(connection, "rename_operation", -1)
        self.assertIsNone(claimScan(self.settings))
        with self.assertRaisesRegex(ValueError, "目录改名"):
            claimCloudInventoryJob(self.settings, pending)
        sessionId = self.fixture.state.sql("SELECT session_id FROM cloud_drive_inventory_jobs WHERE id=?", (pending,))[0][0]
        clientAdmission = CloudDriveAdmission(self.settings, sessionId, endpoint=fixtures.fixtures.ENDPOINT, rootPath=fixtures.fixtures.ROOT)
        with self.assertRaisesRegex(CloudBudgetError, "directory_execution_held"):
            with clientAdmission(time.monotonic() + 1):
                self.fail("不应取得只读RPC槽")
        admission.cancelUnstartedDirectoryExecution(self.settings, self.plan["id"], token, auditContext=self.context)
        with inventoryDb(self.settings) as connection:
            self.assertFalse(directoryExecutionHeld(connection))

    def test_observation_maintenance_and_c1_resume_cannot_clear_execution_freeze(self):
        from app.service.cloud_drive_budget_service import cloudBudgetStatus, resumeCloudBudget
        self.approve()
        jobId, entryId = self.plan["inventoryJobId"], self.plan["items"][0]["entryId"]
        revision = self.fixture.state.summary(jobId)["revision"]
        self.assertFalse(self.fixture.state.summary(jobId)["cacheClearAvailable"])
        with self.assertRaisesRegex(ValueError, "目录改名"):
            clearCloudInventoryCache(self.settings, jobId, expectedRevision=revision, confirmClear=True, auditContext=self.context)
        with self.assertRaisesRegex(ValueError, "目录改名"):
            setCloudInventoryKeep(self.settings, jobId, entryId, kept=True, expectedRevision=revision, auditContext=self.context)
        self.fixture.state.sql("UPDATE users SET permissions_json=?", (json.dumps(["settings:write", "scan:run", "cloud-directory:plan", "cloud-directory:execute"]),))
        status = cloudBudgetStatus(self.settings)
        self.assertTrue(status["directoryExecutionHeld"])
        with patch("app.service.cloud_drive_budget_service._wallNow", return_value=self.clock):
            with self.assertRaisesRegex(CloudBudgetError, "directory_execution_held"):
                resumeCloudBudget(self.settings, expectedRevision=status["revision"], confirmRemoteIdle=True, auditContext=self.context)

    def test_directory_claim_refuses_old_file_or_scan_work(self):
        plan = self.fixture.plan()
        self.fixture.state.sql("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES(1,'queued',100,1,'synthetic')")
        with self.assertRaisesRegex(CloudDirectoryError, "scan_slot_busy"):
            self.approve(plan["id"])
        self.fixture.state.sql("UPDATE scan_jobs SET status='completed'")
        self.fixture.state.sql("INSERT INTO rename_operations(status,mode,total_count,ready_count,conflict_count,renamed_count,failed_count,created_at,updated_at) VALUES('queued','dry_run',0,0,0,0,0,'synthetic','synthetic')")
        with self.assertRaisesRegex(ValueError, "文件写任务"):
            self.approve(plan["id"])

    def test_rpc_intent_is_durable_before_send_and_response_never_releases_slot(self):
        token = self.running()
        before = self.fixture.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions")
        rpcId = self.send(token)
        self.assertEqual([("sending", 1)], self.fixture.state.sql("SELECT phase,ordinal FROM cloud_directory_rpc_intents"))
        self.assertEqual([(1,)], self.fixture.state.sql("SELECT used_rpc FROM cloud_directory_executions"))
        self.assertEqual(before, self.fixture.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        with self.assertRaisesRegex(CloudDirectoryError, "rpc_unresolved"):
            self.send(token)
        admission.recordDirectoryRpcResponse(self.settings, self.plan["id"], token, rpcId, responseCode="ok", receivedBytes=64)
        self.assertEqual([("responded", 64)], self.fixture.state.sql("SELECT phase,received_bytes FROM cloud_directory_rpc_intents"))
        self.assertEqual([(1,)], self.fixture.state.sql("SELECT slot_held FROM cloud_directory_executions"))
        with self.assertRaisesRegex(CloudDirectoryError, "spacing_wait_required"):
            self.send(token)
        self.clock += 2
        self.send(token, "GetRuntimeInfo")

    def test_unknown_rpc_preserves_lease_budget_and_global_freeze(self):
        token = self.running()
        rpcId = self.send(token)
        admission.markDirectoryRpcUnknown(self.settings, self.plan["id"], token, rpcId)
        self.assertEqual([("review", 1, 1)], self.fixture.state.sql("SELECT status,slot_held,used_rpc FROM cloud_directory_executions"))
        self.assertEqual([("unknown",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents"))
        with self.assertRaisesRegex(CloudDirectoryError, "execution_not_active"):
            self.send(token)
        with self.assertRaisesRegex(CloudDirectoryError, "execution_requires_review"):
            admission.cancelUnstartedDirectoryExecution(self.settings, self.plan["id"], token, auditContext=self.context)
        self.assertEqual([("paused", "request_incomplete")], self.fixture.state.sql("SELECT state,pause_reason FROM cloud_drive_budget_pool"))

    def test_revocation_during_request_still_records_known_response_but_stops_next(self):
        token = self.running()
        rpcId = self.send(token)
        self.fixture.state.sql("UPDATE users SET permissions_json='[]'")
        admission.recordDirectoryRpcResponse(self.settings, self.plan["id"], token, rpcId, responseCode="ok", receivedBytes=0)
        self.clock += 2
        with self.assertRaisesRegex(CloudDirectoryError, "permission_denied"):
            self.send(token)
        self.assertEqual([("responded",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents"))

    def test_budget_deadline_methods_and_identity_cannot_be_bypassed(self):
        token = self.running()
        with self.assertRaisesRegex(CloudDirectoryError, "worker_ownership_lost"):
            self.send(str(uuid4()))
        for method in ("DeleteFile", "MoveFile", "GetSubFiles", "RenameFile"):
            with self.subTest(method=method), self.assertRaisesRegex(CloudDirectoryError, "rpc_not_allowed"):
                self.send(token, method)
        self.fixture.state.sql("UPDATE cloud_directory_executions SET used_rpc=max_rpc")
        with self.assertRaisesRegex(CloudDirectoryError, "execution_budget_exhausted"):
            self.send(token)
        self.clock += 181
        with self.assertRaisesRegex(CloudDirectoryError, "execution_not_active"):
            self.send(token)

    def test_byte_overflow_records_consumption_and_requires_review(self):
        token = self.running()
        rpcId = self.send(token)
        admission.recordDirectoryRpcResponse(self.settings, self.plan["id"], token, rpcId, responseCode="ok", receivedBytes=MAX_BYTES + 1)
        self.assertEqual([("review", MAX_BYTES + 1, 1)], self.fixture.state.sql("SELECT status,received_bytes,slot_held FROM cloud_directory_executions"))

    def test_schema43_upgrade_preserves_plan_and_default_users(self):
        plan = self.fixture.plan()
        before = self.fixture.state.sql("SELECT * FROM users")
        self.fixture.state.sql("DROP TABLE cloud_directory_rpc_intents")
        self.fixture.state.sql("DROP TABLE cloud_directory_executions")
        self.fixture.state.sql("UPDATE app_meta SET value='43' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(plan, self.fixture.get(plan["id"]))
        self.assertEqual(before, self.fixture.state.sql("SELECT * FROM users"))
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.fixture.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))


MAX_BYTES = admission.MAX_BYTES
