"""仅临时库验证本次保留例外及分批观察清理，不访问云盘。"""

import os
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_cloud_inventory as fixtures
import test_cloud_inventory_api as apiFixtures
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service import cloud_inventory_lifecycle_service as lifecycle
from app.service.cloud_inventory_advice_service import buildCloudInventoryAdvice
from app.service.cloud_inventory_service import CloudInventoryError, cloudInventoryEntries, continueCloudInventoryQuantum


class CloudInventoryLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.state = fixtures.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings = self.state.settings
        self.context = self.state.context

    def observed(self, count=2):
        jobId = self.state.job()
        self.state.runJob(jobId, fixtures.SyntheticClient({fixtures.ROOT:
            [fixtures.entry(f"更多资源-{index}.txt") for index in range(count)]}))
        return jobId

    def clear(self, jobId, revision=None, **options):
        return lifecycle.clearCloudInventoryCache(self.settings, jobId,
            expectedRevision=self.state.summary(jobId)["revision"] if revision is None else revision,
            confirmClear=True, auditContext=self.context, **options)

    def keep(self, jobId, entryId, kept=True):
        return lifecycle.setCloudInventoryKeep(self.settings, jobId, entryId, kept=kept,
            expectedRevision=self.state.summary(jobId)["revision"], auditContext=self.context)

    def advice(self, jobId):
        return buildCloudInventoryAdvice(self.settings, jobId, self.state.actor.id)

    def test_schema41_upgrade_preserves_observations_approval_and_scope(self):
        jobId = self.observed()
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        self.state.sql("DROP TABLE cloud_drive_inventory_keeps")
        for name in ("cache_state", "selected_json", "cleared_at"):
            self.state.sql(f"ALTER TABLE cloud_drive_inventory_jobs DROP COLUMN {name}")
        self.state.sql("UPDATE app_meta SET value='41' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        summary = self.state.summary(jobId)
        self.assertEqual([fixtures.ROOT], summary["selectedPaths"])
        self.assertEqual("available", summary["cacheState"])
        self.assertEqual(2, summary["storedEntries"])
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.state.sql("SELECT value FROM app_meta WHERE key='schema_version'"))

    def test_saved_keep_survives_refresh_but_not_new_inventory_or_unset(self):
        jobId = self.observed()
        entryId = self.advice(jobId)["items"][0]["id"]
        before = self.state.sql("SELECT COUNT(*) FROM audit_events")
        self.keep(jobId, entryId)
        result = self.advice(jobId)
        self.assertEqual("user_keep", result["items"][0]["protection"])
        self.assertTrue(result["items"][0]["userKept"])
        self.assertFalse(result["keepExceptionsPersisted"])
        self.assertEqual("current_job_entries", result["savedKeepScope"])
        self.keep(jobId, entryId)
        self.assertEqual(before[0][0] + 1, self.state.sql("SELECT COUNT(*) FROM audit_events")[0][0])
        another = self.observed()
        self.assertTrue(all(not item["userKept"] for item in self.advice(another)["items"]))
        self.keep(jobId, entryId, False)
        self.assertEqual("review", self.advice(jobId)["items"][0]["decision"])

    def test_foreign_entry_stale_revision_and_invalid_types_are_rejected(self):
        jobId, other = self.observed(), self.observed()
        entryId = self.advice(other)["items"][0]["id"]
        with self.assertRaisesRegex(CloudInventoryError, "entry_not_found"):
            self.keep(jobId, entryId)
        with self.assertRaisesRegex(CloudInventoryError, "revision_conflict"):
            lifecycle.setCloudInventoryKeep(self.settings, jobId, entryId, kept=True,
                expectedRevision=999, auditContext=self.context)
        with self.assertRaises(ValueError):
            lifecycle.setCloudInventoryKeep(self.settings, jobId, True, kept=True,
                expectedRevision=1, auditContext=self.context)

    def test_clear_is_bounded_resumable_and_preserves_lifetime_budget_and_approval(self):
        jobId = self.observed(4501)
        entryId = self.advice(jobId)["items"][0]["id"]
        self.keep(jobId, entryId)
        self.state.sql("UPDATE cloud_drive_read_sessions SET used_rpc=7")
        before = self.state.sql("SELECT * FROM cloud_drive_read_sessions")
        request = self.state.sql("SELECT request_id,request_hash,session_id FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,))
        first = self.clear(jobId)
        self.assertEqual("clearing", first["cacheState"])
        self.assertEqual(501, first["storedEntries"])
        self.assertEqual(4501, first["receivedEntries"])
        with self.assertRaisesRegex(CloudInventoryError, "observations_clearing"):
            cloudInventoryEntries(self.settings, jobId, self.state.actor.id)
        final = self.clear(jobId)
        self.assertEqual("cleared", final["cacheState"])
        self.assertFalse(final["cacheClearAvailable"])
        self.assertEqual(0, final["storedEntries"])
        self.assertEqual(4501, final["receivedEntries"])
        self.assertEqual([fixtures.ROOT], final["selectedPaths"])
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual(request, self.state.sql("SELECT request_id,request_hash,session_id FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,)))
        self.assertEqual([(0,)], self.state.sql("SELECT stored_bytes FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,)))
        self.assertEqual([], self.state.sql("SELECT * FROM cloud_drive_inventory_keeps"))
        actions = [row[0] for row in self.state.sql("SELECT action FROM audit_events WHERE target_id=?", (jobId,))]
        self.assertEqual(1, actions.count("inventory_cache_clear_start"))
        self.assertEqual(1, actions.count("inventory_cache_clear_complete"))
        self.assertIn("inventory_keep_set", actions)
        self.assertEqual(final, self.clear(jobId, revision=0))
        with self.assertRaisesRegex(CloudInventoryError, "observations_cleared"):
            self.advice(jobId)
        with self.assertRaises(CloudInventoryError):
            continueCloudInventoryQuantum(self.settings, jobId, expectedRevision=final["revision"],
                confirmContinue=True, auditContext=self.context)

    def test_pending_paused_and_worker_held_jobs_cannot_be_cleared(self):
        jobId = self.state.job()
        with self.assertRaisesRegex(CloudInventoryError, "cache_clear_blocked"):
            self.clear(jobId)
        for status, token in (("paused", None), ("running", None), ("completed", str(uuid4()))):
            self.state.sql("UPDATE cloud_drive_inventory_jobs SET status=?,worker_token=? WHERE id=?", (status, token, jobId))
            self.assertFalse(self.state.summary(jobId)["cacheClearAvailable"])
            with self.assertRaisesRegex(CloudInventoryError, "cache_clear_blocked"):
                self.clear(jobId)

    def test_unknown_rpc_is_not_released_or_cleared(self):
        jobId = self.observed()
        sessionId = self.state.sql("SELECT session_id FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,))[0][0]
        self.state.sql("INSERT INTO cloud_drive_rpc_lease(pool_id,id,session_id,owner_pid,owner_instance,owner_host,phase,created_at) "
            "VALUES(1,?,?,?,'test','test','unknown',?)", (str(uuid4()), sessionId, os.getpid(), time.time()))
        before = self.state.sql("SELECT * FROM cloud_drive_rpc_lease")
        self.assertFalse(self.state.summary(jobId)["cacheClearAvailable"])
        with self.assertRaisesRegex(CloudInventoryError, "cache_clear_blocked"):
            self.clear(jobId)
        self.assertEqual(before, self.state.sql("SELECT * FROM cloud_drive_rpc_lease"))
        self.assertEqual(2, self.state.summary(jobId)["storedEntries"])

    def test_revision_conflict_and_audit_failure_leave_observations_intact(self):
        jobId = self.observed()
        with self.assertRaisesRegex(CloudInventoryError, "revision_conflict"):
            self.clear(jobId, revision=0)
        with patch.object(lifecycle, "record_audit_event", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.clear(jobId)
        self.assertEqual("available", self.state.summary(jobId)["cacheState"])
        self.assertEqual(2, len(self.advice(jobId)["items"]))

    def test_interrupted_cleanup_commits_only_bounded_chunk_and_resumes(self):
        jobId = self.observed(2500)
        original = lifecycle._owned
        calls = 0
        def interrupt(connection, jobId, context):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic request interruption")
            return original(connection, jobId, context)
        with patch.object(lifecycle, "_owned", side_effect=interrupt), self.assertRaises(RuntimeError):
            self.clear(jobId)
        summary = self.state.summary(jobId)
        self.assertEqual("clearing", summary["cacheState"])
        self.assertEqual(1500, summary["storedEntries"])
        self.assertEqual("cleared", self.clear(jobId)["cacheState"])


class CloudInventoryLifecycleApiTest(unittest.TestCase):
    def setUp(self):
        self.state = apiFixtures.CloudInventoryApiTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)

    def test_strict_owner_confirm_and_cleared_status(self):
        client, headers, url = self.state.client, self.state.headers, self.state.url
        for payload in ({"confirmClear": True, "expectedRevision": True},
                        {"confirmClear": "true", "expectedRevision": 0},
                        {"confirmClear": True, "expectedRevision": 0, "token": "synthetic"}):
            self.assertEqual(422, client.post(url + "/clear-cache", headers=headers, json=payload).status_code)
        payload = {"confirmClear": True, "expectedRevision": 0}
        self.assertEqual(401, client.post(url + "/clear-cache", json=payload).status_code)
        self.assertEqual(409, client.post(url + "/clear-cache", headers=headers, json=payload).status_code)
        client.post(url + "/cancel", headers=headers)
        revision = client.get(url, headers=headers).json()["revision"]
        self.assertEqual(400, client.post(url + "/clear-cache", headers=headers,
            json={"confirmClear": False, "expectedRevision": revision}).status_code)
        result = client.post(url + "/clear-cache", headers=headers, json={**payload, "expectedRevision": revision})
        self.assertEqual(200, result.status_code)
        self.assertEqual("cleared", result.json()["cacheState"])
        self.assertEqual(409, client.get(url + "/entries", headers=headers).status_code)
        self.state.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        self.assertEqual(404, client.post(url + "/clear-cache", headers=headers, json=payload).status_code)
        self.assertEqual(404, client.put(url + "/entries/1/keep", headers=headers,
            json={"kept": True, "expectedRevision": 0}).status_code)

    def test_keep_update_and_202_cleanup_use_same_owner_revision_fences(self):
        fixtures.CloudInventoryTest.runJob(self.state, self.state.jobId,
            fixtures.SyntheticClient({"/cloud/movies": [fixtures.entry(f"更多资源-{index}.txt", parent="/cloud/movies") for index in range(4501)]}))
        client, headers, url = self.state.client, self.state.headers, self.state.url
        page = client.post(url + "/advice", headers=headers, json={}).json()
        entryId = page["items"][0]["id"]
        revision = page["jobRevision"]
        keepUrl = url + f"/entries/{entryId}/keep"
        self.assertEqual(422, client.put(keepUrl, headers=headers, json={"kept": "true", "expectedRevision": revision}).status_code)
        kept = client.put(keepUrl, headers=headers, json={"kept": True, "expectedRevision": revision})
        self.assertEqual(200, kept.status_code)
        self.assertTrue(client.post(url + "/advice", headers=headers, json={}).json()["items"][0]["userKept"])
        stale = client.post(url + "/clear-cache", headers=headers, json={"confirmClear": True, "expectedRevision": revision})
        self.assertEqual(409, stale.status_code)
        first = client.post(url + "/clear-cache", headers=headers,
            json={"confirmClear": True, "expectedRevision": kept.json()["revision"]})
        self.assertEqual(202, first.status_code)
        self.assertEqual(501, first.json()["storedEntries"])
        self.assertEqual(409, client.put(keepUrl, headers=headers,
            json={"kept": False, "expectedRevision": first.json()["revision"]}).status_code)
        final = client.post(url + "/clear-cache", headers=headers,
            json={"confirmClear": True, "expectedRevision": first.json()["revision"]})
        self.assertEqual(200, final.status_code)
        self.assertEqual("cleared", final.json()["cacheState"])
