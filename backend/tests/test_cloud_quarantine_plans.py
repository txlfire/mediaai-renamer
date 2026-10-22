"""隔离计划仅使用临时库和合成观察，不连接真实云盘。"""

from dataclasses import replace
import json
import sqlite3
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import test_cloud_inventory as fixtures
from app.core.config import CloudDriveSettings, cloudDriveSettingsFromDict
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.main import create_app
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cloud_ad_review_service import createCloudAdReview
from app.service import cloud_quarantine_plan_service as plans
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache, setCloudInventoryKeep


class CloudQuarantinePlanTest(unittest.TestCase):
    def setUp(self):
        self.state = fixtures.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings = replace(self.state.settings, cloudDrive=CloudDriveSettings(fixtures.ROOT,
            endpoint=fixtures.ENDPOINT, directoryMappingRevision="synthetic-v1"))
        self.state.settings = self.settings
        self.actor, self.context = self.state.actor, self.state.context
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps([
            "scan:run", "cloud-quarantine:plan", "cloud-quarantine:execute"]), self.actor.id))
        self.jobId = self.state.job()
        self.state.runJob(self.jobId, fixtures.SyntheticClient({fixtures.ROOT: [
            fixtures.entry("更多资源-www.example.com.txt"), fixtures.entry("更多资源2.txt"),
            fixtures.entry("poster-更多资源.jpg"), fixtures.entry("_MediaAI_Quarantine", directory=True)]}))
        self.ids = [row[0] for row in self.state.sql("SELECT id FROM cloud_drive_inventory_entries ORDER BY id")]
        self.review = createCloudAdReview(self.settings, requestId=str(uuid4()), jobId=self.jobId,
            expectedRevision=self.state.summary(self.jobId)["revision"],
            items=[{"entryId": item, "decision": "suggest_quarantine", "reason": "人工复核名称含资源推广"} for item in self.ids[:2]],
            confirmLocalOnly=True, auditContext=self.context)

    def create(self, **changes):
        values = {"requestId": str(uuid4()), "reviewId": self.review["id"], "entryIds": self.ids[:2],
            "targetEntryId": self.ids[3], "auditContext": self.context}
        values.update(changes)
        return plans.createQuarantinePlan(self.settings, **values)

    def test_fixed_plan_requires_no_cloud_and_matches_only_reviewed_files_and_existing_target(self):
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        result = self.create()
        self.assertTrue(result["valid"])
        self.assertFalse(result["writeAvailable"])
        self.assertFalse(result["networkAccess"])
        self.assertEqual(fixtures.ROOT, result["rootPath"])
        self.assertEqual("_MediaAI_Quarantine", result["targetName"])
        self.assertEqual(self.ids[:2], [item["entryId"] for item in result["items"]])
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual(result, plans.getQuarantinePlan(self.settings, result["id"], self.actor.id))
        self.assertEqual(result, plans.getQuarantinePlan(self.settings, result["requestId"], self.actor.id, byRequest=True))
        self.assertEqual(1, plans.listQuarantinePlans(self.settings, self.actor.id)["total"])

    def test_idempotency_does_not_extend_expiry_or_accept_changed_inputs(self):
        requestId = str(uuid4())
        original = self.create(requestId=requestId)
        self.assertEqual(original, self.create(requestId=requestId))
        with self.assertRaisesRegex(plans.QuarantineError, "request_conflict"):
            self.create(requestId=requestId, entryIds=self.ids[:1])

    def test_invalid_or_unreviewed_selection_and_non_dedicated_target_are_rejected(self):
        for ids in ([], [True], [self.ids[0]]*2, list(range(1,7)), [self.ids[2]], [self.ids[3]]):
            with self.subTest(ids=ids), self.assertRaises((ValueError, plans.QuarantineError)):
                self.create(entryIds=ids)
        with self.assertRaises(plans.QuarantineError):
            self.create(targetEntryId=self.ids[0])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET name='another',path=? WHERE id=?",
            (fixtures.ROOT + "/another", self.ids[3]))
        with self.assertRaisesRegex(plans.QuarantineError, "invalid_quarantine_target"):
            self.create()

    def test_source_identity_generation_and_protection_changes_invalidate_plan(self):
        result = self.create()
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id='changed' WHERE id=?", (self.ids[0],))
        self.assertFalse(plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)["valid"])
        with self.assertRaisesRegex(plans.QuarantineError, "observation_changed"):
            self.create()

    def test_keep_or_clear_invalidates_fixed_plan_but_preserves_evidence(self):
        result = self.create()
        setCloudInventoryKeep(self.settings, self.jobId, self.ids[0], kept=True,
            expectedRevision=self.state.summary(self.jobId)["revision"], auditContext=self.context)
        self.assertFalse(plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)["valid"])
        clearCloudInventoryCache(self.settings, self.jobId, expectedRevision=self.state.summary(self.jobId)["revision"],
            confirmClear=True, auditContext=self.context)
        historical = plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)
        self.assertEqual(result["items"], historical["items"])
        self.assertFalse(historical["valid"])

    def test_permissions_are_direct_current_and_do_not_inherit_c2_grants(self):
        result = self.create()
        self.state.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps([
            "scan:run", "cloud-directory:plan", "cloud-directory:execute"]), self.actor.id))
        with self.assertRaisesRegex(plans.QuarantineError, "permission_denied"):
            plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)

    def test_cancel_requires_current_revision_and_keeps_fixed_history(self):
        result = self.create()
        cancelled = plans.cancelQuarantinePlan(self.settings, result["id"], expectedRevision=0, auditContext=self.context)
        self.assertEqual("cancelled", cancelled["status"])
        self.assertFalse(cancelled["valid"])
        with self.assertRaisesRegex(plans.QuarantineError, "revision_conflict"):
            plans.cancelQuarantinePlan(self.settings, result["id"], expectedRevision=0, auditContext=self.context)

    def test_capacity_and_audit_rollback(self):
        with patch.object(plans, "MAX_PLANS", 0):
            with self.assertRaisesRegex(plans.QuarantineError, "plan_capacity"):
                self.create()
        with patch.object(plans, "record_audit_event", side_effect=RuntimeError("synthetic-audit")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-audit"):
                self.create()
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_plans"))

    def test_schema51_upgrade_is_idempotent_and_preserves_reviews(self):
        self.state.sql("DROP TABLE cloud_quarantine_plans")
        self.state.sql("UPDATE app_meta SET value='51' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_ad_reviews"))

    def test_quarantine_config_is_strict_and_execution_off_by_default(self):
        self.assertFalse(cloudDriveSettingsFromDict({}).quarantineEnabled)
        for values in ({"quarantine_enabled": 1}, {"quarantine_folder_name": "../other"}, {"quarantine_enabled": True}):
            with self.assertRaises(ValueError):
                cloudDriveSettingsFromDict(values)

    def test_all_capacity_limits_stop_without_deleting_or_extending_old_plan(self):
        original = self.create()
        for key in ("MAX_SNAPSHOT_BYTES", "MAX_TOTAL_BYTES", "MAX_PLANS"):
            with self.subTest(key=key), patch.object(plans, key, 1):
                with self.assertRaisesRegex(plans.QuarantineError, "plan_capacity"):
                    self.create()
        self.assertEqual(original, plans.getQuarantinePlan(self.settings, original["id"], self.actor.id))

    def test_expiry_clock_rollback_and_policy_changes_preserve_historical_items(self):
        result = self.create()
        for instant in (result["createdAt"] - 1, result["expiresAt"], float("nan")):
            with self.subTest(instant=instant), patch.object(plans.time, "time", return_value=instant):
                value = plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)
                self.assertEqual("expired", value["invalidReason"])
                self.assertEqual(result["items"], value["items"])
        for changes in ({"quarantineEnabled": True}, {"quarantineFolderName": "other"},
                        {"directoryMappingRevision": "next"}, {"minIntervalSeconds": 3}):
            with self.subTest(changes=changes):
                settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, **changes))
                self.assertEqual("policy_changed", plans.getQuarantinePlan(settings, result["id"], self.actor.id)["invalidReason"])

    def test_target_identity_or_generation_mutation_invalidates_snapshot(self):
        result = self.create()
        original = self.state.sql("SELECT remote_id,generation FROM cloud_drive_inventory_entries WHERE id=?", (self.ids[3],))[0]
        for column, value in (("remote_id", "different-target"), ("generation", original[1]+1)):
            with self.subTest(column=column):
                self.state.sql(f"UPDATE cloud_drive_inventory_entries SET {column}=? WHERE id=?", (value, self.ids[3]))
                self.assertFalse(plans.getQuarantinePlan(self.settings, result["id"], self.actor.id)["valid"])
                self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id=?,generation=? WHERE id=?", (*original, self.ids[3]))

    def test_source_size_type_and_path_mutations_cannot_inherit_review(self):
        for clause, value in (("size", 11), ("is_directory", 1), ("path", fixtures.ROOT + "/other.txt")):
            original = self.state.sql(f"SELECT {clause} FROM cloud_drive_inventory_entries WHERE id=?", (self.ids[0],))[0][0]
            with self.subTest(clause=clause):
                self.state.sql(f"UPDATE cloud_drive_inventory_entries SET {clause}=? WHERE id=?", (value, self.ids[0]))
                with self.assertRaisesRegex(plans.QuarantineError, "observation_changed"):
                    self.create()
                self.state.sql(f"UPDATE cloud_drive_inventory_entries SET {clause}=? WHERE id=?", (original, self.ids[0]))

    def test_review_epoch_job_and_endpoint_changes_stop_new_plan(self):
        for clause, value in (("status", "paused"), ("cancel_requested", 1), ("revision", 999)):
            old = self.state.sql(f"SELECT {clause} FROM cloud_drive_inventory_jobs WHERE id=?", (self.jobId,))[0][0]
            with self.subTest(clause=clause):
                self.state.sql(f"UPDATE cloud_drive_inventory_jobs SET {clause}=? WHERE id=?", (value, self.jobId))
                with self.assertRaises(plans.QuarantineError):
                    self.create()
                self.state.sql(f"UPDATE cloud_drive_inventory_jobs SET {clause}=? WHERE id=?", (old, self.jobId))
        self.state.sql("UPDATE cloud_drive_read_sessions SET endpoint_hash='other'")
        with self.assertRaisesRegex(plans.QuarantineError, "scope_changed"):
            self.create()

    def test_other_owner_missing_scan_permission_and_disabled_actor_are_rejected(self):
        result = self.create()
        other = create_user(self.settings, "other", "other", "TestOnly123!", ["scan:run", "cloud-quarantine:plan"], True)
        with self.assertRaisesRegex(plans.QuarantineError, "not_found"):
            plans.getQuarantinePlan(self.settings, result["id"], other.id)
        self.assertEqual(0, plans.listQuarantinePlans(self.settings, other.id)["total"])
        with self.assertRaisesRegex(plans.QuarantineError, "request_conflict"):
            self.create(requestId=result["requestId"], auditContext=AuditWriteContext(other))
        self.state.sql("UPDATE users SET permissions_json='[\"cloud-quarantine:plan\"]' WHERE id=?", (self.actor.id,))
        with self.assertRaisesRegex(plans.QuarantineError, "permission_denied"):
            self.create()
        self.state.sql("UPDATE users SET enabled=0 WHERE id=?", (other.id,))
        with self.assertRaisesRegex(plans.QuarantineError, "permission_denied"):
            plans.listQuarantinePlans(self.settings, other.id)

    def test_selected_identity_duplicate_target_keep_and_invalid_page_stop(self):
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id=? WHERE id=?",
            (self.state.sql("SELECT remote_id FROM cloud_drive_inventory_entries WHERE id=?", (self.ids[0],))[0][0], self.ids[3]))
        with self.assertRaisesRegex(plans.QuarantineError, "same_parent_distinct_identity_required"):
            self.create()
        for kwargs in ({"page": True}, {"page": 0}, {"pageSize": 51}, {"pageSize": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plans.listQuarantinePlans(self.settings, self.actor.id, **kwargs)

    def test_target_cannot_be_a_protected_structure_even_if_configured(self):
        self.state.sql("UPDATE cloud_drive_inventory_entries SET name='BDMV',path=? WHERE id=?", (fixtures.ROOT + "/BDMV", self.ids[3]))
        settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, quarantineFolderName="BDMV"))
        with self.assertRaisesRegex(plans.QuarantineError, "protected_entry"):
            plans.createQuarantinePlan(settings, requestId=str(uuid4()), reviewId=self.review["id"],
                entryIds=self.ids[:1], targetEntryId=self.ids[3], auditContext=self.context)

    def test_cancel_audit_failure_rolls_back_status_revision_and_evidence(self):
        result = self.create()
        with patch.object(plans, "record_audit_event", side_effect=sqlite3.OperationalError("synthetic-audit")):
            with self.assertRaisesRegex(Exception, "storage_unavailable"):
                plans.cancelQuarantinePlan(self.settings, result["id"], expectedRevision=0, auditContext=self.context)
        self.assertEqual(result, plans.getQuarantinePlan(self.settings, result["id"], self.actor.id))

    def test_config_rejects_unsafe_names_and_keeps_legacy_positional_fields(self):
        for name in ("", ".", "..", "CON.txt", "LPT1", "a/b", "a\\b", "a ", "a\u202e", "x"*241, "／other", "e\u0301"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                cloudDriveSettingsFromDict({"quarantine_folder_name": name})
        value = CloudDriveSettings(fixtures.ROOT, 2, 300, False, fixtures.ENDPOINT, "", False, "mapping")
        self.assertEqual("mapping", value.directoryMappingRevision)
        self.assertFalse(value.quarantineEnabled)
        self.assertEqual("_MediaAI_Quarantine", value.quarantineFolderName)


class CloudQuarantinePlanApiTest(unittest.TestCase):
    def setUp(self):
        self.fixture = CloudQuarantinePlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state
        self.client = TestClient(create_app(self.fixture.settings))
        bindCloudConfiguration(self.fixture.settings)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
        self.url = "/api/cloud-drive/quarantine-plans"
        self.payload = {"requestId": str(uuid4()), "reviewId": self.fixture.review["id"],
            "entryIds": self.fixture.ids[:2], "targetEntryId": self.fixture.ids[3]}

    def test_create_lookup_list_cancel_are_local_only_and_unconfirmed_execution_is_rejected(self):
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        with patch("grpc.secure_channel", side_effect=AssertionError("禁止云连接")):
            response = self.client.post(self.url, headers=self.headers, json=self.payload)
            self.assertEqual(201, response.status_code, response.text)
            result = response.json()
            self.assertTrue(result["valid"])
            self.assertFalse(result["writeAvailable"])
            self.assertEqual(result, self.client.get(self.url + "/" + result["id"], headers=self.headers).json())
            self.assertEqual(result, self.client.get("/api/cloud-drive/quarantine-plan-requests/" + self.payload["requestId"], headers=self.headers).json())
            self.assertEqual(1, self.client.get(self.url, headers=self.headers).json()["total"])
            cancelled = self.client.post(self.url + "/" + result["id"] + "/cancel", headers=self.headers, json={"expectedRevision": 0})
            self.assertEqual("cancelled", cancelled.json()["status"])
            self.assertEqual(409, self.client.post(self.url + "/" + result["id"] + "/cancel", headers=self.headers, json={"expectedRevision": 0}).status_code)
            self.assertEqual(422, self.client.post(self.url + "/" + result["id"] + "/execute", headers=self.headers, json={}).status_code)
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))

    def test_auth_current_permissions_policy_and_missing_plan(self):
        self.assertEqual(401, self.client.get(self.url).status_code)
        policy = self.client.get("/api/cloud-drive/quarantine/policy", headers=self.headers).json()
        self.assertTrue(policy["planAvailable"])
        self.assertFalse(policy["executionEnabled"])
        self.assertTrue(policy["executionImplemented"])
        self.assertFalse(policy["executionAvailable"])
        self.assertFalse(policy["writeAvailable"])
        self.assertEqual(404, self.client.get(self.url + "/" + str(uuid4()), headers=self.headers).status_code)
        self.state.sql("UPDATE users SET permissions_json='[\"cloud-quarantine:plan\"]'")
        self.assertEqual(403, self.client.get(self.url, headers=self.headers).status_code)

    def test_parameters_forbid_paths_secrets_bool_ids_and_unbounded_page(self):
        for changes in ({"entryIds": [True]}, {"targetEntryId": False}, {"entryIds": []},
                        {"targetPath": "/arbitrary"}, {"apiToken": "synthetic-not-a-token"}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, headers=self.headers, json={**self.payload, **changes})
                self.assertEqual(422, response.status_code, response.text)
                self.assertNotIn("synthetic-not-a-token", response.text)
        self.assertEqual(422, self.client.get(self.url + "?pageSize=51", headers=self.headers).status_code)
        self.assertEqual(400, self.client.get(self.url + "/invalid-uuid", headers=self.headers).status_code)

    def test_conflict_capacity_and_audit_storage_failures_are_safe_http_errors(self):
        response = self.client.post(self.url, headers=self.headers, json=self.payload)
        self.assertEqual(201, response.status_code)
        self.assertEqual(409, self.client.post(self.url, headers=self.headers, json={**self.payload, "entryIds": self.fixture.ids[:1]}).status_code)
        new = {**self.payload, "requestId": str(uuid4())}
        with patch.object(plans, "MAX_PLANS", 0):
            self.assertEqual(409, self.client.post(self.url, headers=self.headers, json=new).status_code)
        with patch.object(plans, "record_audit_event", side_effect=sqlite3.OperationalError("synthetic-private-detail")):
            failure = self.client.post(self.url, headers=self.headers, json=new)
        self.assertEqual(503, failure.status_code, failure.text)
        self.assertNotIn("synthetic-private-detail", failure.text)
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_plans"))


if __name__ == "__main__":
    unittest.main()
