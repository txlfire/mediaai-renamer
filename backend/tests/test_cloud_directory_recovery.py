"""只读恢复仅连接本机TLS合成端点；原未知RPC与原消费不改写。"""

from dataclasses import replace
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cd2_directory_client as nativeFixtures
from app.service import cd2_directory_protocol as wire
from app.service.cd2_directory_recovery_client import Cd2DirectoryRecoveryClient
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_directory_recovery_service import (
    approveDirectoryRecovery, bindRecoveryWorker, reserveRecoveryRpc, commitRecoveryPositions, markRecoveryUnknown,
)
from app.service.cloud_directory_admission_service import markDirectoryRpcUnknown
from app.service.cloud_drive_budget_service import pauseCloudBudget
from app.service.cloud_directory_recovery_worker_service import executeDirectoryRecovery, finishRecoveryWorker
from app.service.cloud_directory_path_service import cloudPathEpoch
from app.service.cloud_directory_worker_service import finishDirectoryWorker
from app.service.cloud_inventory_service import inventoryDb
from app.core.database import ensure_database


class DirectoryRecoveryTest(unittest.TestCase):
    fixtureType = nativeFixtures.Cd2DirectoryClientTest

    def setUp(self):
        self.fixture = self.fixtureType()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings, self.plan = self.fixture.settings, self.fixture.plan
        self.sql = self.fixture.fixture.state.sql
        self.actorId = self.fixture.fixture.state.actor.id
        self.context = self.fixture.fixture.context
        self.fixture.permissions = {"allow_list": True, "allow_view_runtime_info": True}
        self.sql("UPDATE cloud_directory_executions SET worker_pid=NULL,status='review',reason='rpc_unknown',used_rpc=2")
        self.originalRevision = self.sql("SELECT revision FROM cloud_directory_executions")[0][0]
        now = time.time()
        self.sql("INSERT INTO cloud_directory_rpc_intents(id,plan_id,ordinal,method,phase,response_code,request_path,remote_id,sent_at,responded_at) VALUES(?,?,1,'FindFileByPath','responded','ok','/','synthetic-parent',?,?)", (str(uuid4()), self.plan["id"], now, now))
        self.sql("INSERT INTO cloud_directory_rpc_intents(id,plan_id,ordinal,method,item_ordinal,phase,request_path,sent_at) VALUES(?,?,2,'RenameFile',1,'unknown',?,?)", (str(uuid4()), self.plan["id"], "/" + self.plan["items"][0]["sourceName"], now))
        self.recoveryId, self.workerToken = None, None

    def approve(self, **changes):
        params = {"requestId": str(uuid4()), "expectedRevision": self.originalRevision, "confirmMaintenance": True,
            "confirmRemoteIdle": True, "auditContext": self.context}
        params.update(changes)
        self.recoveryId, self.workerToken = approveDirectoryRecovery(self.settings, self.plan["id"], **params)
        return self.recoveryId, self.workerToken

    def runRecovery(self):
        self.approve()
        bindRecoveryWorker(self.settings, self.recoveryId, self.workerToken, os.getpid())
        try:
            executeDirectoryRecovery(self.settings, self.recoveryId, self.workerToken, self.fixture.token)
        finally:
            # 只有本进程量子夹具替换退出探测；实际worker另行验证真实回收。
            with patch("app.service.cloud_directory_recovery_worker_service.isProcessAlive", return_value=False):
                finishRecoveryWorker(self.settings, self.recoveryId, self.workerToken, os.getpid())

    def commit(self):
        revision = self.sql("SELECT revision FROM cloud_directory_recoveries WHERE id=?", (self.recoveryId,))[0][0]
        return commitRecoveryPositions(self.settings, self.recoveryId, expectedRevision=revision,
            confirmCurrentPositions=True, actorId=self.actorId)

    def test_target_position_can_be_synced_without_turning_original_unknown_into_success(self):
        self.fixture.renamed = True
        self.runRecovery()
        self.assertEqual([("ready", 6, 0, None)], self.sql("SELECT status,used_rpc,slot_held,worker_pid FROM cloud_directory_recoveries"))
        self.assertEqual([("at_target",)], self.sql("SELECT position FROM cloud_directory_recovery_items"))
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))
        calls = len(self.fixture.calls)
        self.assertEqual(1, self.commit())
        self.assertEqual(calls, len(self.fixture.calls))
        self.assertFalse(any(method == "rename" for method, _ in self.fixture.calls))
        self.assertEqual([("unknown", None, self.recoveryId)], self.sql("SELECT phase,response_code,reviewed_by_recovery FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        self.assertEqual([("review", "positions_reconciled", 0, 2)], self.sql("SELECT status,reason,slot_held,used_rpc FROM cloud_directory_executions"))
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.assertEqual(1, self.commit())
        self.assertNotIn(self.fixture.token, str(self.sql("SELECT * FROM cloud_directory_recoveries")))
        ensure_database(self.settings)
        self.assertEqual([("unknown", self.recoveryId)], self.sql("SELECT phase,reviewed_by_recovery FROM cloud_directory_rpc_intents WHERE ordinal=2"))

    def test_source_position_invalidates_old_plans_but_does_not_claim_original_write_not_sent(self):
        self.runRecovery()
        self.assertEqual([("at_source",)], self.sql("SELECT position FROM cloud_directory_recovery_items"))
        self.commit()
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM cloud_directory_path_revisions"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        with inventoryDb(self.settings) as connection:
            self.assertEqual(1, cloudPathEpoch(connection)["revision"])

    def test_conflicting_or_changed_target_never_releases_original_freeze(self):
        self.fixture.renamed, self.fixture.replaceTarget = True, True
        self.runRecovery()
        self.assertEqual([("ambiguous",)], self.sql("SELECT position FROM cloud_directory_recovery_items"))
        with self.assertRaisesRegex(CloudDirectoryError, "recovery_not_ready"):
            self.commit()
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_disabled_write_switch_still_permits_strictly_readonly_recovery(self):
        self.settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, directoryRenameEnabled=False))
        self.runRecovery()
        self.assertEqual([("ready",)], self.sql("SELECT status FROM cloud_directory_recoveries"))
        self.commit()
        self.assertEqual([(0,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_home_readonly_recovery_works_with_write_switch_disabled(self):
        self.fixture.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_disabled_write_switch_still_permits_strictly_readonly_recovery()
        self.assertFalse(any(method == "rename" for method, _ in self.fixture.calls))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE ordinal=2"))

    def test_mixed_pair_cannot_be_accepted_by_readonly_recovery(self):
        self.fixture.version = ("1.1.2 Build 26-10-06 23:48:14", "1.1.3 Build 26-10-17 17:13:00")
        with self.assertRaisesRegex(CloudDirectoryError, "runtime_not_verified"):
            self.runRecovery()
        self.assertEqual(["token", "runtime"], [method for method, _ in self.fixture.calls])
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_recovery_rpcs WHERE method='GetRuntimeInfo'"))

    def test_recovery_transport_budget_and_token_all_reject_rename(self):
        self.approve()
        bindRecoveryWorker(self.settings, self.recoveryId, self.workerToken, os.getpid())
        with Cd2DirectoryRecoveryClient(self.settings, self.recoveryId, self.workerToken, self.fixture.token) as client:
            with self.assertRaisesRegex(CloudDirectoryError, "rpc_not_allowed"):
                client.renameItem(1, time.monotonic() + 5)
            with self.assertRaisesRegex(CloudDirectoryError, "rpc_not_allowed"):
                client._call("RenameFile", wire.RenameFileRequest(), wire.FileOperationResult, time.monotonic() + 5)
        with self.assertRaisesRegex(CloudDirectoryError, "rpc_not_allowed"):
            reserveRecoveryRpc(self.settings, self.recoveryId, self.workerToken, "RenameFile", itemOrdinal=1, requestPath="/")
        self.assertEqual([], self.fixture.calls)
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_directory_recoveries"))
        self.fixture.permissions["allow_rename"] = True
        with self.assertRaisesRegex(CloudDirectoryError, "token_scope_or_permissions_invalid"):
            executeDirectoryRecovery(self.settings, self.recoveryId, self.workerToken, self.fixture.token)
        self.assertEqual(["token"], [method for method, _ in self.fixture.calls])
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_recovery_rpcs"))

    def test_requires_explicit_idle_confirmations_dead_old_worker_and_cooldown(self):
        for change in ({"confirmMaintenance": False}, {"confirmRemoteIdle": False}):
            with self.assertRaises(ValueError):
                self.approve(**change)
        self.sql("UPDATE cloud_directory_executions SET worker_pid=?", (os.getpid(),))
        with self.assertRaisesRegex(CloudDirectoryError, "worker_exit_unconfirmed"):
            self.approve()
        self.sql("UPDATE cloud_directory_executions SET worker_pid=NULL")
        self.sql("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='request_incomplete',cooldown_until=?", (time.time() + 30,))
        with self.assertRaisesRegex(CloudDirectoryError, "pool_not_ready"):
            self.approve()
        self.sql("UPDATE cloud_drive_budget_pool SET cooldown_until=0,pause_reason='manual_pause'")
        with self.assertRaisesRegex(CloudDirectoryError, "pool_not_ready"):
            self.approve()
        self.sql("UPDATE cloud_drive_budget_pool SET pause_reason='request_incomplete'")
        self.approve()
        self.assertEqual([("ready",)], self.sql("SELECT state FROM cloud_drive_budget_pool"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE ordinal=2"))

    def test_stale_worker_pid_or_accepted_owner_cannot_be_treated_as_finished(self):
        self.sql("UPDATE cloud_directory_executions SET worker_pid=2147483646")
        with patch("app.service.cloud_directory_recovery_service.isProcessAlive", side_effect=[False, True]):
            with self.assertRaisesRegex(CloudDirectoryError, "worker_exit_unconfirmed"):
                self.approve()
        self.sql("UPDATE cloud_directory_executions SET worker_pid=NULL,status='accepted'")
        with self.assertRaisesRegex(CloudDirectoryError, "worker_exit_unconfirmed"):
            self.approve()

    def grantBudgetPause(self):
        permissions = json.loads(self.sql("SELECT permissions_json FROM users WHERE id=?", (self.actorId,))[0][0])
        self.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps([*permissions, "settings:write"]), self.actorId))

    def test_manual_pause_survives_late_recovery_unknown_and_worker_reap(self):
        self.approve()
        bindRecoveryWorker(self.settings, self.recoveryId, self.workerToken, os.getpid())
        rpcId = reserveRecoveryRpc(self.settings, self.recoveryId, self.workerToken, "GetRuntimeInfo")
        self.grantBudgetPause()
        pauseCloudBudget(self.settings, auditContext=self.context)
        markRecoveryUnknown(self.settings, self.recoveryId, self.workerToken, rpcId)
        self.assertEqual([("manual_pause",)], self.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))
        with patch("app.service.cloud_directory_recovery_worker_service.isProcessAlive", return_value=False):
            finishRecoveryWorker(self.settings, self.recoveryId, self.workerToken, os.getpid())
        self.assertEqual([("paused", "manual_pause")], self.sql("SELECT state,pause_reason FROM cloud_drive_budget_pool"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_recovery_rpcs"))

    def test_manual_pause_survives_late_original_unknown_and_worker_reap(self):
        self.sql("UPDATE cloud_directory_executions SET worker_pid=?", (os.getpid(),))
        self.sql("UPDATE cloud_directory_rpc_intents SET phase='sending' WHERE ordinal=2")
        rpcId = self.sql("SELECT id FROM cloud_directory_rpc_intents WHERE ordinal=2")[0][0]
        self.grantBudgetPause()
        pauseCloudBudget(self.settings, auditContext=self.context)
        markDirectoryRpcUnknown(self.settings, self.plan["id"], self.fixture.workerToken, rpcId)
        self.assertEqual([("manual_pause",)], self.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))
        with patch("app.service.cloud_directory_worker_service.isProcessAlive", return_value=False):
            finishDirectoryWorker(self.settings, self.plan["id"], self.fixture.workerToken, os.getpid())
        self.assertEqual([("paused", "manual_pause")], self.sql("SELECT state,pause_reason FROM cloud_drive_budget_pool"))
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_late_original_supervisor_cannot_reclaim_or_rewrite_recovery_owned_state(self):
        self.approve()
        before = self.sql("SELECT status,reason,revision,slot_held FROM cloud_directory_executions")
        with self.assertRaisesRegex(CloudDirectoryError, "worker_ownership_lost"):
            finishDirectoryWorker(self.settings, self.plan["id"], self.fixture.workerToken, None)
        self.assertEqual(before, self.sql("SELECT status,reason,revision,slot_held FROM cloud_directory_executions"))

    def test_recovery_attempts_are_separate_capped_and_no_startup_replay(self):
        requestIds = []
        for attempt in range(1, 6):
            requestId = str(uuid4())
            self.approve(requestId=requestId)
            requestIds.append(requestId)
            self.assertEqual([(attempt, 0, 6)], self.sql("SELECT attempt,used_rpc,max_rpc FROM cloud_directory_recoveries WHERE id=?", (self.recoveryId,)))
            with self.assertRaisesRegex(CloudDirectoryError, "recovery_already_accepted"):
                self.approve(requestId=requestId)
            finishRecoveryWorker(self.settings, self.recoveryId, self.workerToken, None)
        with self.assertRaisesRegex(CloudDirectoryError, "recovery_attempt_limit"):
            self.approve()
        self.assertEqual(5, len(set(requestIds)))
        self.assertEqual([], self.fixture.calls)
        self.assertEqual([(1,)], self.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_migration_keeps_unreviewed_unknown_as_global_send_block(self):
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual([("unknown", None)], self.sql("SELECT phase,reviewed_by_recovery FROM cloud_directory_rpc_intents WHERE ordinal=2"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("INSERT INTO cloud_directory_rpc_intents(id,plan_id,ordinal,method,phase,sent_at) VALUES(?,?,3,'FindFileByPath','sending',?)", (str(uuid4()), self.plan["id"], time.time()))
