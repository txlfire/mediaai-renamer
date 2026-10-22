"""广告人工复核仅使用临时库和合成观察，不请求真实CD2。"""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_inventory as fixtures
import test_cloud_inventory_api as apiFixtures
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service import cloud_ad_review_service as reviews
from app.service.cloud_drive_budget_service import CloudBudgetError
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache, setCloudInventoryKeep
from app.service.cloud_inventory_service import CloudInventoryError


class CloudAdReviewTest(unittest.TestCase):
    def setUp(self):
        self.state = fixtures.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings, self.context = self.state.settings, self.state.context
        self.jobId = self.state.job()
        self.state.runJob(self.jobId, fixtures.SyntheticClient({fixtures.ROOT: [
            fixtures.entry("更多资源-www.example.com.txt"), fixtures.entry("poster-更多资源.jpg"),
            fixtures.entry("AD.2024.mkv"), fixtures.entry("更多资源", directory=True)]}))
        self.ids = [row[0] for row in self.state.sql("SELECT id FROM cloud_drive_inventory_entries ORDER BY id")]

    def save(self, **options):
        values = dict(requestId=str(uuid4()), jobId=self.jobId, expectedRevision=self.state.summary(self.jobId)["revision"],
            items=[{"entryId": self.ids[0], "decision": "review", "reason": ""}], confirmLocalOnly=True, auditContext=self.context)
        values.update(options)
        return reviews.createCloudAdReview(self.settings, **values)

    def test_fixed_snapshot_is_owner_scoped_local_and_audited_without_budget_change(self):
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        result = self.save(items=[{"entryId": self.ids[0], "decision": "suggest_quarantine", "reason": "名称包含资源推广与域名，仍需后续核验"}])
        self.assertEqual(["promotional_phrase", "domain_or_url_in_name"], result["items"][0]["evidence"])
        self.assertEqual("medium", result["items"][0]["evidenceLevel"])
        for key in ("networkAccess", "writeAvailable", "executionAuthorized"):
            self.assertFalse(result[key])
        self.assertEqual(0, result["contentReadBytes"])
        self.assertTrue(result["sourceCurrent"])
        self.assertEqual(result, reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id))
        self.assertEqual(result, reviews.getCloudAdReview(self.settings, result["requestId"], self.state.actor.id, byRequest=True))
        self.assertEqual(1, reviews.listCloudAdReviews(self.settings, self.state.actor.id)["total"])
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        detail = json.loads(self.state.sql("SELECT detail_json FROM audit_events WHERE action='ad_review_saved'")[0][0])
        self.assertEqual(0, detail["remoteActions"])
        self.assertNotIn(result["items"][0]["name"], str(detail))

    def test_uuid_is_idempotent_but_changed_payload_is_rejected(self):
        requestId = str(uuid4())
        first = self.save(requestId=requestId)
        self.assertEqual(first, self.save(requestId=requestId))
        with self.assertRaisesRegex(CloudInventoryError, "request_conflict"):
            self.save(requestId=requestId, items=[{"entryId": self.ids[0], "decision": "keep", "reason": ""}])
        self.assertEqual([(1,)], self.state.sql("SELECT COUNT(*) FROM cloud_ad_reviews"))

    def test_protections_override_human_decisions_and_keeps_are_rechecked(self):
        for entryId in (self.ids[1],):
            for decision in ("review", "suggest_quarantine", "suggest_delete"):
                with self.subTest(decision=decision), self.assertRaisesRegex(CloudInventoryError, "protected_entry"):
                    self.save(items=[{"entryId": entryId, "decision": decision, "reason": "人工理由"}])
        result = self.save(items=[{"entryId": self.ids[1], "decision": "keep", "reason": "保留海报"}])
        self.assertEqual("protected_artwork", result["items"][0]["protection"])
        revision = self.state.summary(self.jobId)["revision"]
        setCloudInventoryKeep(self.settings, self.jobId, self.ids[0], kept=True, expectedRevision=revision, auditContext=self.context)
        with self.assertRaisesRegex(CloudInventoryError, "protected_entry"):
            self.save(items=[{"entryId": self.ids[0], "decision": "suggest_delete", "reason": "不能绕过保留"}])

    def test_extension_size_or_ad_word_alone_cannot_justify_suggestion(self):
        with self.assertRaisesRegex(CloudInventoryError, "review_evidence_required"):
            self.save(items=[{"entryId": self.ids[2], "decision": "suggest_delete", "reason": "名字看起来像广告"}])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET remote_id=NULL WHERE id=?", (self.ids[0],))
        with self.assertRaisesRegex(CloudInventoryError, "review_evidence_required"):
            self.save(items=[{"entryId": self.ids[0], "decision": "suggest_delete", "reason": "缺身份"}])

    def test_rejects_unsafe_types_duplicates_large_or_unconfirmed_input(self):
        base = {"entryId": self.ids[0], "decision": "review", "reason": ""}
        for values in ([], [base] * 101, [base, base], [{**base, "entryId": True}], [{**base, "reason": "a\nb"}],
                [{**base, "reason": "x" * 501}], [{**base, "reason": " padded "}], [{**base, "decision": "delete"}],
                [{**base, "path": "/other"}], [{**base, "decision": "suggest_delete"}]):
            with self.subTest(values=str(values)[:60]), self.assertRaises(ValueError):
                self.save(items=values)
        with self.assertRaises(ValueError):
            self.save(confirmLocalOnly=False)

    def test_foreign_entries_directories_and_incomplete_generations_stop(self):
        with self.assertRaisesRegex(CloudInventoryError, "entry_not_found"):
            self.save(items=[{"entryId": 999999, "decision": "review", "reason": ""}])
        with self.assertRaisesRegex(CloudInventoryError, "review_file_incomplete"):
            self.save(items=[{"entryId": self.ids[3], "decision": "keep", "reason": ""}])
        self.state.sql("UPDATE cloud_drive_inventory_entries SET generation=generation+1 WHERE id=?", (self.ids[0],))
        with self.assertRaisesRegex(CloudInventoryError, "review_file_incomplete"):
            self.save()

    def test_revision_path_and_source_cache_changes_are_visible_without_losing_evidence(self):
        result = self.save()
        self.state.sql("UPDATE cloud_drive_inventory_jobs SET revision=revision+1 WHERE id=?", (self.jobId,))
        value = reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id)
        self.assertFalse(value["sourceCurrent"])
        self.assertEqual("observation_changed", value["staleReason"])
        with self.assertRaisesRegex(CloudInventoryError, "observation_changed"):
            self.save(expectedRevision=result["inventoryRevision"])
        clearCloudInventoryCache(self.settings, self.jobId, expectedRevision=self.state.summary(self.jobId)["revision"],
            confirmClear=True, auditContext=self.context)
        historical = reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id)
        self.assertEqual("observations_cleared", historical["staleReason"])
        self.assertEqual(result["snapshotHash"], historical["snapshotHash"])
        self.assertEqual(result["items"], historical["items"])

    def test_epoch_config_and_rule_change_do_not_claim_current_snapshot(self):
        result = self.save()
        changed = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive, directoryMappingRevision="changed"))
        self.assertEqual("scope_changed", reviews.getCloudAdReview(changed, result["id"], self.state.actor.id)["staleReason"])
        with patch.object(reviews, "RULE_VERSION", "next-rules"):
            self.assertEqual("observation_changed", reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id)["staleReason"])
        self.state.sql("UPDATE cloud_directory_path_epoch SET revision=revision+1")
        with self.assertRaisesRegex(CloudInventoryError, "path_revision_changed"):
            self.save()
        self.assertEqual("path_revision_changed", reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id)["staleReason"])

    def test_capacity_count_bytes_and_audit_failure_are_transactional(self):
        with patch.object(reviews, "record_audit_event", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self.save()
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_ad_reviews"))
        self.save()
        for constant, value in (("MAX_REVIEW_RECORDS", 1), ("MAX_TOTAL_BYTES", 1), ("MAX_REVIEW_BYTES", 1)):
            with self.subTest(constant=constant), patch.object(reviews, constant, value):
                with self.assertRaisesRegex(CloudInventoryError, "review_capacity"):
                    self.save()

    def test_owner_permission_pagination_and_schema50_migration(self):
        result = self.save()
        with self.assertRaises(CloudBudgetError):
            reviews.getCloudAdReview(self.settings, result["id"], self.state.actor.id + 100)
        self.assertEqual([], reviews.listCloudAdReviews(self.settings, self.state.actor.id, page=2)["items"])
        with self.assertRaises(ValueError):
            reviews.listCloudAdReviews(self.settings, self.state.actor.id, pageSize=51)
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        self.state.sql("DROP TABLE cloud_ad_reviews")
        self.state.sql("UPDATE app_meta SET value='50' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))


class CloudAdReviewApiTest(unittest.TestCase):
    def setUp(self):
        self.state = apiFixtures.CloudInventoryApiTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.url = "/api/cloud-drive/ad-reviews"

    def test_auth_validation_and_no_remote_action_routes(self):
        self.assertEqual(401, self.state.client.get(self.url).status_code)
        self.assertEqual(200, self.state.client.get(self.url, headers=self.state.headers).status_code)
        self.assertEqual(422, self.state.client.get(self.url + "?pageSize=51", headers=self.state.headers).status_code)
        payload = {"requestId": str(uuid4()), "jobId": self.state.jobId, "expectedRevision": 0,
            "items": [{"entryId": True, "decision": "review", "reason": ""}], "confirmLocalOnly": True}
        self.assertEqual(422, self.state.client.post(self.url, headers=self.state.headers, json=payload).status_code)
        self.assertEqual(404, self.state.client.get("/api/cloud-drive/ad-review-requests/" + str(uuid4()), headers=self.state.headers).status_code)
        self.assertEqual(405, self.state.client.post(self.url + "/delete", headers=self.state.headers, json={}).status_code)
        self.state.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.state.actor.id,))
        self.assertEqual(403, self.state.client.get(self.url, headers=self.state.headers).status_code)

    def test_http_save_query_history_and_foreign_owner_use_the_product_routes(self):
        runner = fixtures.CloudInventoryTest()
        runner.settings = self.state.settings
        root = "/cloud/movies"
        runner.runJob(self.state.jobId, fixtures.SyntheticClient({root: [fixtures.entry("更多资源.txt", parent=root)]}))
        with reviews.inventoryDb(self.state.settings) as connection:
            entryId = connection.execute("SELECT id FROM cloud_drive_inventory_entries").fetchone()[0]
            before = tuple(connection.execute("SELECT used_rpc FROM cloud_drive_read_sessions").fetchone())
        revision = self.state.client.get(self.state.url, headers=self.state.headers).json()["revision"]
        payload = {"requestId": str(uuid4()), "jobId": self.state.jobId, "expectedRevision": revision,
            "items": [{"entryId": entryId, "decision": "suggest_quarantine", "reason": "人工复核推广文件"}], "confirmLocalOnly": True}
        response = self.state.client.post(self.url, headers=self.state.headers, json=payload)
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        self.assertFalse(result["executionAuthorized"])
        self.assertEqual(result, self.state.client.get(self.url + "/" + result["id"], headers=self.state.headers).json())
        lookup = "/api/cloud-drive/ad-review-requests/" + payload["requestId"]
        self.assertEqual(result, self.state.client.get(lookup, headers=self.state.headers).json())
        self.assertEqual(1, self.state.client.get(self.url, headers=self.state.headers).json()["total"])
        self.state.sql("UPDATE cloud_ad_reviews SET actor_id=actor_id+1")
        self.assertEqual(404, self.state.client.get(lookup, headers=self.state.headers).status_code)
        self.assertEqual(0, self.state.client.get(self.url, headers=self.state.headers).json()["total"])
        with reviews.inventoryDb(self.state.settings) as connection:
            self.assertEqual(before, tuple(connection.execute("SELECT used_rpc FROM cloud_drive_read_sessions").fetchone()))
