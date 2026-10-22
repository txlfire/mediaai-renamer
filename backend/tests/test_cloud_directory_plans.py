"""C2固定计划只使用临时库和合成观察，不连接云盘。"""

from dataclasses import replace
import json
import sqlite3
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_inventory as fixtures
from app.core.config import CloudDriveSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache, setCloudInventoryKeep
from app.service.cloud_inventory_service import CloudInventoryError
from app.service.cloud_directory_plan_service import (
    CloudDirectoryError, createDirectoryPlan, getDirectoryPlan, directoryPlanByRequest, cancelDirectoryPlan,
)


class CloudDirectoryPlanTest(unittest.TestCase):
    def setUp(self):
        self.state = fixtures.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings = replace(self.state.settings, cloudDrive=CloudDriveSettings(fixtures.ROOT, endpoint=fixtures.ENDPOINT))
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?",
            (json.dumps(["scan:run", "cloud-directory:plan"]), self.state.actor.id))
        self.context = self.state.context

    def observed(self, names=None, *, partial=False):
        jobId = self.state.job()
        client = fixtures.SyntheticClient({fixtures.ROOT: [fixtures.entry(name, directory=True)
            for name in (names if names is not None else ["[论坛推广] 示例 (2024)"])]}, fail="unavailable" if partial else None)
        if partial:
            with self.assertRaises(CloudInventoryError):
                self.state.runJob(jobId, client)
        else:
            self.state.runJob(jobId, client)
        return jobId

    def params(self, jobId, **overrides):
        params = dict(requestId=str(uuid4()), jobId=jobId, expectedRevision=self.state.summary(jobId)["revision"],
            entryIds=[row[0] for row in self.state.sql("SELECT id FROM cloud_drive_inventory_entries WHERE job_id=? ORDER BY id", (jobId,))],
            folderTerms=["论坛推广"], auditContext=self.context)
        params.update(overrides)
        return params

    def plan(self, jobId=None, **options):
        return createDirectoryPlan(self.settings, **self.params(jobId or self.observed(), **options))

    def get(self, planId):
        return getDirectoryPlan(self.settings, planId, self.state.actor.id)

    def test_fixed_plan_is_local_idempotent_bounded_and_retains_ids_rules(self):
        params = self.params(self.observed(["[论坛推广] 示例 (2024)", "[论坛推广] 另一部 (2025)"]))
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        result = createDirectoryPlan(self.settings, **params)
        self.assertTrue(result["valid"])
        self.assertFalse(result["writeAvailable"])
        self.assertFalse(result["networkAccess"])
        self.assertEqual(["示例 (2024)", "另一部 (2025)"], [row["targetName"] for row in result["items"]])
        self.assertTrue(all(row["remoteId"] and row["generation"] == 1 for row in result["items"]))
        self.assertEqual(result, createDirectoryPlan(self.settings, **params))
        self.assertEqual(result, directoryPlanByRequest(self.settings, params["requestId"], self.state.actor.id))
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual(1, self.state.sql("SELECT COUNT(*) FROM audit_events WHERE event_type='cloud_directory.plan'")[0][0])
        with self.assertRaisesRegex(CloudDirectoryError, "request_conflict"):
            createDirectoryPlan(self.settings, **{**params, "folderTerms": ["另一词"]})

    def test_wrong_permissions_owner_revocation_and_disabled_are_rejected(self):
        result = self.plan()
        other = create_user(self.settings, "other", "Other", "TestOnly123!", ["cloud-directory:plan"], True)
        with self.assertRaisesRegex(CloudDirectoryError, "not_found"):
            getDirectoryPlan(self.settings, result["id"], other.id)
        for permissions in (["scan:run"], ["rename:execute"], ["cloud-directory:execute"]):
            self.state.sql("UPDATE users SET permissions_json=?", (json.dumps(permissions),))
            with self.assertRaisesRegex(CloudDirectoryError, "permission_denied"):
                self.get(result["id"])
        self.state.sql("UPDATE users SET enabled=0")
        with self.assertRaisesRegex(CloudDirectoryError, "permission_denied"):
            self.get(result["id"])

    def test_cache_clear_keeps_immutable_plan_but_invalidates_it(self):
        jobId = self.observed()
        result = self.plan(jobId)
        items = self.state.sql("SELECT * FROM cloud_directory_plan_items")
        clearCloudInventoryCache(self.settings, jobId, confirmClear=True,
            expectedRevision=self.state.summary(jobId)["revision"], auditContext=self.context)
        current = self.get(result["id"])
        self.assertFalse(current["valid"])
        self.assertEqual(items, self.state.sql("SELECT * FROM cloud_directory_plan_items"))
        self.assertEqual(result["items"], current["items"])
        self.assertEqual(1, self.state.sql("SELECT COUNT(*) FROM cloud_directory_plans")[0][0])

    def test_keep_revision_expiry_policy_and_observation_changes_invalidate(self):
        jobId = self.observed()
        result = self.plan(jobId)
        entryId = result["items"][0]["entryId"]
        setCloudInventoryKeep(self.settings, jobId, entryId, kept=True,
            expectedRevision=self.state.summary(jobId)["revision"], auditContext=self.context)
        self.assertEqual("observation_changed", self.get(result["id"])["invalidReason"])
        result = self.plan()
        for config in (replace(self.settings.cloudDrive, endpoint="https://other.example"),
                       replace(self.settings.cloudDrive, minIntervalSeconds=3)):
            self.assertEqual("policy_changed", getDirectoryPlan(replace(self.settings, cloudDrive=config), result["id"],
                             self.state.actor.id)["invalidReason"])
        with patch("app.service.cloud_directory_plan_service.time.time", return_value=result["expiresAt"]):
            self.assertEqual("expired", self.get(result["id"])["invalidReason"])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id='replaced' WHERE id=?", (result["items"][0]["entryId"],))
        self.assertEqual("observation_changed", self.get(result["id"])["invalidReason"])

    def test_missing_or_duplicate_ids_partial_and_foreign_entries_are_rejected(self):
        jobId = self.observed()
        entryId = self.params(jobId)["entryIds"][0]
        for ids in ([], [entryId] * 2, [True], [entryId] * 6):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.plan(jobId, entryIds=ids)
        with self.assertRaisesRegex(CloudDirectoryError, "entry_not_found"):
            self.plan(jobId, entryIds=[9999])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id=NULL WHERE job_id=?", (jobId,))
        with self.assertRaisesRegex(CloudDirectoryError, "identity_or_observation"):
            self.plan(jobId)
        with self.assertRaisesRegex(CloudDirectoryError, "observation_changed"):
            self.plan(self.observed(partial=True))

    def test_name_protection_collision_unicode_and_unsafe_names(self):
        for name in ("BDMV", "Season 01", "[论坛推广] Season 01", "[论坛推广] CON", "[论坛推广] bad:name",
                     "[论坛推广] 示例\u202eabc", "[论坛推广] Cafe\u0301", "[论坛推广] 示例 (2024)",
                     "[论坛推广] ＣＯＮ", "[论坛推广] bad／name", "[论坛推广] bad%2推广Fname"):
            with self.subTest(name=name):
                terms = ["2024"] if "2024" in name else ["论坛推广", "推广"]
                with self.assertRaises(CloudDirectoryError):
                    self.plan(self.observed([name]), folderTerms=terms)
        for target in ("ABC", "ＡＢＣ", "abc"):
            jobId = self.observed(["[论坛推广] abc", target])
            firstId = self.params(jobId)["entryIds"][0]
            with self.assertRaisesRegex(CloudDirectoryError, "observed_name_conflict"):
                self.plan(jobId, entryIds=[firstId])

    def test_selected_entries_cannot_expand_to_later_observations_or_changed_scope(self):
        jobId = self.observed(["[论坛推广] one", "[论坛推广] two"])
        firstId = self.params(jobId)["entryIds"][0]
        result = self.plan(jobId, entryIds=[firstId])
        self.assertEqual(1, len(result["items"]))
        self.state.sql("UPDATE cloud_drive_inventory_entries SET path=?,name=? WHERE id=?",
                       ("/another-root/one", "one", firstId))
        self.assertEqual("scope_changed", self.get(result["id"])["invalidReason"])

    def test_cancel_requires_revision_and_never_deletes_snapshot(self):
        result = self.plan()
        with self.assertRaisesRegex(CloudDirectoryError, "revision_conflict"):
            cancelDirectoryPlan(self.settings, result["id"], expectedRevision=1, auditContext=self.context)
        cancelled = cancelDirectoryPlan(self.settings, result["id"], expectedRevision=0, auditContext=self.context)
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual(1, cancelled["revision"])
        self.assertEqual(result["items"], cancelled["items"])
        self.assertFalse(cancelled["valid"])
        with self.assertRaisesRegex(CloudDirectoryError, "revision_conflict"):
            cancelDirectoryPlan(self.settings, result["id"], expectedRevision=1, auditContext=self.context)

    def test_schema42_migration_is_repeatable_and_preserves_observations(self):
        self.observed()
        before = self.state.sql("SELECT * FROM cloud_drive_inventory_entries")
        self.state.sql("DROP TABLE cloud_directory_plan_items")
        self.state.sql("DROP TABLE cloud_directory_plans")
        self.state.sql("UPDATE app_meta SET value='42' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_inventory_entries"))
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))
        with sqlite3.connect(self.settings.database_path) as connection:
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())

    def test_capacity_and_sibling_limit_fail_closed(self):
        jobId = self.observed(["[论坛推广] one", *[str(i) for i in range(1000)]])
        firstId = self.params(jobId)["entryIds"][0]
        with self.assertRaisesRegex(CloudDirectoryError, "smaller_scope_required"):
            self.plan(jobId, entryIds=[firstId])
        with patch("app.service.cloud_directory_plan_service.PLAN_LIMIT", 0):
            with self.assertRaisesRegex(CloudDirectoryError, "plan_capacity"):
                self.plan()

    def test_protected_ancestor_cannot_be_hidden_inside_approved_root(self):
        for parent in ("BDMV", "extras", "Season 01"):
            with self.subTest(parent=parent):
                nested = fixtures.ROOT + "/" + parent
                jobId = self.state.job(selectedPaths=[nested])
                self.state.runJob(jobId, fixtures.SyntheticClient({nested: [fixtures.entry("[论坛推广] 示例 (2024)", parent=nested, directory=True)]}))
                with self.assertRaisesRegex(CloudDirectoryError, "no_safe_suggestion"):
                    self.plan(jobId)

    def test_audit_failure_rolls_back_plan_and_items_atomically(self):
        params = self.params(self.observed())
        with patch("app.service.cloud_directory_plan_service.record_audit_event", side_effect=RuntimeError("synthetic-audit-failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-audit-failure"):
                createDirectoryPlan(self.settings, **params)
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_plans"))
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_directory_plan_items"))

    def test_same_parent_multiple_targets_and_distinct_remote_identity_are_enforced(self):
        jobId = self.observed(["[论坛推广] same", "(论坛推广) same"])
        with self.assertRaisesRegex(CloudDirectoryError, "observed_name_conflict"):
            self.plan(jobId)
        jobId = self.observed(["[论坛推广] one", "[论坛推广] two"])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id='same-object' WHERE job_id=?", (jobId,))
        with self.assertRaisesRegex(CloudDirectoryError, "same_parent_distinct_identity"):
            self.plan(jobId)
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id=path WHERE job_id=?", (jobId,))
        second = self.params(jobId)["entryIds"][1]
        self.state.sql("UPDATE cloud_drive_inventory_entries SET path=? WHERE id=?", (fixtures.ROOT + "/other/[论坛推广] two", second))
        with self.assertRaisesRegex(CloudDirectoryError, "scope_changed"):
            self.plan(jobId)
        left, right = fixtures.ROOT + "/left", fixtures.ROOT + "/right"
        jobId = self.state.job(selectedPaths=[left, right])
        self.state.runJob(jobId, fixtures.SyntheticClient({
            left: [fixtures.entry("[论坛推广] one", parent=left, directory=True)],
            right: [fixtures.entry("[论坛推广] two", parent=right, directory=True)],
        }))
        with self.assertRaisesRegex(CloudDirectoryError, "same_parent_distinct_identity"):
            self.plan(jobId)


class CloudDirectoryPlanApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.fixture = CloudDirectoryPlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = TestClient(create_app(self.fixture.settings))
        bindCloudConfiguration(self.fixture.settings)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.payload = self.fixture.params(self.fixture.observed())
        self.payload.pop("auditContext")
        self.base = "/api/cloud-drive/directory-plans"

    def test_api_create_get_request_lookup_cancel_and_plan_permission_cannot_execute(self):
        response = self.client.post(self.base, headers=self.headers, json=self.payload)
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        url = self.base + "/" + result["id"]
        self.assertEqual(result, self.client.get(url, headers=self.headers).json())
        lookup = "/api/cloud-drive/directory-plan-requests/" + self.payload["requestId"]
        self.assertEqual(result, self.client.get(lookup, headers=self.headers).json())
        self.assertFalse(result["writeAvailable"])
        self.assertEqual(403, self.client.post(url + "/execute", headers=self.headers, json={}).status_code)
        cancelled = self.client.post(url + "/cancel", headers=self.headers, json={"expectedRevision": 0})
        self.assertEqual(200, cancelled.status_code)
        self.assertEqual("cancelled", cancelled.json()["status"])
        self.assertEqual(409, self.client.post(url + "/cancel", headers=self.headers, json={"expectedRevision": 0}).status_code)

    def test_strict_shapes_authentication_permissions_and_owner(self):
        self.assertEqual(401, self.client.post(self.base, json=self.payload).status_code)
        for bad in ({"entryIds": [True]}, {"expectedRevision": True}, {"entryIds": [1] * 6},
                    {"apiToken": "synthetic-secret"}, {"sourcePath": "/another"}, {"folderTerms": ["one"] * 51}):
            with self.subTest(bad=bad):
                result = self.client.post(self.base, headers=self.headers, json={**self.payload, **bad})
                self.assertEqual(422, result.status_code)
                self.assertNotIn("synthetic-secret", result.text)
        invalidTerm = self.client.post(self.base, headers={**self.headers, "Content-Type": "application/json"},
            content=json.dumps({**self.payload, "folderTerms": ["test\ud800"]}, ensure_ascii=True))
        self.assertEqual(422, invalidTerm.status_code)
        result = self.client.post(self.base, headers=self.headers, json=self.payload).json()
        self.fixture.state.sql("UPDATE cloud_directory_plans SET actor_id=actor_id+1")
        self.assertEqual(404, self.client.get(self.base + "/" + result["id"], headers=self.headers).status_code)
        self.fixture.state.sql("UPDATE users SET permissions_json='[\"scan:run\",\"rename:execute\"]'")
        self.assertEqual(403, self.client.post(self.base, headers=self.headers, json=self.payload).status_code)

    def test_storage_failure_and_invalid_scope_do_not_leak_server_data(self):
        self.assertEqual(400, self.client.get(self.base + "/not-a-uuid", headers=self.headers).status_code)
        self.fixture.state.sql("DROP TABLE cloud_directory_plans")
        result = self.client.post(self.base, headers=self.headers, json=self.payload)
        self.assertEqual(503, result.status_code)
        self.assertNotIn(str(self.fixture.settings.database_path), result.text)


if __name__ == "__main__":
    unittest.main()
