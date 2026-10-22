"""隔离只读位置核对、本地同步及反向新计划；不发送真实云请求。"""

from dataclasses import replace
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_quarantine_execution as fixtures
from app.service import cloud_quarantine_execution_service as actions
from app.service import cloud_quarantine_recovery_service as recovery
from app.service import cloud_quarantine_plan_service as plans
from app.service import cloud_quarantine_item_service as items
from app.service.cloud_quarantine_worker_service import finishQuarantineWorker
from app.service.cloud_drive_budget_service import releaseDirectoryManualPause
from app.service.cloud_inventory_service import CloudInventoryError
from app.service.operation_log_service import record_operation_log
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user


class QuarantineRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuarantineExecutionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.plan = self.fixture.fixture.create(entryIds=self.fixture.fixture.ids[:1])
        self.settings, self.state = self.fixture.settings, self.fixture.state
        self.actor, self.context, self.plan = self.fixture.actor, self.fixture.context, self.fixture.plan

    def revision(self, runId):
        return actions.getQuarantineRun(self.settings, runId, self.actor.id)["revision"]

    def unknown(self):
        runId, token = self.fixture.bound()
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "GetApiTokenInfo")
        actions.markQuarantineUnknown(self.settings, runId, token, rpcId)
        self.state.sql("UPDATE cloud_quarantine_runs SET worker_pid=NULL WHERE id=?", (runId,))
        self.state.sql("UPDATE cloud_drive_budget_pool SET cooldown_until=0")
        return runId, token

    def observe(self, runId, **changes):
        values = dict(requestId=str(uuid4()), expectedRevision=self.revision(runId), confirmMaintenance=True,
            confirmRemoteIdle=True, auditContext=self.context)
        values.update(changes)
        return recovery.approveQuarantineObservation(self.settings, runId, **values)

    def observations(self, observeId, token, *, atTarget=False, conflict=False):
        actions.bindQuarantineWorker(self.settings, observeId, token, os.getpid())
        rpc = self.fixture.rpc
        identity = self.fixture.identity
        folder = "/"+self.plan["targetName"]
        item, source, target, _ = actions.itemPaths(self.plan, 1)
        rpc(observeId, token, "GetApiTokenInfo")
        rpc(observeId, token, "GetRuntimeInfo")
        rpc(observeId, token, "FindFileByPath", "/", identity=identity("/", "parent-id"))
        rpc(observeId, token, "FindFileByPath", folder, identity=identity(folder, self.plan["targetRemoteId"]))
        rpc(observeId, token, "FindFileByPath", source, 1, code="not_found" if atTarget else "ok",
            identity=None if atTarget else identity(source, item["remoteId"], directory=False, size=item["size"]))
        rpc(observeId, token, "FindFileByPath", target, 1, code="ok" if atTarget or conflict else "not_found",
            identity=identity(target, item["remoteId"] if atTarget else "unrelated", directory=False, size=item["size"]) if atTarget or conflict else None)
        rpc(observeId, token, "FindFileByPath", "/", identity=identity("/", "parent-id"))
        rpc(observeId, token, "FindFileByPath", folder, identity=identity(folder, self.plan["targetRemoteId"]))
        self.assertTrue(recovery.verifyQuarantinePositions(self.settings, observeId, token))
        self.state.sql("UPDATE cloud_quarantine_runs SET worker_pid=NULL WHERE id=?", (observeId,))
        finishQuarantineWorker(self.settings, observeId, token, None)

    def test_new_read_budget_preserves_unknown_and_denies_move_and_old_commit(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        actions.bindQuarantineWorker(self.settings, observeId, token, os.getpid())
        with self.assertRaisesRegex(plans.QuarantineError, "rpc_not_allowed"):
            actions.reserveQuarantineRpc(self.settings, observeId, token, "MoveFile", itemOrdinal=1, requestPath="/x")
        with self.assertRaisesRegex(plans.QuarantineError, "path_commit_requires_review"):
            recovery.commitQuarantineLocal(self.settings, runId, expectedRevision=self.revision(runId), confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual([("unknown", None)], self.state.sql("SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))

    def test_observation_commit_releases_only_local_fence_preserving_unknown_fact(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        self.observations(observeId, token, atTarget=True)
        epoch = recovery.commitQuarantineObservation(self.settings, observeId, expectedRevision=self.revision(observeId),
            confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual(1, epoch)
        self.assertEqual([("unknown", observeId)], self.state.sql("SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))
        self.assertEqual([(0,)], self.state.sql("SELECT SUM(slot_held) FROM cloud_quarantine_runs"))
        self.assertEqual(1, recovery.commitQuarantineObservation(self.settings, observeId, expectedRevision=self.revision(observeId),
            confirmLocalOnly=True, actorId=self.actor.id))

    def test_real_sqlite_full_cannot_commit_observer_after_original_transaction_rolled_back(self):
        runId,_=self.unknown()
        observeId,token=self.observe(runId)
        self.observations(observeId,token,atTarget=True)
        before=self.state.sql("SELECT id,slot_held,committed_epoch,revision FROM cloud_quarantine_runs ORDER BY id")
        auditBefore=self.state.sql("SELECT COUNT(*) FROM audit_events")
        codes=[]

        class WatchedConnection:
            def __init__(self,connection):
                self.connection=connection
            def __getattr__(self,name):
                return getattr(self.connection,name)
            def execute(self,*args):
                try:
                    return self.connection.execute(*args)
                except sqlite3.Error as exc:
                    codes.append(getattr(exc,"sqlite_errorcode",None))
                    raise

        def exhaust(settings,**values):
            connection=values["connection"]
            pages=connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={pages}")
            values["connection"]=WatchedConnection(connection)
            values["message"]="x"*(connection.execute("PRAGMA page_size").fetchone()[0]*32)
            return record_operation_log(settings,**values)

        with patch("app.service.cloud_quarantine_log_service.record_operation_log",side_effect=exhaust):
            with self.assertRaisesRegex(CloudInventoryError,"storage_unavailable"):
                recovery.commitQuarantineObservation(self.settings,observeId,expectedRevision=self.revision(observeId),confirmLocalOnly=True,actorId=self.actor.id)
        self.assertIn(sqlite3.SQLITE_FULL,codes)
        self.assertEqual(before,self.state.sql("SELECT id,slot_held,committed_epoch,revision FROM cloud_quarantine_runs ORDER BY id"))
        self.assertEqual([(0,)],self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))
        self.assertEqual(auditBefore,self.state.sql("SELECT COUNT(*) FROM audit_events"))
        self.assertEqual(1,recovery.commitQuarantineObservation(self.settings,observeId,expectedRevision=self.revision(observeId),confirmLocalOnly=True,actorId=self.actor.id))

    def test_observation_requires_exited_original_confirmations_and_same_policy(self):
        runId, _ = self.unknown()
        with self.assertRaises(ValueError):
            self.observe(runId, confirmRemoteIdle=False)
        self.state.sql("UPDATE cloud_quarantine_runs SET worker_pid=? WHERE id=?", (os.getpid(), runId))
        with self.assertRaisesRegex(plans.QuarantineError, "worker_exit_unconfirmed"):
            self.observe(runId)

    def test_stale_evidence_cannot_release_original(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        self.observations(observeId, token)
        self.state.sql("UPDATE cloud_quarantine_rpcs SET responded_at=responded_at-301 WHERE run_id=?", (observeId,))
        with self.assertRaisesRegex(plans.QuarantineError, "recovery_not_ready_or_stale"):
            recovery.commitQuarantineObservation(self.settings, observeId, expectedRevision=self.revision(observeId), confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_quarantine_runs WHERE id=?", (runId,)))

    def test_cancelled_observation_cannot_release_original_unknown(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        self.observations(observeId, token)
        actions.requestQuarantineCancel(self.settings, observeId,
            expectedRevision=self.revision(observeId), actorId=self.actor.id, auditContext=self.context)
        with self.assertRaisesRegex(plans.QuarantineError, "recovery_not_ready_or_stale"):
            recovery.commitQuarantineObservation(self.settings, observeId,
                expectedRevision=self.revision(observeId), confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual([("unknown", None)], self.state.sql(
            "SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_quarantine_runs WHERE id=?", (runId,)))
        self.assertEqual([(0,)], self.state.sql("SELECT revision FROM cloud_directory_path_epoch"))

    def test_only_latest_observation_can_release_original_unknown(self):
        runId, _ = self.unknown()
        firstId, firstToken = self.observe(runId)
        self.observations(firstId, firstToken)
        secondId, secondToken = self.observe(runId)
        with self.assertRaisesRegex(plans.QuarantineError, "recovery_not_ready_or_stale"):
            recovery.commitQuarantineObservation(self.settings, firstId,
                expectedRevision=self.revision(firstId), confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual([("unknown", None)], self.state.sql(
            "SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))
        self.observations(secondId, secondToken, atTarget=True)
        self.assertEqual(1, recovery.commitQuarantineObservation(self.settings, secondId,
            expectedRevision=self.revision(secondId), confirmLocalOnly=True, actorId=self.actor.id))
        self.assertEqual([("unknown", secondId)], self.state.sql(
            "SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))
        self.assertEqual([(None,)], self.state.sql(
            "SELECT committed_epoch FROM cloud_quarantine_runs WHERE id=?", (firstId,)))

    def test_five_failed_observation_starts_exhaust_round_limit_without_releasing_original(self):
        runId, _ = self.unknown()
        for _ in range(5):
            observeId, token = self.observe(runId)
            finishQuarantineWorker(self.settings, observeId, token, None, stopReason="worker_start_failed")
        with self.assertRaisesRegex(plans.QuarantineError, "recovery_attempt_limit"):
            self.observe(runId)
        self.assertEqual([(5, 5, 0)], self.state.sql(
            "SELECT COUNT(*),MAX(attempt),SUM(slot_held) FROM cloud_quarantine_runs WHERE original_run_id=?", (runId,)))
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_quarantine_runs WHERE id=?", (runId,)))
        self.assertEqual([("unknown", None)], self.state.sql(
            "SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (runId,)))

    def test_audit_failure_rolls_back_observation_commit_and_allows_explicit_local_retry(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        self.observations(observeId, token, atTarget=True)
        queries = (
            "SELECT id,slot_held,committed_epoch,revision FROM cloud_quarantine_runs ORDER BY id",
            "SELECT id,phase,reviewed_by_run FROM cloud_quarantine_rpcs ORDER BY id",
            "SELECT revision FROM cloud_directory_path_epoch",
            "SELECT COUNT(*) FROM audit_events",
        )
        before = [self.state.sql(query) for query in queries]
        with patch.object(actions, "record_audit_event", side_effect=sqlite3.OperationalError("audit unavailable")):
            with self.assertRaisesRegex(CloudInventoryError, "storage_unavailable"):
                recovery.commitQuarantineObservation(self.settings, observeId,
                    expectedRevision=self.revision(observeId), confirmLocalOnly=True, actorId=self.actor.id)
        self.assertEqual(before, [self.state.sql(query) for query in queries])
        self.assertEqual(1, recovery.commitQuarantineObservation(self.settings, observeId,
            expectedRevision=self.revision(observeId), confirmLocalOnly=True, actorId=self.actor.id))

    def test_write_switch_off_allows_only_new_read_observation(self):
        runId, _ = self.unknown()
        self.settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, quarantineEnabled=False))
        observeId, token = self.observe(runId)
        self.assertEqual("observe", actions.getQuarantineRun(self.settings, observeId, self.actor.id)["kind"])

    def test_committed_target_position_creates_fixed_reverse_plan_not_network_action(self):
        runId, _ = self.unknown()
        observeId, token = self.observe(runId)
        self.observations(observeId, token, atTarget=True)
        recovery.commitQuarantineObservation(self.settings, observeId, expectedRevision=self.revision(observeId), confirmLocalOnly=True, actorId=self.actor.id)
        before = self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_rpcs")
        requestId = str(uuid4())
        result = recovery.createReverseQuarantinePlan(self.settings, self.plan["id"], requestId=requestId,
            expectedRevision=0, auditContext=self.context)
        self.assertEqual("reverse", result["kind"])
        self.assertTrue(result["valid"])
        self.assertEqual(self.plan["items"][0]["targetPath"], result["items"][0]["sourcePath"])
        self.assertEqual(before, self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_rpcs"))
        self.assertEqual(result, recovery.createReverseQuarantinePlan(self.settings, self.plan["id"], requestId=requestId,
            expectedRevision=0, auditContext=self.context))
        self.state.sql("UPDATE cloud_directory_path_epoch SET revision=revision+1")
        self.assertFalse(plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)["valid"])

    def test_manual_pause_release_supports_quarantine_without_releasing_original_unknown(self):
        runId, _ = self.unknown()
        grants = ["scan:run","cloud-quarantine:plan","cloud-quarantine:execute","settings:write"]
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps(grants),self.actor.id))
        self.state.sql("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='manual_pause',cooldown_until=0")
        revision = self.state.sql("SELECT revision FROM cloud_drive_budget_pool")[0][0]
        releaseDirectoryManualPause(self.settings, expectedRevision=revision, confirmClientsStopped=True, confirmRemoteIdle=True,auditContext=self.context)
        self.assertEqual([("paused","request_incomplete")], self.state.sql("SELECT state,pause_reason FROM cloud_drive_budget_pool"))
        self.assertEqual([(1,)], self.state.sql("SELECT slot_held FROM cloud_quarantine_runs WHERE id=?", (runId,)))
        self.observe(runId)

    def test_local_admin_worker_review_requires_both_processes_exited_and_never_releases_original(self):
        runId, token = self.fixture.bound()
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "GetApiTokenInfo")
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps(["scan:run","cloud-quarantine:plan","cloud-quarantine:execute","settings:write"]),self.actor.id))
        with self.assertRaisesRegex(plans.QuarantineError,"worker_exit_unconfirmed"):
            recovery.reviewQuarantineWorker(self.settings,runId,expectedRevision=self.revision(runId),confirmClientsStopped=True,confirmRemoteIdle=True,auditContext=self.context)
        self.state.sql("UPDATE cloud_quarantine_runs SET owner_pid=2147483000,worker_pid=2147483001 WHERE id=?", (runId,))
        result = recovery.reviewQuarantineWorker(self.settings,runId,expectedRevision=self.revision(runId),confirmClientsStopped=True,confirmRemoteIdle=True,auditContext=self.context)
        self.assertTrue(result["globalSlotHeld"])
        self.assertFalse(result["workerSlotHeld"])
        self.assertEqual([("unknown",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs WHERE id=?", (rpcId,)))

    def test_worker_review_rejects_other_owner_even_with_all_permissions(self):
        runId, _ = self.fixture.bound()
        revision = self.revision(runId)
        grants = ["scan:run", "cloud-quarantine:plan", "cloud-quarantine:execute", "settings:write"]
        other = create_user(self.settings, "review-other", "synthetic", "SyntheticOnly123!", grants, True)
        before = self.state.sql("SELECT * FROM cloud_quarantine_runs WHERE id=?", (runId,))
        with patch.object(recovery, "isProcessAlive", return_value=False):
            with self.assertRaisesRegex(plans.QuarantineError, "not_found"):
                recovery.reviewQuarantineWorker(self.settings, runId, expectedRevision=revision,
                    confirmClientsStopped=True, confirmRemoteIdle=True, auditContext=AuditWriteContext(other))
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_quarantine_runs WHERE id=?", (runId,)))

    def test_worker_review_requires_current_business_permissions(self):
        runId, _ = self.fixture.bound()
        revision = self.revision(runId)
        before = self.state.sql("SELECT * FROM cloud_quarantine_runs WHERE id=?", (runId,))
        grants = {"scan:run", "cloud-quarantine:plan", "cloud-quarantine:execute", "settings:write"}
        for missing in grants:
            with self.subTest(missing=missing):
                self.state.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps(sorted(grants - {missing})), self.actor.id))
                with patch.object(recovery, "isProcessAlive", return_value=False):
                    with self.assertRaisesRegex(plans.QuarantineError, "permission_denied"):
                        recovery.reviewQuarantineWorker(self.settings, runId, expectedRevision=revision,
                            confirmClientsStopped=True, confirmRemoteIdle=True, auditContext=self.context)
                self.assertEqual(before, self.state.sql("SELECT * FROM cloud_quarantine_runs WHERE id=?", (runId,)))
