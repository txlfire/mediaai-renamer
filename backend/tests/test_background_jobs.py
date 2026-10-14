"""后台受理、真实隔离干跑及外发边界；仅使用临时库、媒体和合成结果。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.core.config import AppSettings, LoggingSettings, ScanSettings, BackgroundJobSettings, backgroundJobSettingsFromDict
from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
from app.core.logger import shutdown_logging
from app.schema.ai_parse import AiParseResult, AiParseCandidate
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.background_job_service import (
    acceptBackgroundJob, getBackgroundJob, claimBackgroundJob, executeBackgroundJob, heartbeatBackgroundJob,
    finishInterruptedBackgroundJob, recoverExpiredBackgroundJobs, cleanupBackgroundResults, _executionGuard,
    BackgroundJobConflict, BackgroundJobInterrupted,
)
from app.service.background_job_worker_service import startBackgroundJobProcess
from app.service.execution_guard_service import executionGuard
from app.service.media_source_service import create_media_source
from app.service.preview_service import generate_rename_previews, list_rename_previews
from app.service.scan_service import run_full_scan
from app.service.settings_service import update_setting_values
from app.service.external_request_budget_service import ExternalRequestBudget


class BackgroundJobTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(shutdown_logging)
        root = Path(self.temp.name)
        media = root / "media"
        media.mkdir()
        for name in ("Movie.2024.1080p.mkv", "Other.2024.1080p.mkv"):
            (media / name).write_bytes(b"temporary-media")
        self.settings = AppSettings(data_dir=root / "data", database_path=root / "data/test.db",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=100, batch_interval_seconds=0))
        ensure_database(self.settings)
        self.user = create_user(self.settings, "tester", "tester", "temporary-test-password",
            ["settings:write", "metadata:submit", "rename:execute", "rollback:execute", "task:manage"], True)
        self.context = AuditWriteContext(self.user)
        self.source = create_media_source(self.settings, "temporary", media)
        update_setting_values(self.settings, {"scan.minimum_file_size": 0, "scan.batch_interval_seconds": 0})
        scan = run_full_scan(self.settings, self.source.id)
        generate_rename_previews(self.settings, scan_job_id=scan.id)
        self.ids = [item.id for item in list_rename_previews(self.settings, scan_job_id=scan.id)]

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(statement, values).fetchall()
            connection.commit()
            return rows

    def accept(self, kind="rename_dry_run", ids=None, requestId=None):
        return acceptBackgroundJob(self.settings, kind, requestId or str(uuid4()),
            {"rename_preview_ids": self.ids if ids is None else ids}, self.context)

    def execute(self, job):
        claimed = claimBackgroundJob(self.settings)
        self.assertEqual(job["id"], claimed[0])
        executeBackgroundJob(self.settings, *claimed)
        finishInterruptedBackgroundJob(self.settings, *claimed, "normal", processExited=True)
        return getBackgroundJob(self.settings, job["request_id"], self.user, includeResult=True)

    def test_admission_is_database_only_and_idempotent(self):
        requestId = str(uuid4())
        with patch("app.service.rename_operation_service._build_item_plan", side_effect=AssertionError("no probe")), \
             patch("app.service.external_request_budget_service.urlopen", side_effect=AssertionError("no HTTP")):
            first = self.accept(requestId=requestId)
            second = self.accept(ids=list(reversed(self.ids)), requestId=requestId)
        self.assertEqual(first, second)
        self.assertEqual("queued", first["status"])
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM background_jobs")[0][0])
        self.assertEqual([], self.sql("SELECT id FROM rename_operations"))

    def test_request_conflict_does_not_replace_job(self):
        requestId = str(uuid4())
        self.accept(requestId=requestId)
        with self.assertRaises(BackgroundJobConflict):
            self.accept(ids=self.ids[:1], requestId=requestId)

    def test_overlapping_new_job_rejected(self):
        self.accept()
        with self.assertRaises(BackgroundJobConflict):
            self.accept("ai_batch")

    def test_limit_rejects_before_probe(self):
        update_setting_values(self.settings, {"operations.batch_limit": 1})
        with self.assertRaises(BackgroundJobConflict):
            self.accept()

    def test_parallel_duplicate_accept_and_claim_only_once(self):
        requestId = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows = list(executor.map(lambda _: self.accept(requestId=requestId), range(2)))
            claims = list(executor.map(lambda _: claimBackgroundJob(self.settings), range(2)))
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual(1, sum(row is not None for row in claims))

    def test_dry_run_results_are_linked_and_no_file_is_renamed(self):
        job = self.accept()
        result = self.execute(job)
        self.assertEqual("completed", result["status"])
        operationId = result["result"]["operation_id"]
        self.assertEqual("dry_run", self.sql("SELECT status FROM rename_operations WHERE id=?", (operationId,))[0][0])
        self.assertEqual(2, result["completed_count"])
        self.assertEqual(2, len(list((Path(self.temp.name) / "media").glob("*.mkv"))))
        self.assertEqual("Movie.2024.1080p.mkv", sorted((Path(self.temp.name) / "media").glob("*.mkv"))[0].name)

    def test_status_does_not_load_or_expose_payload_or_secret(self):
        result = self.execute(self.accept())
        public = getBackgroundJob(self.settings, result["request_id"], self.user)
        self.assertTrue(public["result_available"])
        for key in ("result", "items", "input_json", "snapshot_json", "config_hash", "execution_token"):
            self.assertNotIn(key, public)

    def test_other_user_cannot_read_owner_result(self):
        job = self.accept()
        other = create_user(self.settings, "other", "other", "temporary-test-password", ["metadata:submit"], True)
        with self.assertRaises(LookupError):
            getBackgroundJob(self.settings, job["request_id"], other)

    def test_revoke_before_execute_makes_zero_probe(self):
        job = self.accept()
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.user.id,))
        with patch("app.service.rename_operation_service._build_item_plan", side_effect=AssertionError("no probe")):
            with self.assertRaises(BackgroundJobInterrupted):
                executeBackgroundJob(self.settings, *claim)
        self.assertFalse(heartbeatBackgroundJob(self.settings, *claim))

    def test_configuration_changed_while_queued_is_rejected(self):
        job = self.accept()
        claim = claimBackgroundJob(self.settings)
        update_setting_values(self.settings, {"privacy.custom_sensitive_words": ["temporary-block"]})
        with self.assertRaises(BackgroundJobInterrupted):
            executeBackgroundJob(self.settings, *claim)

    def test_preview_changed_while_queued_is_rejected(self):
        self.accept()
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE rename_previews SET parsed_title='changed' WHERE id=?", (self.ids[0],))
        with self.assertRaises(BackgroundJobInterrupted):
            executeBackgroundJob(self.settings, *claim)

    def test_interrupted_sent_and_not_sent_are_distinct_and_not_replayed(self):
        job = self.accept("ai_batch")
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE background_job_items SET status='dispatched' WHERE job_id=? AND item_id=?", (job["id"], self.ids[0]))
        finishInterruptedBackgroundJob(self.settings, *claim, "synthetic", processExited=False)
        states = self.sql("SELECT status FROM background_job_items WHERE job_id=? ORDER BY item_id", (job["id"],))
        self.assertEqual([("unknown",), ("not_sent",)], states)
        self.assertIsNone(claimBackgroundJob(self.settings))
        same = self.accept("ai_batch", requestId=job["request_id"])
        self.assertEqual("interrupted", same["status"])
        finishInterruptedBackgroundJob(self.settings, *claim, "synthetic", processExited=True)
        self.assertIsNone(claimBackgroundJob(self.settings))

    def test_actual_process_exit_is_required_for_recovery(self):
        self.accept()
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE background_jobs SET execution_pid=123,execution_expires_at='2000' WHERE id=?", (claim[0],))
        with patch("app.service.background_job_service.isProcessAlive", return_value=True):
            recoverExpiredBackgroundJobs(self.settings)
        self.assertIsNone(claimBackgroundJob(self.settings))
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM background_jobs WHERE execution_token IS NOT NULL")[0][0])
        with patch("app.service.background_job_service.isProcessAlive", return_value=False):
            recoverExpiredBackgroundJobs(self.settings)
        self.assertEqual(0, self.sql("SELECT COUNT(*) FROM background_jobs WHERE execution_token IS NOT NULL")[0][0])

    def test_cleanup_clears_results_not_idempotence_or_audit(self):
        job = self.execute(self.accept())
        audits = self.sql("SELECT COUNT(*) FROM audit_events")[0][0]
        self.sql("UPDATE background_jobs SET updated_at='2000' WHERE id=?", (job["id"],))
        self.assertEqual(1, cleanupBackgroundResults(self.settings))
        self.assertFalse(getBackgroundJob(self.settings, job["request_id"], self.user)["result_available"])
        with self.assertRaises(LookupError):
            getBackgroundJob(self.settings, job["request_id"], self.user, includeResult=True)
        self.assertEqual("completed", self.accept(requestId=job["request_id"])["status"])
        self.assertEqual(audits, self.sql("SELECT COUNT(*) FROM audit_events")[0][0])

    def test_cleanup_does_not_release_active_slots(self):
        job = self.accept()
        claimBackgroundJob(self.settings)
        self.sql("UPDATE background_jobs SET updated_at='2000' WHERE id=?", (job["id"],))
        self.assertEqual(0, cleanupBackgroundResults(self.settings))

    def test_real_hidden_process_dry_run_uses_only_temporary_media(self):
        job = self.accept()
        claim = claimBackgroundJob(self.settings)
        process = startBackgroundJobProcess(self.settings, *claim)
        try:
            self.assertEqual(0, process.wait(timeout=30))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            finishInterruptedBackgroundJob(self.settings, *claim, "test cleanup", processExited=True)
        self.assertEqual("completed", getBackgroundJob(self.settings, job["request_id"], self.user)["status"])

    def test_ai_batch_drops_raw_data_and_merges_known_usage(self):
        job = self.accept("ai_batch")
        candidate = AiParseCandidate("Synthetic", "movie", 2024, None, None, 90, "synthetic", {"prompt": "never-persist"})
        outcome = AiParseResult("success", "synthetic", [candidate], usage={"total_tokens": 2})
        with patch("app.service.preview_service.parse_rename_preview_with_ai", return_value=outcome):
            result = self.execute(job)
        self.assertEqual(2, result["result"]["success_count"])
        self.assertEqual(4, result["result"]["usage"]["total_tokens"])
        self.assertNotIn("never-persist", json.dumps(result))

    def test_send_guard_rechecks_after_budget_wait(self):
        from urllib.request import Request
        job = self.accept("ai_batch")
        claim = claimBackgroundJob(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            expected = json.loads(connection.execute("SELECT snapshot_json FROM background_jobs WHERE id=?", (job["id"],)).fetchone()[0])
        def acquire(*_args):
            self.sql("UPDATE users SET enabled=0 WHERE id=?", (self.user.id,))
            return "synthetic-token"
        guard = _executionGuard(self.settings, *claim, expected, {"rename_preview_ids": self.ids}, "ai_batch", self.ids[0])
        with executionGuard(guard), patch.object(ExternalRequestBudget, "acquire", side_effect=acquire), \
             patch("app.service.external_request_budget_service.urlopen") as transport:
            with self.assertRaises(BackgroundJobInterrupted):
                with ExternalRequestBudget(self.settings, "ai").open(Request("https://example.invalid"), 1):
                    pass
        transport.assert_not_called()

    def test_migration_38_preserves_user_and_adds_job_tables(self):
        self.sql("DROP TABLE background_job_items")
        self.sql("DROP TABLE background_jobs")
        self.sql("UPDATE app_meta SET value='38' WHERE key='schema_version'")
        ensure_database(self.settings)
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])
        self.assertEqual(self.user.username, self.sql("SELECT username FROM users WHERE id=?", (self.user.id,))[0][0])
        self.assertEqual([], self.sql("SELECT id FROM background_jobs"))

    def test_real_api_loop_accepts_queries_and_preserves_idempotence(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.service.auth_service import login
        client = TestClient(create_app(self.settings))
        body = {"request_id": str(uuid4()), "kind": "rename_dry_run", "rename_preview_ids": self.ids}
        self.assertEqual(401, client.post("/api/background-jobs", json=body).status_code)
        client.headers["Authorization"] = f"Bearer {login(self.settings, self.user.username, 'temporary-test-password').access_token}"
        with client:
            response = client.post("/api/background-jobs", json=body)
            self.assertEqual(202, response.status_code, response.text)
            self.assertNotIn("execution_token", response.json())
            path = f"/api/background-jobs/{body['request_id']}"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = client.get(path).json()
                if state["status"] in {"completed", "interrupted"}:
                    break
                time.sleep(0.1)
            self.assertEqual("completed", state["status"], state)
            self.assertIn("operation_id", client.get(path + "/result").json()["result"])
            self.assertEqual(1, len(client.get("/api/background-jobs").json()["items"]))
            self.assertEqual(response.json()["id"], client.post("/api/background-jobs", json=body).json()["id"])
            invalid = dict(body, rename_preview_ids=[True])
            self.assertEqual(422, client.post("/api/background-jobs", json=invalid).status_code)
            page = client.get("/api/tasks/page", params={"task_type": "background_job"})
            self.assertEqual(200, page.status_code, page.text)
            self.assertEqual(1, page.json()["total"])
            self.assertEqual(body["request_id"], page.json()["items"][0]["target_query"]["background_request_id"])

    def test_rollback_background_dry_run_does_not_restore_files(self):
        from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation
        from app.service.rename_rollback_service import create_rename_rollback_plan
        operation = execute_rename_operation(self.settings, create_rename_dry_run(self.settings, self.ids).id)
        plan = create_rename_rollback_plan(self.settings, operation.id)
        names = sorted(item.name for item in (Path(self.temp.name) / "media").iterdir())
        job = acceptBackgroundJob(self.settings, "rollback_dry_run", str(uuid4()), {"plan_id": plan.id}, self.context)
        with patch("app.service.rename_rollback_service._rollbackDryRunDatabaseState", wraps=__import__("app.service.rename_rollback_service", fromlist=["_"])._rollbackDryRunDatabaseState) as snapshot:
            result = self.execute(job)
        self.assertEqual({"plan_id": plan.id, "operation_id": operation.id}, result["result"])
        self.assertEqual("checked", self.sql("SELECT status FROM rename_rollback_plans WHERE id=?", (plan.id,))[0][0])
        self.assertEqual(names, sorted(item.name for item in (Path(self.temp.name) / "media").iterdir()))
        self.assertLessEqual(snapshot.call_count, 5)

    def test_metadata_partial_results_and_provider_counts_are_retained(self):
        job = self.accept("metadata_batch")
        values = [{"preview": {"id": self.ids[0], "metadata_match_status": "matched"},
                   "provider_results": [{"status": "success", "raw_data": {"prompt": "never-persist"}}]},
                  {"preview": {"id": self.ids[1], "metadata_match_status": "failed"}, "provider_results": [{"status": "failed"}]}]
        with patch("app.service.preview_service.match_rename_preview_multi_source_metadata", side_effect=values):
            result = self.execute(job)
        self.assertEqual("partial_completed", result["status"])
        self.assertEqual(1, result["result"]["success_count"])
        self.assertEqual(1, result["result"]["failed_count"])
        self.assertEqual(1, result["result"]["provider_failed_count"])
        self.assertNotIn("never-persist", json.dumps(result))

    def test_ai_sent_timeout_is_unknown_and_retains_known_results(self):
        job = self.accept("ai_batch")
        calls = []
        def parse(_settings, itemId, **_kwargs):
            calls.append(itemId)
            self.sql("UPDATE background_job_items SET status='dispatched' WHERE job_id=? AND item_id=?", (job["id"], itemId))
            return AiParseResult("failed", "timeout", [], error_type="timeout") if len(calls) == 2 else AiParseResult("success", "known", [])
        with patch("app.service.preview_service.parse_rename_preview_with_ai", side_effect=parse):
            result = self.execute(job)
        self.assertEqual("partial_completed", result["status"])
        self.assertEqual(1, result["unknown_count"])
        self.assertEqual(["completed", "unknown"], [item["status"] for item in result["items"]])
        self.assertEqual(self.ids, calls)

    def test_result_size_cap_and_pressure_cleanup_keep_request_tombstone(self):
        self.settings = replace(self.settings, background_jobs=BackgroundJobSettings(result_max_size_mb=1, result_total_size_mb=2))
        job = self.accept("ai_batch")
        outcome = AiParseResult("success", "x" * 1024 * 1024, [])
        with patch("app.service.preview_service.parse_rename_preview_with_ai", return_value=outcome):
            result = self.execute(job)
        self.assertEqual("partial_completed", result["status"])
        self.assertLessEqual(self.sql("SELECT result_bytes FROM background_jobs WHERE id=?", (job["id"],))[0][0], 1024 * 1024)
        self.sql("UPDATE background_jobs SET result_bytes=? WHERE id=?", (2 * 1024 * 1024, job["id"]))
        self.assertEqual(1, cleanupBackgroundResults(self.settings, reserveBytes=1024 * 1024))
        self.assertFalse(self.accept("ai_batch", requestId=job["request_id"])["result_available"])

    def test_capacity_rejects_while_existing_reservation_is_active(self):
        self.settings = replace(self.settings, background_jobs=BackgroundJobSettings(result_max_size_mb=1, result_total_size_mb=1))
        self.accept(ids=self.ids[:1])
        with self.assertRaises(BackgroundJobConflict):
            self.accept(ids=self.ids[1:])

    def test_dead_owner_before_pid_handoff_can_release_but_unknown_owner_cannot(self):
        self.accept()
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE background_jobs SET execution_expires_at='2000' WHERE id=?", (claim[0],))
        with patch("app.service.background_job_service.isProcessAlive", return_value=True):
            recoverExpiredBackgroundJobs(self.settings)
        self.assertEqual(1, self.sql("SELECT COUNT(*) FROM background_jobs WHERE execution_token IS NOT NULL")[0][0])
        with patch("app.service.background_job_service.isProcessAlive", return_value=False):
            recoverExpiredBackgroundJobs(self.settings)
        self.assertEqual(0, self.sql("SELECT COUNT(*) FROM background_jobs WHERE execution_token IS NOT NULL")[0][0])
        self.assertIsNone(claimBackgroundJob(self.settings))

    def test_publication_guard_rejects_configuration_change_after_probe(self):
        job = self.accept()
        claim = claimBackgroundJob(self.settings)
        from app.service.rename_operation_service import _build_item_plan
        def probe(*args, **kwargs):
            result = _build_item_plan(*args, **kwargs)
            update_setting_values(self.settings, {"privacy.custom_sensitive_words": ["synthetic-change"]})
            return result
        with patch("app.service.rename_operation_service._build_item_plan", side_effect=probe):
            with self.assertRaises(BackgroundJobInterrupted):
                executeBackgroundJob(self.settings, *claim)
        self.assertEqual([], self.sql("SELECT id FROM rename_operations"))

    def test_multisource_provider_thread_inherits_execution_guard(self):
        from app.service.metadata_multi_source_service import _startProvider
        from app.service.execution_guard_service import checkExecutionGuard
        from app.service.metadata_provider_registry import ProviderRegistryItem
        from app.schema.media import ParsedMediaName
        calls = []
        class Searcher:
            def search(self, _parsed):
                checkExecutionGuard(phase="send")
                return []
        item = ProviderRegistryItem("tmdb", "synthetic", 1, True, True, Searcher())
        with executionGuard(lambda _connection, phase: calls.append(phase)):
            future = _startProvider(item, ParsedMediaName("movie", "Synthetic", 2024, None, None))
            future.result(timeout=5)
        self.assertEqual(["send"], calls)

    def test_ai_input_change_after_response_prevents_publication(self):
        self.accept("ai_batch")
        claim = claimBackgroundJob(self.settings)
        def parse(*_args, **_kwargs):
            self.sql("UPDATE rename_previews SET parsed_title='concurrent-edit' WHERE id=?", (self.ids[0],))
            return AiParseResult("success", "stale", [])
        with patch("app.service.preview_service.parse_rename_preview_with_ai", side_effect=parse):
            with self.assertRaises(BackgroundJobInterrupted):
                executeBackgroundJob(self.settings, *claim)
        self.assertEqual(0, self.sql("SELECT completed_count FROM background_jobs WHERE id=?", (claim[0],))[0][0])

    def test_real_metadata_publication_uses_short_transaction_and_known_results(self):
        from app.schema.metadata import MetadataMatchSummary
        from app.service.metadata_multi_source_service import MultiSourceMatchResult
        job = self.accept("metadata_batch")
        outcome = MultiSourceMatchResult(MetadataMatchSummary("failed", "synthetic empty", [], "synthetic"), [])
        with patch("app.service.preview_service.match_multi_source_metadata_candidates", return_value=outcome):
            result = self.execute(job)
        self.assertEqual(2, result["completed_count"])
        self.assertEqual("partial_completed", result["status"])
        self.assertEqual(2, result["result"]["failed_count"])

    def test_summary_query_at_maximum_batch_is_bounded(self):
        job = self.accept("ai_batch")
        self.sql("UPDATE background_jobs SET total_count=1000,result_json=? WHERE id=?", (json.dumps({"synthetic": "x" * 1024 * 1024}), job["id"]))
        times = []
        for _ in range(100):
            start = time.perf_counter()
            public = getBackgroundJob(self.settings, job["request_id"], self.user)
            times.append(time.perf_counter() - start)
            self.assertLess(len(json.dumps(public).encode()), 1024)
        print(f"background_summary queries=100 total_count=1000 response_bytes={len(json.dumps(public).encode())} p95_ms={sorted(times)[94] * 1000:.3f}")

    def test_aggregate_usage_overflow_retains_individual_known_results(self):
        self.settings = replace(self.settings, background_jobs=BackgroundJobSettings(result_max_size_mb=1, result_total_size_mb=2))
        job = self.accept("ai_batch")
        outcomes = [AiParseResult("success", "known", [], usage={str(index) + "x" * 185000: 1}) for index in range(2)]
        with patch("app.service.preview_service.parse_rename_preview_with_ai", side_effect=outcomes):
            result = self.execute(job)
        self.assertTrue(result["result"]["result_truncated"])
        self.assertEqual("partial_completed", result["status"])
        self.assertEqual(2, result["success_count"])
        self.assertTrue(all(item["outcome"]["status"] == "success" for item in result["items"]))
        self.assertLessEqual(self.sql("SELECT result_bytes FROM background_jobs WHERE id=?", (job["id"],))[0][0], 1024 * 1024)

    def test_known_ai_failure_marks_item_failed_not_successful_processing(self):
        job = self.accept("ai_batch")
        with patch("app.service.preview_service.parse_rename_preview_with_ai", return_value=AiParseResult("failed", "synthetic denied", [], error_type="authentication", http_status=401)):
            result = self.execute(job)
        self.assertEqual(2, result["failed_count"])
        self.assertEqual(0, result["unknown_count"])
        self.assertEqual(["failed", "failed"], [item["status"] for item in result["items"]])

    def test_interruption_audit_is_idempotent_and_survives_result_cleanup(self):
        job = self.accept()
        claim = claimBackgroundJob(self.settings)
        self.sql("UPDATE users SET enabled=0 WHERE id=?", (self.user.id,))
        finishInterruptedBackgroundJob(self.settings, *claim, "synthetic revoked", processExited=True)
        finishInterruptedBackgroundJob(self.settings, *claim, "synthetic repeated", processExited=True)
        self.sql("UPDATE background_jobs SET updated_at='2000' WHERE id=?", (job["id"],))
        cleanupBackgroundResults(self.settings)
        rows = self.sql("SELECT actor_name,summary FROM audit_events WHERE event_type='background.interrupted' AND target_id=?", (str(job["id"]),))
        self.assertEqual(1, len(rows))
        self.assertEqual(self.user.username, rows[0][0])
        self.assertIn("synthetic revoked", rows[0][1])


class BackgroundConfigurationTest(unittest.TestCase):
    def test_result_limits_and_timeout_are_validated(self):
        for values in ({"max_queued": 0}, {"retention_days": 0}, {"timeout_seconds": True},
                       {"result_max_size_mb": 8, "result_total_size_mb": 4}, {"typo": 1}, []):
            with self.assertRaises(ValueError):
                backgroundJobSettingsFromDict(values)
        self.assertEqual(BackgroundJobSettings(), backgroundJobSettingsFromDict({}))
