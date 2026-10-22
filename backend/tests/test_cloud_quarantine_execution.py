"""隔离动作准入和账本只用临时库，不访问云端。"""

from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_quarantine_plans as fixtures
from app.service import cloud_quarantine_execution_service as actions
from app.service import cloud_quarantine_plan_service as plans
from app.service import cloud_quarantine_item_service as items
from app.service.cloud_directory_guard import directoryExecutionHeld
from app.service.cloud_inventory_service import inventoryDb


class QuarantineExecutionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudQuarantinePlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = replace(self.fixture.settings, cloudDrive=replace(self.fixture.settings.cloudDrive, quarantineEnabled=True))
        self.fixture.settings = self.settings
        self.state = self.fixture.state
        self.plan = self.fixture.create()
        self.context, self.actor = self.fixture.context, self.fixture.actor

    def approve(self, **changes):
        values = dict(requestId=str(uuid4()), expectedRevision=0, policyHash=self.plan["policyHash"],
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)
        values.update(changes)
        return actions.approveQuarantineRun(self.settings, self.plan["id"], **values)

    def bound(self):
        runId, token = self.approve()
        actions.bindQuarantineWorker(self.settings, runId, token, os.getpid())
        return runId, token

    def rpc(self, runId, token, method, path=None, ordinal=None, code="ok", identity=None):
        self.state.sql("UPDATE cloud_drive_budget_pool SET last_started_at=NULL")
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, method, itemOrdinal=ordinal, requestPath=path)
        actions.recordQuarantineResponse(self.settings, runId, token, rpcId, responseCode=code, receivedBytes=10, identity=identity)
        return rpcId

    def identity(self, path, objectId, *, directory=True, size=0):
        return {"objectId": objectId, "virtualPath": path, "name": path.rpartition("/")[2], "isDirectory": directory, "size": size}

    def test_approval_is_once_owned_fixed_budget_and_no_network(self):
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        runId, token = self.approve()
        value = actions.getQuarantineRun(self.settings, runId, self.actor.id)
        self.assertEqual("accepted", value["status"])
        self.assertEqual(21, value["maxRpc"])
        self.assertTrue(value["globalSlotHeld"])
        self.assertNotIn(token, json.dumps(value))
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        with self.assertRaisesRegex(plans.QuarantineError, "execution_already_accepted"):
            self.approve()
        self.assertEqual("execution_accepted", plans.getQuarantinePlan(self.settings, self.plan["id"], self.actor.id)["invalidReason"])
        with self.assertRaisesRegex(plans.QuarantineError, "execution_accepted"):
            plans.cancelQuarantinePlan(self.settings, self.plan["id"], expectedRevision=0, auditContext=self.context)

    def test_disabled_unconfirmed_revision_and_policy_are_rejected(self):
        for changes in ({"confirmMaintenance": False}, {"confirmGlobalFence": False},
                        {"expectedRevision": 1}, {"policyHash": "0"*64}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, plans.QuarantineError)):
                self.approve(**changes)
        settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, quarantineEnabled=False))
        with self.assertRaisesRegex(plans.QuarantineError, "execution_disabled"):
            actions.approveQuarantineRun(settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
                policyHash=self.plan["policyHash"], confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.context)

    def test_worker_binding_and_direct_write_without_proof_are_denied(self):
        runId, token = self.approve()
        with self.assertRaisesRegex(plans.QuarantineError, "worker_ownership_lost"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")
        actions.bindQuarantineWorker(self.settings, runId, token, os.getpid())
        with self.assertRaisesRegex(plans.QuarantineError, "identity_precheck_required"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "MoveFile", itemOrdinal=1, requestPath="/" + self.plan["items"][0]["sourceName"])
        for method in ("DeleteFile", "RenameFile", "GetSubFiles", "CreateFile"):
            with self.subTest(method=method), self.assertRaisesRegex(plans.QuarantineError, "rpc_not_allowed"):
                actions.reserveQuarantineRpc(self.settings, runId, token, method)

    def test_unknown_preserves_manual_pause_budget_and_global_fence(self):
        runId, token = self.bound()
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "GetApiTokenInfo")
        self.state.sql("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='manual_pause'")
        actions.markQuarantineUnknown(self.settings, runId, token, rpcId)
        self.assertEqual([("unknown",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs"))
        self.assertEqual([("manual_pause",)], self.state.sql("SELECT pause_reason FROM cloud_drive_budget_pool"))
        with inventoryDb(self.settings) as connection:
            self.assertTrue(directoryExecutionHeld(connection))
        with self.assertRaises(plans.QuarantineError):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")

    def test_late_response_after_revocation_is_saved_but_cannot_continue(self):
        runId, token = self.bound()
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "GetApiTokenInfo")
        self.state.sql("UPDATE users SET permissions_json='[]'")
        actions.recordQuarantineResponse(self.settings, runId, token, rpcId, responseCode="ok", receivedBytes=10)
        self.assertEqual([("responded",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs"))
        with self.assertRaisesRegex(plans.QuarantineError, "permission_denied"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")

    def test_unknown_blocks_other_rpc_and_bytes_exhaustion_stops(self):
        runId, token = self.bound()
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "GetApiTokenInfo")
        with self.assertRaisesRegex(plans.QuarantineError, "rpc_unresolved"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")
        actions.recordQuarantineResponse(self.settings, runId, token, rpcId, responseCode="ok", receivedBytes=1024*1024+1)
        self.assertEqual([("review",)], self.state.sql("SELECT status FROM cloud_quarantine_runs"))
        with self.assertRaises(plans.QuarantineError):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")

    def test_exact_paths_and_response_identity_are_required(self):
        runId, token = self.bound()
        with self.assertRaisesRegex(plans.QuarantineError, "path_not_in_plan"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "FindFileByPath", requestPath="/other")
        rpcId = actions.reserveQuarantineRpc(self.settings, runId, token, "FindFileByPath", requestPath="/")
        with self.assertRaises(ValueError):
            actions.recordQuarantineResponse(self.settings, runId, token, rpcId, responseCode="ok", receivedBytes=10,
                identity=self.identity("/other", "root"))
        self.assertEqual([("sending",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs"))

    def test_cancel_revision_does_not_release_slot_or_revoke_sent_request(self):
        runId, token = self.bound()
        actions.requestQuarantineCancel(self.settings, runId, expectedRevision=1, actorId=self.actor.id)
        value = actions.getQuarantineRun(self.settings, runId, self.actor.id)
        self.assertTrue(value["cancelRequested"])
        self.assertTrue(value["globalSlotHeld"])
        with self.assertRaisesRegex(plans.QuarantineError, "cancel_requested"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "GetRuntimeInfo")

    def test_approval_audit_failure_rolls_back_and_other_owner_cannot_query(self):
        with patch.object(actions, "record_audit_event", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self.approve()
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_runs"))
        runId, _ = self.approve()
        with self.assertRaises(plans.QuarantineError):
            actions.getQuarantineRun(self.settings, runId, self.actor.id+1)

    def prechecks(self, runId, token, ordinal=1, *, conflict=False):
        item, source, target, folder = actions.itemPaths(self.plan, ordinal)
        if self.state.sql("SELECT used_rpc FROM cloud_quarantine_runs WHERE id=?", (runId,))[0][0] == 0:
            self.rpc(runId, token, "GetApiTokenInfo")
            self.rpc(runId, token, "GetRuntimeInfo")
            self.rpc(runId, token, "FindFileByPath", "/", identity=self.identity("/", "parent-id"))
        self.rpc(runId, token, "FindFileByPath", "/", ordinal, identity=self.identity("/", "parent-id"))
        self.rpc(runId, token, "FindFileByPath", folder, ordinal, identity=self.identity(folder, self.plan["targetRemoteId"]))
        self.rpc(runId, token, "FindFileByPath", source, ordinal, identity=self.identity(source, item["remoteId"], directory=False, size=item["size"]))
        self.rpc(runId, token, "FindFileByPath", target, ordinal, code="ok" if conflict else "not_found",
            identity=self.identity(target, "unrelated", directory=False, size=1) if conflict else None)

    def test_verified_move_requires_before_after_id_size_and_both_parents(self):
        runId, token = self.bound()
        self.prechecks(runId, token)
        items.prepareQuarantineItem(self.settings, runId, token, 1)
        item, source, target, folder = actions.itemPaths(self.plan, 1)
        self.rpc(runId, token, "MoveFile", source, 1)
        self.rpc(runId, token, "FindFileByPath", target, 1, identity=self.identity(target, item["remoteId"], directory=False, size=item["size"]))
        self.rpc(runId, token, "FindFileByPath", source, 1, code="not_found")
        self.rpc(runId, token, "FindFileByPath", "/", 1, identity=self.identity("/", "parent-id"))
        self.rpc(runId, token, "FindFileByPath", folder, 1, identity=self.identity(folder, self.plan["targetRemoteId"]))
        self.assertEqual("verified", items.verifyQuarantineItem(self.settings, runId, token, 1))
        self.assertTrue(actions.getQuarantineRun(self.settings, runId, self.actor.id)["globalSlotHeld"])

    def test_conflict_precheck_does_not_prepare_or_send_move(self):
        runId, token = self.bound()
        self.prechecks(runId, token, conflict=True)
        with self.assertRaisesRegex(plans.QuarantineError, "identity_or_conflict_changed"):
            items.prepareQuarantineItem(self.settings, runId, token, 1)
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_rpcs WHERE method='MoveFile'"))

    def test_new_rpc_after_preparation_invalidates_direct_move_proof(self):
        runId, token = self.bound()
        self.prechecks(runId, token)
        items.prepareQuarantineItem(self.settings, runId, token, 1)
        self.rpc(runId, token, "FindFileByPath", "/", 1, identity=self.identity("/", "parent-id"))
        self.state.sql("UPDATE cloud_drive_budget_pool SET last_started_at=NULL")
        with self.assertRaisesRegex(plans.QuarantineError, "identity_precheck"):
            actions.reserveQuarantineRpc(self.settings, runId, token, "MoveFile", itemOrdinal=1,
                requestPath="/" + self.plan["items"][0]["sourceName"])

    def test_skip_success_is_not_misreported_as_verified_move(self):
        runId, token = self.bound()
        self.prechecks(runId, token)
        items.prepareQuarantineItem(self.settings, runId, token, 1)
        item, source, target, folder = actions.itemPaths(self.plan, 1)
        self.rpc(runId, token, "MoveFile", source, 1)
        self.rpc(runId, token, "FindFileByPath", target, 1, identity=self.identity(target, "unrelated", directory=False, size=1))
        self.rpc(runId, token, "FindFileByPath", source, 1, identity=self.identity(source, item["remoteId"], directory=False, size=item["size"]))
        self.rpc(runId, token, "FindFileByPath", "/", 1, identity=self.identity("/", "parent-id"))
        self.rpc(runId, token, "FindFileByPath", folder, 1, identity=self.identity(folder, self.plan["targetRemoteId"]))
        self.assertEqual("not_applied", items.verifyQuarantineItem(self.settings, runId, token, 1))
        self.assertEqual([("review", "target_conflict_or_not_applied")], self.state.sql("SELECT status,reason FROM cloud_quarantine_runs"))


if __name__ == "__main__":
    unittest.main()
