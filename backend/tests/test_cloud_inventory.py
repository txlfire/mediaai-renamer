"""盘点队列只使用临时SQLite和合成目录，不连接CD2或115。"""

from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cd2_metadata_client import CloudEntry, DirectoryObservation
from app.service.cloud_drive_budget_service import CloudBudgetError, createCloudReadSession
from app.service import cloud_inventory_service as inventory
from app.service import cloud_inventory_worker_service as workers

ROOT = "/test-cloud/movies"
ENDPOINT = "https://cd2.example"


class SyntheticClient:
    def __init__(self, tree, fail=None):
        self.tree, self.fail, self.calls = tree, fail, []

    def consumeDirectory(self, path, sink, **options):
        self.calls.append(path)
        entries = self.tree.get(path, [])
        for start in range(0, len(entries), 128):
            chunk = tuple(entries[start:start + 128])
            sink.messageReceived(len(chunk), len(chunk) * 64)
            sink.writeEntries(chunk)
        if self.fail:
            raise inventory.CloudInventoryError(self.fail)
        return DirectoryObservation(path, (), True, "stream_finished", len(entries), len(entries) * 64)


def entry(name, *, parent=ROOT, directory=False):
    return CloudEntry("id-" + name, name, parent + "/" + name, directory, 0 if directory else 10)


class CloudInventoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False), cloudDrive=CloudDriveSettings(ROOT))
        ensure_database(self.settings)
        self.actor = create_user(self.settings, "operator", "test", "TestOnly123!", ["scan:run"], True)
        self.context = AuditWriteContext(self.actor)

    def test_safe_stop_reason_rejects_arbitrary_remote_text_and_non_strings(self):
        for value in (None, ["not_found"], {"code": "not_found"}, "not_found with private token", "private-server-detail"):
            with self.subTest(value=value):
                self.assertEqual("worker_failed", inventory.safeCloudInventoryReason(value))
        for value in ("not_found", "invalid_argument", "worker_ownership_lost", "entry_budget"):
            self.assertEqual(value, inventory.safeCloudInventoryReason(value))

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(query, args).fetchall()
            connection.commit()
            return rows

    def job(self, **options):
        session = createCloudReadSession(self.settings, requestId=str(uuid4()), endpoint=ENDPOINT,
            rootPath=ROOT, maxRpc=100, durationSeconds=900, auditContext=self.context)
        params = dict(requestId=str(uuid4()), sessionId=session["id"], selectedPaths=[ROOT], excludedPaths=[], auditContext=self.context)
        params.update(options)
        return inventory.createCloudInventoryJob(self.settings, **params)

    def runJob(self, jobId, client):
        token = inventory.claimCloudInventoryJob(self.settings, jobId)
        inventory.persistCloudInventoryPid(self.settings, jobId, token, os.getpid())
        try:
            inventory.executeCloudInventoryQuantum(self.settings, jobId, token, client)
        finally:
            # 单元测试在本进程执行量子；只在此替换退出探测，生产必须回收真实子进程。
            with patch.object(workers, "isProcessAlive", return_value=False):
                workers._finishOwnedProcess(self.settings, jobId, token, os.getpid())

    def summary(self, jobId):
        return inventory.cloudInventorySummary(self.settings, jobId, self.actor.id)

    def test_schema40_migration_preserves_budget_and_business_rows(self):
        jobId = self.job()
        before = self.sql("SELECT * FROM cloud_drive_read_sessions")
        self.sql("DROP TABLE cloud_drive_inventory_entries")
        self.sql("DROP TABLE cloud_drive_inventory_directories")
        self.sql("DROP TABLE cloud_drive_inventory_jobs")
        self.sql("UPDATE app_meta SET value='40' WHERE key='schema_version'")
        self.sql("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES(1,'completed',100,1,'old')")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(before, self.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertEqual([("completed",)], self.sql("SELECT status FROM scan_jobs"))
        self.assertEqual([(str(CURRENT_SCHEMA_VERSION),)], self.sql("SELECT value FROM app_meta WHERE key='schema_version'"))
        self.assertEqual([], self.sql("SELECT id FROM cloud_drive_inventory_jobs"))

    def test_idempotent_approval_cannot_change_limits_or_bind_second_job(self):
        jobId = self.job()
        requestId, sessionId = self.sql("SELECT request_id,session_id FROM cloud_drive_inventory_jobs")[0]
        params = dict(requestId=requestId, sessionId=sessionId, selectedPaths=[ROOT], excludedPaths=[], auditContext=self.context)
        self.runJob(jobId, SyntheticClient({ROOT: [entry("one.mkv")]}))
        self.assertEqual(jobId, inventory.createCloudInventoryJob(self.settings, **params))
        self.assertEqual(1, self.summary(jobId)["receivedEntries"])
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            inventory.createCloudInventoryJob(self.settings, **params, maxEntries=20)
        self.assertEqual("request_conflict", caught.exception.code)
        params["requestId"] = str(uuid4())
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            inventory.createCloudInventoryJob(self.settings, **params)
        self.assertEqual("session_already_bound", caught.exception.code)

    def test_overview_includes_direct_files_without_descending(self):
        jobId = self.job()
        client = SyntheticClient({ROOT: [entry("one.mkv"), entry("category", directory=True)]})
        self.runJob(jobId, client)
        self.assertEqual([ROOT], client.calls)
        self.assertTrue(self.summary(jobId)["scopeComplete"])
        rows = inventory.cloudInventoryEntries(self.settings, jobId, self.actor.id)["items"]
        self.assertEqual(["one.mkv", "category"], [row["name"] for row in rows])
        self.assertTrue(all(row["directoryComplete"] for row in rows))

    def test_summary_distinguishes_approved_root_from_selected_and_excluded_scope(self):
        jobId = self.job(mode="subtrees", selectedPaths=[ROOT + "/category", ROOT + "/category/sub"],
                         excludedPaths=[ROOT + "/category/keep"])
        result = self.summary(jobId)
        self.assertEqual(ROOT, result["approvedRoot"])
        self.assertEqual([ROOT + "/category"], result["selectedPaths"])
        self.assertEqual([ROOT + "/category/keep"], result["excludedPaths"])
        self.assertEqual("unknown", result["freshness"])

    def test_quantum_continue_preserves_total_and_expiry_and_skips_completed_directory(self):
        jobId = self.job(mode="subtrees", quantumDirectories=1)
        expiry = self.sql("SELECT expires_at FROM cloud_drive_inventory_jobs")[0][0]
        client = SyntheticClient({ROOT: [entry("category", directory=True), entry("one.mkv")],
            ROOT + "/category": [entry("two.mkv", parent=ROOT + "/category")]})
        self.runJob(jobId, client)
        summary = self.summary(jobId)
        self.assertEqual(("paused", "quantum_boundary", 2), (summary["status"], summary["reason"], summary["receivedEntries"]))
        with self.assertRaises(inventory.CloudInventoryError):
            inventory.continueCloudInventoryQuantum(self.settings, jobId, auditContext=self.context, expectedRevision=0, confirmContinue=True)
        inventory.continueCloudInventoryQuantum(self.settings, jobId, auditContext=self.context,
            expectedRevision=summary["revision"], confirmContinue=True)
        self.runJob(jobId, client)
        self.assertEqual([ROOT, ROOT + "/category"], client.calls)
        self.assertEqual(3, self.summary(jobId)["receivedEntries"])
        self.assertTrue(self.summary(jobId)["scopeComplete"])
        self.assertEqual(expiry, self.sql("SELECT expires_at FROM cloud_drive_inventory_jobs")[0][0])

    def test_exclusions_and_parent_selection_do_not_duplicate_or_descend(self):
        jobId = self.job(mode="subtrees", selectedPaths=[ROOT, ROOT + "/keep"], excludedPaths=[ROOT + "/skip"])
        client = SyntheticClient({ROOT: [entry("keep", directory=True), entry("skip", directory=True), entry("file.txt")]})
        self.runJob(jobId, client)
        self.assertEqual([ROOT, ROOT + "/keep"], client.calls)
        self.assertEqual(3, self.summary(jobId)["receivedEntries"])
        self.assertEqual(2, self.summary(jobId)["storedEntries"])

    def test_partial_directory_keeps_observations_but_cannot_continue(self):
        jobId = self.job()
        with self.assertRaises(inventory.CloudInventoryError):
            self.runJob(jobId, SyntheticClient({ROOT: [entry("first")]}, fail="unavailable"))
        summary = self.summary(jobId)
        self.assertFalse(summary["scopeComplete"])
        self.assertEqual({"partial": 1}, summary["directoryStates"])
        self.assertFalse(inventory.cloudInventoryEntries(self.settings, jobId, self.actor.id)["items"][0]["directoryComplete"])
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            inventory.continueCloudInventoryQuantum(self.settings, jobId, auditContext=self.context,
                expectedRevision=summary["revision"], confirmContinue=True)
        self.assertEqual("job_not_resumable", caught.exception.code)

    def test_duplicate_chunk_rolls_back_rows_but_not_received_budget(self):
        jobId = self.job()
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            self.runJob(jobId, SyntheticClient({ROOT: [entry("same"), entry("same")]}))
        self.assertEqual("duplicate_entry", caught.exception.code)
        self.assertEqual((2, 0), (self.summary(jobId)["receivedEntries"], self.summary(jobId)["storedEntries"]))

    def test_received_budget_overshoot_is_retained_and_not_reported_complete(self):
        jobId = self.job(maxEntries=1)
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            self.runJob(jobId, SyntheticClient({ROOT: [entry("a"), entry("b")]}))
        self.assertEqual("entry_budget", caught.exception.code)
        self.assertEqual(2, self.summary(jobId)["receivedEntries"])
        self.assertEqual(0, self.summary(jobId)["storedEntries"])

    def test_byte_budget_charges_received_message_and_cache_checks_logical_bytes(self):
        first = self.job(maxBytes=1024)
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            self.runJob(first, SyntheticClient({ROOT: [entry(str(i)) for i in range(20)]}))
        self.assertEqual("byte_budget", caught.exception.code)
        self.assertEqual(1280, self.summary(first)["receivedBytes"])
        second = self.job(maxBytes=1024)
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            self.runJob(second, SyntheticClient({ROOT: [entry("a" * 900)]}))
        self.assertEqual("cache_capacity", caught.exception.code)
        self.assertEqual(0, self.summary(second)["storedEntries"])

    def test_capacity_stops_without_deleting_history(self):
        jobId = self.job()
        with patch.object(inventory, "MAX_CACHE_ENTRIES", 1):
            with self.assertRaises(inventory.CloudInventoryError):
                self.runJob(jobId, SyntheticClient({ROOT: [entry("a"), entry("b")]}))
        self.assertEqual("cache_capacity", self.summary(jobId)["reason"])
        self.assertEqual(1, len(self.sql("SELECT id FROM cloud_drive_inventory_jobs")))

    def test_full_cache_stops_before_client_or_directory_is_opened(self):
        first = self.job()
        self.runJob(first, SyntheticClient({ROOT: [entry("first")]}))
        second = self.job()
        client = SyntheticClient({ROOT: [entry("second")]})
        with patch.object(inventory, "MAX_CACHE_ENTRIES", 1):
            with self.assertRaises(inventory.CloudInventoryError) as caught:
                self.runJob(second, client)
        self.assertEqual("cache_capacity", caught.exception.code)
        self.assertEqual([], client.calls)
        self.assertEqual({"pending": 1}, self.summary(second)["directoryStates"])
        self.assertEqual(1, self.summary(first)["storedEntries"])

    def test_one_remaining_rpc_cannot_start_a_two_rpc_directory_read(self):
        jobId = self.job()
        self.sql("UPDATE cloud_drive_read_sessions SET max_rpc=1")
        client = SyntheticClient({ROOT: []})
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            self.runJob(jobId, client)
        self.assertEqual("session_exhausted", caught.exception.code)
        self.assertEqual([], client.calls)
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertEqual({"pending": 1}, self.summary(jobId)["directoryStates"])

    def test_depth_directory_and_queue_limits_never_claim_scope_complete(self):
        for options, reason in (({"maxDepth": 0}, "depth_budget"), ({"maxDirectories": 1}, "directory_budget")):
            with self.subTest(options=options):
                jobId = self.job(mode="subtrees", **options)
                self.runJob(jobId, SyntheticClient({ROOT: [entry("category", directory=True)]}))
                self.assertEqual(reason, self.summary(jobId)["reason"])
                self.assertFalse(self.summary(jobId)["scopeComplete"])
        jobId = self.job(mode="subtrees")
        with patch.object(inventory, "MAX_DIRECTORY_ROWS", 3):
            self.runJob(jobId, SyntheticClient({ROOT: [entry("category", directory=True)]}))
        self.assertEqual("queue_capacity", self.summary(jobId)["reason"])

    def test_paging_is_keyset_bounded_and_owner_isolated(self):
        jobId = self.job()
        self.runJob(jobId, SyntheticClient({ROOT: [entry(str(i)) for i in range(205)]}))
        first = inventory.cloudInventoryEntries(self.settings, jobId, self.actor.id, limit=200)
        second = inventory.cloudInventoryEntries(self.settings, jobId, self.actor.id, afterId=first["nextId"], limit=200)
        self.assertEqual((200, 5), (len(first["items"]), len(second["items"])))
        self.assertTrue(first["hasMore"])
        self.assertFalse(second["hasMore"])
        self.assertNotIn("worker_token", str(self.summary(jobId)))
        for function in (inventory.cloudInventorySummary, inventory.cloudInventoryEntries):
            with self.assertRaises(inventory.CloudInventoryError) as caught:
                function(self.settings, jobId, self.actor.id + 1)
            self.assertEqual("job_not_found", caught.exception.code)
        with self.assertRaises(ValueError):
            inventory.cloudInventoryEntries(self.settings, jobId, self.actor.id, limit=201)

    def test_worker_slot_and_stale_token_are_fenced(self):
        first, second = self.job(), self.job()
        token = inventory.claimCloudInventoryJob(self.settings, first)
        inventory.persistCloudInventoryPid(self.settings, first, token, os.getpid())
        with self.assertRaises(inventory.CloudInventoryError) as caught:
            inventory.claimCloudInventoryJob(self.settings, second)
        self.assertEqual("worker_slot_busy", caught.exception.code)
        with self.assertRaises(inventory.CloudInventoryError):
            inventory.executeCloudInventoryQuantum(self.settings, first, str(uuid4()), SyntheticClient({}))
        self.assertEqual("running", self.summary(first)["status"])
        self.assertTrue(self.summary(first)["workerSlotHeld"])

    def test_cancel_never_releases_live_worker_and_blocks_next_directory(self):
        jobId = self.job()
        token = inventory.claimCloudInventoryJob(self.settings, jobId)
        inventory.persistCloudInventoryPid(self.settings, jobId, token, os.getpid())
        result = inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.context)
        self.assertTrue(result["workerSlotHeld"])
        client = SyntheticClient({})
        with self.assertRaises(inventory.CloudInventoryError):
            inventory.executeCloudInventoryQuantum(self.settings, jobId, token, client)
        self.assertEqual([], client.calls)
        with self.assertRaises(inventory.CloudInventoryError):
            workers._finishOwnedProcess(self.settings, jobId, token, os.getpid())
        self.assertTrue(self.summary(jobId)["workerSlotHeld"])

    def test_queued_cancel_is_terminal_and_idempotent(self):
        jobId = self.job()
        first = inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.context)
        second = inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.context)
        self.assertEqual("cancelled", first["status"])
        self.assertEqual(first["revision"], second["revision"])

    def test_cancel_after_quantum_exit_but_before_reap_is_terminal(self):
        jobId = self.job(mode="subtrees", quantumDirectories=1)
        token = inventory.claimCloudInventoryJob(self.settings, jobId)
        inventory.persistCloudInventoryPid(self.settings, jobId, token, os.getpid())
        inventory.executeCloudInventoryQuantum(self.settings, jobId, token,
            SyntheticClient({ROOT: [entry("category", directory=True)]}))
        self.assertEqual("paused", self.summary(jobId)["status"])
        inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.context)
        with patch.object(workers, "isProcessAlive", return_value=False):
            workers._finishOwnedProcess(self.settings, jobId, token, os.getpid())
        self.assertEqual("cancelled", self.summary(jobId)["status"])
        self.assertFalse(self.summary(jobId)["workerSlotHeld"])

    def test_permission_expiry_and_scope_revocation_prevent_claim(self):
        jobId = self.job()
        self.sql("UPDATE users SET enabled=0 WHERE id=?", (self.actor.id,))
        with self.assertRaises(CloudBudgetError):
            inventory.claimCloudInventoryJob(self.settings, jobId)
        self.sql("UPDATE users SET enabled=1 WHERE id=?", (self.actor.id,))
        revoked = replace(self.settings, cloudDrive=CloudDriveSettings(ROOT + "/other"))
        with self.assertRaises(CloudBudgetError):
            inventory.claimCloudInventoryJob(revoked, jobId)
        self.sql("UPDATE cloud_drive_read_sessions SET expires_at=?", (time.time() - 1,))
        with self.assertRaises(CloudBudgetError):
            inventory.claimCloudInventoryJob(self.settings, jobId)

    def test_audit_failure_rolls_back_approval_and_cancel(self):
        jobId = self.job()
        with patch.object(inventory, "record_audit_event", side_effect=sqlite3.OperationalError("synthetic-private")):
            with self.assertRaises(inventory.CloudInventoryError):
                self.job()
            with self.assertRaises(inventory.CloudInventoryError):
                inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.context)
        self.assertEqual("queued", self.summary(jobId)["status"])
        self.assertEqual(1, len(self.sql("SELECT id FROM cloud_drive_inventory_jobs")))

    def test_recovery_refuses_uncertain_or_live_process_and_audits_reviewed_release(self):
        jobId = self.job()
        token = inventory.claimCloudInventoryJob(self.settings, jobId)
        inventory.persistCloudInventoryPid(self.settings, jobId, token, os.getpid())
        with patch.object(workers, "isProcessAlive", side_effect=OSError("synthetic-private")):
            with self.assertRaises(inventory.CloudInventoryError) as caught:
                workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertEqual("worker_exit_unconfirmed", caught.exception.code)
        with patch.object(workers, "isProcessAlive", return_value=False):
            workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertEqual("interrupted", self.summary(jobId)["status"])
        self.assertFalse(self.summary(jobId)["workerSlotHeld"])
        self.assertEqual(1, len(self.sql("SELECT id FROM audit_events WHERE action='inventory_worker_release_reviewed'")))

    def test_review_can_release_unbound_claim_only_after_local_owner_exit(self):
        jobId = self.job()
        inventory.claimCloudInventoryJob(self.settings, jobId)
        with patch.object(workers, "isProcessAlive", return_value=True):
            with self.assertRaisesRegex(inventory.CloudInventoryError, "worker_exit_unconfirmed"):
                workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertTrue(self.summary(jobId)["workerSlotHeld"])
        with patch.object(workers, "isProcessAlive", return_value=False):
            workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertEqual("interrupted", self.summary(jobId)["status"])
        self.assertFalse(self.summary(jobId)["workerSlotHeld"])
        self.assertEqual(1, len(self.sql("SELECT id FROM audit_events WHERE action='inventory_worker_release_reviewed'")))

    def test_unbound_release_preserves_and_refuses_unresolved_rpc(self):
        jobId = self.job()
        inventory.claimCloudInventoryJob(self.settings, jobId)
        sessionId = self.sql("SELECT session_id FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,))[0][0]
        self.sql("INSERT INTO cloud_drive_rpc_lease(pool_id,id,session_id,owner_pid,owner_instance,owner_host,phase,created_at) "
                 "VALUES(1,?,?,?,?,?,'unknown',?)", (str(uuid4()), sessionId, os.getpid(), str(uuid4()), workers.cloudDriveHostKey(), time.time()))
        before = self.sql("SELECT * FROM cloud_drive_rpc_lease")
        with patch.object(workers, "isProcessAlive", return_value=False):
            with self.assertRaisesRegex(inventory.CloudInventoryError, "worker_exit_unconfirmed"):
                workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertTrue(self.summary(jobId)["workerSlotHeld"])
        self.assertEqual(before, self.sql("SELECT * FROM cloud_drive_rpc_lease"))

    def test_unbound_review_rechecks_owner_binding_in_write_transaction(self):
        jobId = self.job()
        inventory.claimCloudInventoryJob(self.settings, jobId)
        def ownerExited(pid):
            self.sql("UPDATE cloud_drive_inventory_jobs SET owner_instance=? WHERE id=?", (str(uuid4()), jobId))
            return False
        with patch.object(workers, "isProcessAlive", side_effect=ownerExited):
            with self.assertRaisesRegex(inventory.CloudInventoryError, "worker_ownership_lost"):
                workers.releaseCloudInventoryWorkerAfterReview(self.settings, jobId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertTrue(self.summary(jobId)["workerSlotHeld"])
