"""路径世代仅改变小型修订/版本水位，不自动扫描或重写历史路径。"""

from dataclasses import replace
import json
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_rename_queue as legacyFixtures
import test_cloud_inventory as inventoryFixtures
from app.core.config import CloudDriveSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cloud_directory_admission_service import approveDirectoryExecution
from app.service.cloud_directory_path_service import cloudPathEpoch, commitDirectoryPathRevision, requireFreshFileTask, requireFreshScan
from app.service.cloud_directory_plan_service import createDirectoryPlan, CloudDirectoryError
from app.service.cloud_directory_recovery_service import approveDirectoryRecovery
from app.service.cloud_directory_probe_service import directoryProbeWindow
from app.service.cloud_directory_probe_service import inspectDirectoryProbes, releaseDirectoryProbeAfterReview
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache
from app.service.cloud_drive_budget_service import pauseCloudBudget, cloudBudgetStatus, CloudBudgetError, releaseDirectoryManualPause
from app.service.preview_service import _assertPreviewEditable, generate_rename_previews, list_rename_previews
from app.service.rename_queue_service import acceptRename
from app.service.scan_service import run_full_scan


class CloudDirectoryPathTest(unittest.TestCase):
    def setUp(self):
        self.legacy = legacyFixtures.RenameQueueTest()
        self.legacy.setUp()
        self.addCleanup(self.legacy.doCleanups)
        self.settings = replace(self.legacy.settings, cloudDrive=CloudDriveSettings(inventoryFixtures.ROOT,
            endpoint=inventoryFixtures.ENDPOINT, directoryRenameEnabled=True, directoryMappingRevision="synthetic-v1"))
        self.actor = create_user(self.settings, "cloud-operator", "synthetic", "TestOnly123!",
            ["scan:run", "rename:execute", "cloud-directory:plan", "cloud-directory:execute"], True)
        self.context = AuditWriteContext(self.actor)
        self.state = inventoryFixtures.CloudInventoryTest()
        self.state.settings, self.state.actor, self.state.context = self.settings, self.actor, self.context
        self.jobId = self.state.job()
        self.state.runJob(self.jobId, inventoryFixtures.SyntheticClient({inventoryFixtures.ROOT:
            [inventoryFixtures.entry("[论坛推广] 示例 (2024)", directory=True)]}))
        self.entryId = self.state.sql("SELECT id FROM cloud_drive_inventory_entries WHERE job_id=?", (self.jobId,))[0][0]
        self.plan = createDirectoryPlan(self.settings, requestId=str(uuid4()), jobId=self.jobId,
            expectedRevision=self.state.summary(self.jobId)["revision"], entryIds=[self.entryId],
            folderTerms=["论坛推广"], auditContext=self.context)

    def seedVerified(self):
        token = approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
        rpcId = str(uuid4())
        now = time.time()
        # 单元夹具模拟已由连接器核验的持久化终态；真实线协议另有合成TLS/子进程测试。
        self.state.sql("UPDATE cloud_directory_executions SET status='review',reason='awaiting_path_sync',used_rpc=1 WHERE plan_id=?", (self.plan["id"],))
        self.state.sql("INSERT INTO cloud_directory_rpc_intents(id,plan_id,ordinal,method,item_ordinal,phase,response_code,sent_at,responded_at,request_path) VALUES(?,?,1,'RenameFile',1,'responded','ok',?,?,?)",
            (rpcId, self.plan["id"], now, now, "/" + self.plan["items"][0]["sourceName"]))
        self.state.sql("INSERT INTO cloud_directory_item_outcomes(plan_id,ordinal,state,source_remote_id,parent_remote_id,precheck_json,postcheck_json,rename_rpc_id,remote_reply,created_at,verified_at) VALUES(?,1,'verified',?,'synthetic-parent','[]','[]',?,1,?,?)",
            (self.plan["id"], self.plan["items"][0]["remoteId"], rpcId, now, now))
        return token

    def commit(self):
        return commitDirectoryPathRevision(self.settings, self.plan["id"], actorId=self.actor.id)

    def test_atomic_path_revision_keeps_history_and_consumption_and_releases_only_after_commit(self):
        oldPaths = self.state.sql("SELECT file_path FROM media_files")
        oldItems = self.state.sql("SELECT * FROM rename_operation_items")
        oldBudget = self.state.sql("SELECT id,max_rpc,used_rpc,root_path FROM cloud_drive_read_sessions")
        self.seedVerified()
        self.assertEqual(1, self.commit())
        self.assertEqual(oldPaths, self.state.sql("SELECT file_path FROM media_files"))
        self.assertEqual(oldItems, self.state.sql("SELECT * FROM rename_operation_items"))
        self.assertEqual(oldBudget, self.state.sql("SELECT id,max_rpc,used_rpc,root_path FROM cloud_drive_read_sessions"))
        self.assertEqual([("completed", 0)], self.state.sql("SELECT status,slot_held FROM cloud_directory_executions"))
        self.assertEqual([("committed",)], self.state.sql("SELECT state FROM cloud_directory_item_outcomes"))
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.assertFalse(self.state.summary(self.jobId)["pathCurrent"])
        self.assertEqual(1, self.commit())
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))

    def test_old_previews_and_plans_are_blocked_but_new_selected_scan_is_allowed(self):
        self.seedVerified()
        self.commit()
        with self.assertRaisesRegex(ValueError, "旧动作"):
            acceptRename(self.settings, self.legacy.operation.id, self.context)
        with sqlite3.connect(self.settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            with self.assertRaisesRegex(ValueError, "重新扫描"):
                _assertPreviewEditable(connection, self.legacy.previews[0].id)
        job = run_full_scan(self.settings, self.legacy.source.id)
        generate_rename_previews(self.settings, scan_job_id=job.id)
        self.assertTrue(list_rename_previews(self.settings, scan_job_id=job.id))
        with sqlite3.connect(self.settings.database_path) as connection:
            requireFreshScan(connection, job.id)

    def test_stale_inventory_cannot_create_new_plan_even_with_current_revision(self):
        self.seedVerified()
        self.commit()
        with self.assertRaisesRegex(CloudDirectoryError, "path_revision_changed"):
            createDirectoryPlan(self.settings, requestId=str(uuid4()), jobId=self.jobId,
                expectedRevision=self.state.summary(self.jobId)["revision"], entryIds=[self.entryId],
                folderTerms=["论坛推广"], auditContext=self.context)
        clearCloudInventoryCache(self.settings, self.jobId, confirmClear=True,
            expectedRevision=self.state.summary(self.jobId)["revision"], auditContext=self.context)
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_item_outcomes"))

    def test_audit_failure_rolls_back_local_commit_and_preserves_freeze(self):
        self.seedVerified()
        with patch("app.service.cloud_directory_admission_service._audit", side_effect=RuntimeError("synthetic-audit-failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-audit-failure"):
                self.commit()
        self.assertEqual([(0,)], self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_directory_executions"))
        self.assertEqual([("verified",)], self.state.sql("SELECT state FROM cloud_directory_item_outcomes"))

    def test_unknown_or_live_worker_never_releases_path_freeze(self):
        self.seedVerified()
        self.state.sql("UPDATE cloud_directory_executions SET worker_pid=12345")
        with self.assertRaisesRegex(CloudDirectoryError, "path_commit_requires_review"):
            self.commit()
        self.state.sql("UPDATE cloud_directory_executions SET worker_pid=NULL")
        self.state.sql("UPDATE cloud_directory_rpc_intents SET phase='unknown'")
        with self.assertRaisesRegex(CloudDirectoryError, "path_commit_requires_review"):
            self.commit()

    def test_recovery_handoff_blocks_normal_commit_in_every_recovery_state(self):
        self.seedVerified()
        recoveryId, _ = approveDirectoryRecovery(self.settings, self.plan["id"], requestId=str(uuid4()),
            expectedRevision=0, confirmMaintenance=True, confirmRemoteIdle=True, auditContext=self.context)
        for status, reason, held in (("accepted", None, 1), ("running", None, 1),
                                    ("review", "ambiguous_position", 0), ("review", "rpc_unknown", 0)):
            with self.subTest(status=status, reason=reason):
                self.state.sql("UPDATE cloud_directory_recoveries SET status=?,reason=?,slot_held=? WHERE id=?",
                               (status, reason, held, recoveryId))
                with patch("app.service.cloud_directory_admission_service._audit") as audit:
                    with self.assertRaisesRegex(CloudDirectoryError, "path_commit_requires_review"):
                        self.commit()
                    audit.assert_not_called()
                self.assertEqual([(0,)], self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))
                self.assertEqual([("review", 1)], self.state.sql("SELECT status,slot_held FROM cloud_directory_executions"))
                self.assertEqual([("verified",)], self.state.sql("SELECT state FROM cloud_directory_item_outcomes"))
                self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))

        # 即使旧执行被错误标成完成，普通幂等分支也不能绕过恢复专用提交。
        self.state.sql("UPDATE cloud_directory_executions SET status='completed',slot_held=0")
        with self.assertRaisesRegex(CloudDirectoryError, "path_commit_requires_review"):
            self.commit()

    def test_manual_pause_after_unknown_still_blocks_recovery_approval(self):
        self.seedVerified()
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?",
            (json.dumps([*self.actor.permissions, "settings:write"]), self.actor.id))
        self.state.sql("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='request_incomplete',cooldown_until=0")
        pauseCloudBudget(self.settings, auditContext=self.context)
        with self.assertRaisesRegex(CloudDirectoryError, "pool_not_ready"):
            approveDirectoryRecovery(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
                confirmMaintenance=True, confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual([("paused", "manual_pause")], self.state.sql("SELECT state,pause_reason FROM cloud_drive_budget_pool"))
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_recoveries"))

    def test_sync_scan_does_not_probe_protocol_while_directory_execution_is_held(self):
        self.seedVerified()
        with patch("app.service.scan_service.get_protocol") as protocol:
            with self.assertRaises(ValueError):
                run_full_scan(self.settings, self.legacy.source.id)
            protocol.assert_not_called()
        self.assertEqual([], inspectDirectoryProbes(self.settings))

    def pauseDirectoryBudget(self):
        self.seedVerified()
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?",
            (json.dumps([*self.actor.permissions, "settings:write"]), self.actor.id))
        self.state.sql("UPDATE cloud_directory_rpc_intents SET phase='unknown'")
        return pauseCloudBudget(self.settings, auditContext=self.context)

    def releaseManualPause(self, **changes):
        params = {"expectedRevision": cloudBudgetStatus(self.settings)["revision"],
            "confirmClientsStopped": True, "confirmRemoteIdle": True, "auditContext": self.context}
        params.update(changes)
        return releaseDirectoryManualPause(self.settings, **params)

    def test_explicit_manual_pause_release_preserves_freeze_and_requires_new_recovery(self):
        self.pauseDirectoryBudget()
        original = self.state.sql("SELECT * FROM cloud_directory_executions")
        unknown = self.state.sql("SELECT * FROM cloud_directory_rpc_intents")
        result = self.releaseManualPause()
        self.assertEqual(("paused", "request_incomplete", True),
            (result["state"], result["pauseReason"], result["directoryExecutionHeld"]))
        self.assertEqual(original, self.state.sql("SELECT * FROM cloud_directory_executions"))
        self.assertEqual(unknown, self.state.sql("SELECT * FROM cloud_directory_rpc_intents"))
        self.assertEqual([(0,)], self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))
        approveDirectoryRecovery(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_directory_executions"))
        self.assertEqual([("unknown",)], self.state.sql("SELECT phase FROM cloud_directory_rpc_intents"))
        self.assertEqual([(0,)], self.state.sql("SELECT used_rpc FROM cloud_directory_recoveries"))

    def test_manual_pause_release_rejects_missing_confirmation_live_slots_and_policy_barriers(self):
        status = self.pauseDirectoryBudget()
        for field in ("confirmClientsStopped", "confirmRemoteIdle"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.releaseManualPause(**{field: False})
        with self.assertRaisesRegex(CloudBudgetError, "revision_conflict"):
            self.releaseManualPause(expectedRevision=status["revision"] - 1)
        for sql, args in (("UPDATE cloud_directory_executions SET worker_pid=12345", ()),
                          ("UPDATE cloud_directory_executions SET worker_pid=NULL,status='running'", ()),
                          ("UPDATE cloud_directory_executions SET status='review',owner_host=?", ("0" * 64,))):
            self.state.sql(sql, args)
            with self.assertRaisesRegex(CloudBudgetError, "directory_clients_require_review"):
                self.releaseManualPause()
        from app.service.cloud_drive_budget_service import cloudDriveHostKey
        self.state.sql("UPDATE cloud_directory_executions SET owner_host=?", (cloudDriveHostKey(),))
        self.state.sql("UPDATE cloud_drive_budget_pool SET cooldown_until=?", (time.time() + 120,))
        with self.assertRaisesRegex(CloudBudgetError, "cooldown_not_finished"):
            self.releaseManualPause()
        self.assertEqual([("manual_pause",)], self.state.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))

    def test_manual_pause_release_audit_failure_rolls_back_and_permissions_are_current(self):
        self.pauseDirectoryBudget()
        with patch("app.service.cloud_drive_budget_service._audit", side_effect=RuntimeError("synthetic-audit")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-audit"):
                self.releaseManualPause()
        self.assertEqual([("manual_pause",)], self.state.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))
        self.state.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.actor.id,))
        with self.assertRaisesRegex(CloudBudgetError, "actor_permission_denied"):
            self.releaseManualPause()

    def test_manual_pause_release_rejects_recovery_worker_and_unfinished_send(self):
        self.pauseDirectoryBudget()
        self.state.sql("UPDATE cloud_directory_rpc_intents SET phase='sending'")
        with self.assertRaisesRegex(CloudBudgetError, "directory_clients_require_review"):
            self.releaseManualPause()
        self.state.sql("UPDATE cloud_directory_rpc_intents SET phase='unknown'")
        self.state.sql("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL")
        recoveryId, _ = approveDirectoryRecovery(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmRemoteIdle=True, auditContext=self.context)
        pauseCloudBudget(self.settings, auditContext=self.context)
        with self.assertRaisesRegex(CloudBudgetError, "directory_clients_require_review"):
            self.releaseManualPause()
        self.state.sql("UPDATE cloud_directory_recoveries SET slot_held=0,status='review' WHERE id=?", (recoveryId,))
        self.state.sql("UPDATE cloud_drive_budget_pool SET observed_at=?", (time.time() + 120,))
        with self.assertRaisesRegex(CloudBudgetError, "clock_rollback"):
            self.releaseManualPause()
        self.assertEqual([("manual_pause",)], self.state.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))

    def test_manual_pause_release_rejects_recovery_pid_without_slot_and_retains_c1_lease(self):
        self.pauseDirectoryBudget()
        self.state.sql("UPDATE cloud_drive_budget_pool SET state='ready',pause_reason=NULL")
        recoveryId, _ = approveDirectoryRecovery(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmRemoteIdle=True, auditContext=self.context)
        pauseCloudBudget(self.settings, auditContext=self.context)
        self.state.sql("UPDATE cloud_directory_recoveries SET slot_held=0,worker_pid=12345,status='review' WHERE id=?", (recoveryId,))
        with self.assertRaisesRegex(CloudBudgetError, "directory_clients_require_review"):
            self.releaseManualPause()
        self.state.sql("UPDATE cloud_directory_recoveries SET worker_pid=NULL WHERE id=?", (recoveryId,))
        now = time.time()
        self.state.sql("INSERT INTO cloud_directory_recovery_rpcs(id,recovery_id,ordinal,method,phase,sent_at) VALUES(?,?,1,'GetRuntimeInfo','sending',?)",
                       (str(uuid4()), recoveryId, now))
        with self.assertRaisesRegex(CloudBudgetError, "directory_clients_require_review"):
            self.releaseManualPause()
        self.state.sql("UPDATE cloud_directory_recovery_rpcs SET phase='unknown'")
        sessionId = self.state.sql("SELECT id FROM cloud_drive_read_sessions LIMIT 1")[0][0]
        from app.service.cloud_drive_budget_service import cloudDriveHostKey
        leaseId = str(uuid4())
        self.state.sql("INSERT INTO cloud_drive_rpc_lease(id,pool_id,session_id,owner_instance,owner_pid,owner_host,phase,created_at) VALUES(?,1,?,?,?,?, 'unknown',?)",
            (leaseId, sessionId, str(uuid4()), 12345, cloudDriveHostKey(), now))
        with self.assertRaisesRegex(CloudBudgetError, "lease_requires_local_review"):
            self.releaseManualPause()
        self.assertEqual([(leaseId, "unknown")], self.state.sql("SELECT id,phase FROM cloud_drive_rpc_lease"))
        self.assertEqual([("manual_pause",)], self.state.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))

    def test_sync_scan_holds_probe_lease_before_io_and_releases_on_error(self):
        def checkReady(*args):
            self.assertEqual([("scan",)], self.state.sql("SELECT kind FROM cloud_directory_probe_leases"))
            with self.assertRaisesRegex(CloudDirectoryError, "file_probe_unresolved"):
                approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
                    confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
            raise RuntimeError("synthetic-probe-error")
        with patch("app.service.scan_service.get_protocol") as protocol:
            protocol.return_value.check_scan_ready.side_effect = checkReady
            with self.assertRaisesRegex(RuntimeError, "synthetic-probe-error"):
                run_full_scan(self.settings, self.legacy.source.id)
        self.assertEqual([], inspectDirectoryProbes(self.settings))

    def test_sync_probe_lease_blocks_directory_claim_and_is_removed_only_on_return(self):
        with directoryProbeWindow(self.settings, "rename_dry_run"):
            self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_probe_leases"))
            with self.assertRaisesRegex(CloudDirectoryError, "file_probe_unresolved"):
                approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
                    confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_probe_leases"))

    def test_crashed_scan_probe_keeps_lease_until_explicit_process_and_remote_review(self):
        code = ("import os,sys; from pathlib import Path; from app.core.config import AppSettings; "
                "from app.service.cloud_directory_probe_service import directoryProbeWindow; "
                "p=Path(sys.argv[1]); s=AppSettings(data_dir=p.parent,database_path=p); "
                "window=directoryProbeWindow(s,'scan'); window.__enter__(); os._exit(0)")
        subprocess.run([sys.executable, "-c", code, str(self.settings.database_path)], check=True, timeout=15)
        leases = inspectDirectoryProbes(self.settings)
        self.assertEqual(1, len(leases))
        self.assertEqual("scan", leases[0]["kind"])
        with self.assertRaisesRegex(CloudDirectoryError, "file_probe_unresolved"):
            approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
                confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
        with self.assertRaises(ValueError):
            releaseDirectoryProbeAfterReview(self.settings, leases[0]["id"], confirmProcessStopped=True, confirmRemoteIdle=False)
        releaseDirectoryProbeAfterReview(self.settings, leases[0]["id"], confirmProcessStopped=True, confirmRemoteIdle=True)
        self.assertEqual([], inspectDirectoryProbes(self.settings))

    def test_schema46_upgrade_preserves_earlier_plan_and_default_epoch(self):
        for table in ("cloud_directory_path_revisions", "cloud_directory_path_epoch", "cloud_directory_probe_leases"):
            self.state.sql("DROP TABLE " + table)
        self.state.sql("ALTER TABLE cloud_drive_inventory_jobs DROP COLUMN path_epoch")
        self.state.sql("UPDATE app_meta SET value='46' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))
        self.assertEqual([(0,)], self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_plans"))

    def test_probe_review_requires_dead_local_process_and_explicit_remote_idle(self):
        leaseId = str(uuid4())
        from app.service.cloud_drive_budget_service import cloudDriveHostKey
        self.state.sql("INSERT INTO cloud_directory_probe_leases(id,kind,owner_pid,owner_host,created_at) VALUES(?,'rename_dry_run',12345,?,?)",
                       (leaseId, cloudDriveHostKey(), time.time()))
        self.assertEqual(1, len(inspectDirectoryProbes(self.settings)))
        with self.assertRaises(ValueError):
            releaseDirectoryProbeAfterReview(self.settings, leaseId, confirmProcessStopped=True, confirmRemoteIdle=False)
        with patch("app.service.cloud_directory_probe_service.isProcessAlive", return_value=True):
            with self.assertRaises(ValueError):
                releaseDirectoryProbeAfterReview(self.settings, leaseId, confirmProcessStopped=True, confirmRemoteIdle=True)
        with patch("app.service.cloud_directory_probe_service.isProcessAlive", return_value=False):
            releaseDirectoryProbeAfterReview(self.settings, leaseId, confirmProcessStopped=True, confirmRemoteIdle=True)
        self.assertEqual([], inspectDirectoryProbes(self.settings))
