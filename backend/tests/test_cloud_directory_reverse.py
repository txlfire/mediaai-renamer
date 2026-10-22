"""反向必须新计划/新预算，不能重放旧执行或依赖已清理观察。"""

from dataclasses import replace
import json
import unittest
from uuid import uuid4

import test_cloud_directory_paths as fixtures
from app.service.cloud_directory_reverse_service import createReverseDirectoryPlan
from app.service.cloud_directory_plan_service import getDirectoryPlan, CloudDirectoryError
from app.service.cloud_directory_admission_service import approveDirectoryExecution, bindDirectoryWorker
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache
from app.service.auth_service import create_user
from app.service.audit_service import AuditWriteContext


class DirectoryReverseTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudDirectoryPathTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings, self.plan = self.fixture.settings, self.fixture.plan
        self.sql = self.fixture.state.sql
        self.fixture.seedVerified()
        self.fixture.commit()
        self.revision = self.sql("SELECT revision FROM cloud_directory_executions")[0][0]

    def reverse(self, **changes):
        params = dict(requestId=str(uuid4()), expectedRevision=self.revision, auditContext=self.fixture.context)
        params.update(changes)
        return createReverseDirectoryPlan(self.settings, self.plan["id"], **params)

    def test_reverse_has_new_fixed_identity_and_never_sends_or_uses_old_budget(self):
        before = self.sql("SELECT * FROM cloud_directory_executions")
        result = self.reverse()
        self.assertTrue(result["valid"], result)
        self.assertEqual("reverse", result["kind"])
        self.assertEqual(self.plan["id"], result["originPlanId"])
        self.assertNotEqual(self.plan["id"], result["id"])
        self.assertEqual(self.plan["items"][0]["remoteId"], result["items"][0]["remoteId"])
        self.assertEqual(self.plan["items"][0]["targetName"], result["items"][0]["sourceName"])
        self.assertEqual(self.plan["items"][0]["sourceName"], result["items"][0]["targetName"])
        self.assertEqual(before, self.sql("SELECT * FROM cloud_directory_executions"))
        self.assertFalse(result["networkAccess"])
        self.assertFalse(result["writeAvailable"])
        token = approveDirectoryExecution(self.settings, result["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.fixture.context)
        import os
        bindDirectoryWorker(self.settings, result["id"], token, os.getpid())
        self.assertEqual([(0, 10)], self.sql("SELECT used_rpc,max_rpc FROM cloud_directory_executions WHERE plan_id=?", (result["id"],)))

    def test_reverse_survives_origin_cache_clear_but_not_new_path_epoch(self):
        result = self.reverse()
        revision = self.fixture.state.summary(self.fixture.jobId)["revision"]
        clearCloudInventoryCache(self.settings, self.fixture.jobId, confirmClear=True, expectedRevision=revision, auditContext=self.fixture.context)
        self.assertTrue(getDirectoryPlan(self.settings, result["id"], self.fixture.actor.id)["valid"])
        self.sql("UPDATE cloud_directory_path_epoch SET revision=revision+1")
        self.assertFalse(getDirectoryPlan(self.settings, result["id"], self.fixture.actor.id)["valid"])
        with self.assertRaisesRegex(CloudDirectoryError, "reverse_basis_outdated"):
            self.reverse()

    def test_reverse_rejects_uncommitted_live_or_stale_origin_and_configuration_change(self):
        self.sql("UPDATE cloud_directory_executions SET status='review',reason='rpc_unknown'")
        with self.assertRaisesRegex(CloudDirectoryError, "reverse_requires_committed"):
            self.reverse()
        self.sql("UPDATE cloud_directory_executions SET status='completed',reason=NULL")
        with self.assertRaisesRegex(CloudDirectoryError, "revision_conflict"):
            self.reverse(expectedRevision=self.revision + 1)
        self.settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, directoryMappingRevision="different"))
        with self.assertRaisesRegex(CloudDirectoryError, "execution_policy_changed"):
            self.reverse()

    def test_reverse_is_owner_scoped_local_idempotent_and_fixed_target_cannot_expand(self):
        requestId = str(uuid4())
        result = self.reverse(requestId=requestId)
        self.assertEqual(result, self.reverse(requestId=requestId))
        other = create_user(self.settings, "other-reverse", "synthetic", "TestOnly123!", ["cloud-directory:plan"], True)
        with self.assertRaisesRegex(CloudDirectoryError, "not_found"):
            self.reverse(auditContext=AuditWriteContext(other))
        self.sql("UPDATE cloud_directory_plan_items SET target_name='different',target_path=REPLACE(target_path,source_name,'different') WHERE plan_id=?", (result["id"],))
        self.assertFalse(getDirectoryPlan(self.settings, result["id"], self.fixture.actor.id)["valid"])

    def test_partial_reconciled_origin_reverses_only_committed_target_positions(self):
        self.sql("UPDATE cloud_directory_executions SET status='review',reason='positions_reconciled'")
        result = self.reverse()
        self.assertEqual(1, len(result["items"]))
        self.sql("DELETE FROM cloud_directory_path_revisions")
        with self.assertRaisesRegex(CloudDirectoryError, "reverse_basis_outdated_or_no_changes"):
            self.reverse()
