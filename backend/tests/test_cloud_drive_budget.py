"""CD2准入只用临时数据库、合成用户和独立本机进程，不连接云盘。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import multiprocessing
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
from app.service.cloud_drive_budget_service import (
    CloudBudgetError, CloudBudgetUnavailable, CloudDriveAdmission, cloudBudgetStatus,
    createCloudReadSession, pauseCloudBudget, releaseCloudLeaseAfterReview, resumeCloudBudget, resumeCloudReadSession,
)

_ROOT = "/test-cloud/movies"
_ENDPOINT = "https://cd2.example"


def _holdInChild(databasePath, sessionId, output, finish, abandon):
    path = Path(databasePath)
    settings = AppSettings(data_dir=path.parent, database_path=path, cloudDrive=CloudDriveSettings(_ROOT))
    admission = CloudDriveAdmission(settings, sessionId, endpoint=_ENDPOINT, rootPath=_ROOT)
    try:
        with admission(time.monotonic() + 5) as permit:
            permit.markSending("GetSubFiles")
            output.put({"leaseId": permit.leaseId, "pid": os.getpid()})
            finish.wait(10)
            if abandon:
                os._exit(0)
            permit.markSucceeded()
    except CloudBudgetError as exc:
        output.put({"error": exc.code})


class CloudDriveBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False), cloudDrive=CloudDriveSettings(_ROOT))
        ensure_database(self.settings)
        self.actor = create_user(self.settings, "operator", "test", "TestOnly123!", ["scan:run", "settings:write"], True)
        self.context = AuditWriteContext(self.actor)
        self.wall = time.time()
        self.clockPatch = patch("app.service.cloud_drive_budget_service._wallNow", side_effect=lambda: self.wall)
        self.clockPatch.start()
        self.addCleanup(self.clockPatch.stop)

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            result = connection.execute(query, args).fetchall()
            connection.commit()
            return result

    def session(self, **kwargs):
        options = {"requestId": str(uuid4()), "endpoint": _ENDPOINT, "rootPath": _ROOT,
                   "maxRpc": 10, "durationSeconds": 900, "auditContext": self.context}
        options.update(kwargs)
        return createCloudReadSession(self.settings, **options)

    def admission(self, session, **kwargs):
        return CloudDriveAdmission(self.settings, session["id"], endpoint=kwargs.get("endpoint", _ENDPOINT),
                                   rootPath=kwargs.get("rootPath", _ROOT))

    def success(self, admission):
        with admission(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
            permit.markSucceeded()

    def test_v39_migration_is_idempotent_and_preserves_business_data(self):
        session = self.session()
        self.sql("DROP TABLE cloud_drive_rpc_lease")
        self.sql("DROP TABLE cloud_drive_read_sessions")
        self.sql("DROP TABLE cloud_drive_budget_pool")
        self.sql("UPDATE app_meta SET value='39' WHERE key='schema_version'")
        self.sql("INSERT INTO scan_jobs(media_source_id,status,batch_size,batch_interval_seconds,created_at) "
                 "VALUES(1,'completed',100,1,'old')")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])
        self.assertGreaterEqual(CURRENT_SCHEMA_VERSION, 40)
        self.assertEqual([("completed",)], self.sql("SELECT status FROM scan_jobs"))
        self.assertEqual([], self.sql("SELECT * FROM cloud_drive_read_sessions"))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_approval_is_idempotent_bound_to_payload_and_never_resets_budget(self):
        requestId = str(uuid4())
        session = self.session(requestId=requestId)
        self.success(self.admission(session))
        repeated = self.session(requestId=requestId, endpoint="https://CD2.example:443/")
        self.assertEqual(session["id"], repeated["id"])
        self.assertEqual(1, repeated["used_rpc"])
        self.assertEqual(session["expires_at"], repeated["expires_at"])
        with self.assertRaises(CloudBudgetError) as caught:
            self.session(requestId=requestId, maxRpc=11)
        self.assertEqual("request_conflict", caught.exception.code)

    def test_reserved_does_not_count_and_success_consumes_total(self):
        session = self.session(maxRpc=1)
        admission = self.admission(session)
        with admission(time.monotonic() + 1):
            self.assertEqual([("reserved",)], self.sql("SELECT phase FROM cloud_drive_rpc_lease"))
            self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertEqual([], self.sql("SELECT * FROM cloud_drive_rpc_lease"))
        self.success(admission)
        self.assertEqual([(1, "exhausted")], self.sql("SELECT used_rpc,status FROM cloud_drive_read_sessions"))
        with self.assertRaises(CloudBudgetError) as caught:
            self.success(admission)
        self.assertEqual("session_exhausted", caught.exception.code)

    def test_aliases_and_other_endpoint_sessions_share_interval(self):
        first = self.session()
        second = self.session(endpoint="https://other.example", rootPath=_ROOT + "/category")
        self.success(self.admission(first))
        admission = self.admission(second, endpoint="https://other.example", rootPath=_ROOT + "/category")
        with self.assertRaises(CloudBudgetError) as caught:
            with admission(time.monotonic() + 0.08):
                self.fail("间隔内不能放行另一个端点")
        self.assertEqual("wait_timeout", caught.exception.code)
        self.wall += 2
        self.success(admission)

    def test_failure_and_partial_observation_pause_and_retain_unknown(self):
        session = self.session()
        admission = self.admission(session)
        with admission(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
            permit.markIncomplete("entry_limit")
        status = cloudBudgetStatus(self.settings)
        self.assertEqual("paused", status["state"])
        self.assertEqual("unknown", status["leasePhase"])
        self.assertEqual("entry_limit", status["pauseReason"])
        ensure_database(self.settings)
        self.assertEqual(status, cloudBudgetStatus(self.settings))
        with self.assertRaises(CloudBudgetError):
            self.session(endpoint="https://alias.example")
        with self.assertRaises(CloudBudgetError) as caught:
            with self.admission(session)(time.monotonic() + 0.1):
                self.fail("暂停后不能继续")
        self.assertIn(caught.exception.code, {"session_not_active", "pool_paused"})

    def test_exception_before_send_releases_without_consuming_or_pausing(self):
        session = self.session()
        with self.assertRaises(ValueError):
            with self.admission(session)(time.monotonic() + 1):
                raise ValueError("synthetic-pre-send-error")
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])
        self.assertEqual("ready", cloudBudgetStatus(self.settings)["state"])
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_failure_after_sending_never_logs_raw_error_or_replays(self):
        session = self.session()
        with self.assertRaisesRegex(RuntimeError, "synthetic-sensitive"):
            with self.admission(session)(time.monotonic() + 1) as permit:
                permit.markSending("GetSubFiles")
                raise RuntimeError("synthetic-sensitive-server-detail")
        self.assertEqual([(1, "paused")], self.sql("SELECT used_rpc,status FROM cloud_drive_read_sessions"))
        self.assertEqual("request_incomplete", cloudBudgetStatus(self.settings)["pauseReason"])
        audit = str(self.sql("SELECT detail_json FROM audit_events WHERE event_type='cloud_drive.budget'"))
        self.assertNotIn("synthetic-sensitive", audit)
        self.assertNotIn(_ENDPOINT, audit)
        self.assertNotIn(_ROOT, audit)

    def test_approval_expiry_and_queue_wait_never_extend_total(self):
        session = self.session(durationSeconds=30)
        self.wall += 30
        with self.assertRaises(CloudBudgetError) as caught:
            self.success(self.admission(session))
        self.assertEqual("session_expired", caught.exception.code)
        self.assertEqual([(0, "expired")], self.sql("SELECT used_rpc,status FROM cloud_drive_read_sessions"))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_clock_rollback_is_persistently_paused(self):
        session = self.session()
        self.wall -= 10
        with self.assertRaises(CloudBudgetError) as caught:
            self.success(self.admission(session))
        self.assertEqual("clock_rollback", caught.exception.code)
        self.assertEqual("clock_rollback", cloudBudgetStatus(self.settings)["pauseReason"])
        self.wall += 1000
        self.assertEqual("paused", cloudBudgetStatus(self.settings)["state"])

    def test_manual_pause_survives_late_unknown_response_and_clock_rollback(self):
        session = self.session()
        with self.admission(session)(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
            pauseCloudBudget(self.settings, auditContext=self.context)
        status = cloudBudgetStatus(self.settings)
        self.assertEqual(("paused", "manual_pause", "unknown"),
            (status["state"], status["pauseReason"], status["leasePhase"]))
        self.assertGreater(status["cooldownUntil"], self.wall)
        self.wall -= 10
        with self.assertRaisesRegex(CloudBudgetError, "clock_rollback"):
            self.session()
        status = cloudBudgetStatus(self.settings)
        self.assertEqual(("paused", "manual_pause"), (status["state"], status["pauseReason"]))

    def test_pause_fences_reserved_send_and_keeps_known_success_paused(self):
        session = self.session()
        with self.assertRaises(CloudBudgetError):
            with self.admission(session)(time.monotonic() + 1) as permit:
                pauseCloudBudget(self.settings, auditContext=self.context)
                permit.markSending("GetSubFiles")
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        status = cloudBudgetStatus(self.settings)
        resumeCloudBudget(self.settings, expectedRevision=status["revision"], confirmRemoteIdle=True, auditContext=self.context)
        with self.admission(session)(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
            pauseCloudBudget(self.settings, auditContext=self.context)
            permit.markSucceeded()
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])
        self.assertEqual("paused", cloudBudgetStatus(self.settings)["state"])

    def test_resume_requires_revision_and_explicit_remote_confirmation(self):
        first = pauseCloudBudget(self.settings, auditContext=self.context)
        second = pauseCloudBudget(self.settings, auditContext=self.context)
        self.assertGreater(second["revision"], first["revision"])
        with self.assertRaises(CloudBudgetError) as caught:
            resumeCloudBudget(self.settings, expectedRevision=first["revision"], confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual("revision_conflict", caught.exception.code)
        with self.assertRaises(ValueError):
            resumeCloudBudget(self.settings, expectedRevision=second["revision"], confirmRemoteIdle=False, auditContext=self.context)
        result = resumeCloudBudget(self.settings, expectedRevision=second["revision"], confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual("ready", result["state"])
        self.assertFalse(result["executionAvailable"])

    def test_live_or_foreign_host_lease_cannot_be_released(self):
        session = self.session()
        with self.admission(session)(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
        leaseId = cloudBudgetStatus(self.settings, includeLeaseIdentity=True)["lease"]["id"]
        with self.assertRaises(CloudBudgetError):
            releaseCloudLeaseAfterReview(self.settings, leaseId, confirmClientsStopped=True, confirmRemoteIdle=True)
        with patch("app.service.cloud_drive_budget_service._hostKey", return_value="different-host"):
            with self.assertRaises(CloudBudgetError):
                releaseCloudLeaseAfterReview(self.settings, leaseId, confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertTrue(cloudBudgetStatus(self.settings)["inFlight"])

    def test_local_review_cooldown_then_explicit_resume_preserves_total(self):
        session = self.session()
        with self.admission(session)(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
        status = cloudBudgetStatus(self.settings, includeLeaseIdentity=True)
        with self.assertRaises(CloudBudgetError) as caught:
            resumeCloudBudget(self.settings, expectedRevision=status["revision"], confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual("lease_requires_local_review", caught.exception.code)
        with patch("app.service.cloud_drive_budget_service.isProcessAlive", return_value=False):
            releaseCloudLeaseAfterReview(self.settings, status["lease"]["id"], confirmClientsStopped=True, confirmRemoteIdle=True)
        status = cloudBudgetStatus(self.settings)
        with self.assertRaises(CloudBudgetError) as caught:
            resumeCloudBudget(self.settings, expectedRevision=status["revision"], confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual("cooldown_not_finished", caught.exception.code)
        self.wall += 301
        resumeCloudBudget(self.settings, expectedRevision=status["revision"], confirmRemoteIdle=True, auditContext=self.context)
        self.assertEqual([("paused", 1)], self.sql("SELECT status,used_rpc FROM cloud_drive_read_sessions"))
        resumeCloudReadSession(self.settings, session["id"], auditContext=self.context, confirmContinue=True)
        self.success(self.admission(session))
        self.assertEqual([(2,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_actor_revocation_binding_and_rpc_allowlist_fail_before_send(self):
        session = self.session()
        for kwargs in ({"endpoint": "https://other.example"}, {"rootPath": _ROOT + "/nested"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CloudBudgetError):
                with self.admission(session, **kwargs)(time.monotonic() + 1):
                    self.fail("不允许改绑定")
        with self.assertRaises(CloudBudgetError):
            with self.admission(session)(time.monotonic() + 1) as permit:
                permit.markSending("DeleteFile")
        self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (self.actor.id,))
        with self.assertRaises(CloudBudgetError):
            self.success(self.admission(session))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_stricter_policy_survives_other_process_settings(self):
        self.settings = replace(self.settings, cloudDrive=CloudDriveSettings(_ROOT, 5, 600))
        session = self.session()
        weaker = replace(self.settings, cloudDrive=CloudDriveSettings(_ROOT))
        self.success(CloudDriveAdmission(weaker, session["id"], endpoint=_ENDPOINT, rootPath=_ROOT))
        status = cloudBudgetStatus(weaker)
        self.assertEqual(5, status["minIntervalSeconds"])
        self.assertEqual(600, status["failureCooldownSeconds"])

    def test_policy_strengthening_after_reservation_is_checked_before_send(self):
        session = self.session()
        self.success(self.admission(session))
        self.wall += 2
        with self.assertRaises(CloudBudgetError) as caught:
            with self.admission(session)(time.monotonic() + 1) as permit:
                stronger = replace(self.settings, cloudDrive=CloudDriveSettings(_ROOT, 5, 600))
                createCloudReadSession(stronger, requestId=str(uuid4()), endpoint=_ENDPOINT, rootPath=_ROOT,
                    maxRpc=10, durationSeconds=900, auditContext=self.context)
                permit.markSending("GetSubFiles")
        self.assertEqual("spacing_changed_before_send", caught.exception.code)
        self.assertEqual([(1,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions WHERE id=?", (session["id"],)))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_audit_failure_rolls_back_admin_change_and_approval(self):
        self.session()
        with patch("app.service.cloud_drive_budget_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(CloudBudgetUnavailable):
                pauseCloudBudget(self.settings, auditContext=self.context)
            with self.assertRaises(CloudBudgetUnavailable):
                self.session()
        self.assertEqual("ready", cloudBudgetStatus(self.settings)["state"])
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_drive_read_sessions"))

    def test_failure_audit_storage_error_retains_sending_slot(self):
        session = self.session()
        with self.assertRaises(CloudBudgetUnavailable):
            with patch("app.service.cloud_drive_budget_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
                with self.admission(session)(time.monotonic() + 1) as permit:
                    permit.markSending("GetSubFiles")
        self.assertEqual([("sending",)], self.sql("SELECT phase FROM cloud_drive_rpc_lease"))
        self.assertEqual([(1,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_deadline_invalid_state_and_missing_database_fail_closed(self):
        session = self.session()
        for deadline in (float("nan"), float("inf"), True):
            with self.subTest(deadline=deadline), self.assertRaises(CloudBudgetError):
                with self.admission(session)(deadline):
                    self.fail("非法预算不准入")
        self.sql("UPDATE cloud_drive_budget_pool SET observed_at=?", (float("inf"),))
        with self.assertRaises(CloudBudgetUnavailable):
            self.success(self.admission(session))
        settings = replace(self.settings, database_path=self.settings.data_dir / "absent.sqlite3")
        with self.assertRaises(CloudBudgetUnavailable):
            cloudBudgetStatus(settings)
        self.assertFalse(settings.database_path.exists())

    def test_threads_share_single_reserved_slot_without_holding_transaction(self):
        session = self.session()
        with self.admission(session)(time.monotonic() + 1):
            def blocked():
                with self.assertRaises(CloudBudgetError):
                    with self.admission(session)(time.monotonic() + 0.1):
                        self.fail("不能双重准入")
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(blocked).result(timeout=2)
            self.sql("UPDATE users SET display_name='not-blocked' WHERE id=?", (self.actor.id,))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_session_capacity_rejects_without_deleting_approval_history(self):
        session = self.session()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO cloud_drive_read_sessions "
                "SELECT ?,?,request_hash,endpoint_hash,root_path,actor_id,max_rpc,used_rpc,status,expires_at,created_at,updated_at "
                "FROM cloud_drive_read_sessions WHERE id=?",
                [(str(uuid4()), str(uuid4()), session["id"]) for _ in range(999)])
            connection.commit()
        with self.assertRaises(CloudBudgetError) as caught:
            self.session()
        self.assertEqual("session_capacity_reached", caught.exception.code)
        self.assertEqual([(1000,)], self.sql("SELECT COUNT(*) FROM cloud_drive_read_sessions"))

    def test_changed_lease_cannot_be_deleted_by_stale_owner(self):
        session = self.session()
        replacement = str(uuid4())
        with self.assertRaises(CloudBudgetError) as caught:
            with self.admission(session)(time.monotonic() + 1):
                self.sql("UPDATE cloud_drive_rpc_lease SET id=?", (replacement,))
        self.assertEqual("lease_ownership_lost", caught.exception.code)
        self.assertEqual([(replacement,)], self.sql("SELECT id FROM cloud_drive_rpc_lease"))

    def test_bad_authority_or_persisted_reason_is_not_echoed(self):
        session = self.session()
        self.sql("UPDATE users SET permissions_json=?", (json.dumps({"scan:run": True}),))
        with self.assertRaises(CloudBudgetUnavailable):
            self.success(self.admission(session))
        self.sql("UPDATE cloud_drive_budget_pool SET state='paused',pause_reason='synthetic-untrusted-detail'")
        with self.assertRaises(CloudBudgetUnavailable) as caught:
            cloudBudgetStatus(self.settings)
        self.assertNotIn("synthetic-untrusted-detail", str(caught.exception))

    def test_review_refuses_unknown_process_probe_and_wrong_confirmation(self):
        session = self.session()
        with self.admission(session)(time.monotonic() + 1) as permit:
            permit.markSending("GetSubFiles")
        lease = cloudBudgetStatus(self.settings, includeLeaseIdentity=True)["lease"]
        with self.assertRaises(ValueError):
            releaseCloudLeaseAfterReview(self.settings, lease["id"], confirmClientsStopped=False, confirmRemoteIdle=True)
        with patch("app.service.cloud_drive_budget_service.isProcessAlive", side_effect=OSError("synthetic")):
            with self.assertRaises(CloudBudgetError) as caught:
                releaseCloudLeaseAfterReview(self.settings, lease["id"], confirmClientsStopped=True, confirmRemoteIdle=True)
            self.assertEqual("owner_process_state_unknown", caught.exception.code)
        self.assertTrue(cloudBudgetStatus(self.settings)["inFlight"])

    def test_process_exit_never_auto_reclaims_or_replays_unknown_rpc(self):
        self.clockPatch.stop()
        session = self.session()
        context = multiprocessing.get_context("spawn")
        output, finish = context.Queue(), context.Event()
        process = context.Process(target=_holdInChild, args=(str(self.settings.database_path), session["id"], output, finish, True))
        process.start()
        def cleanup():
            finish.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            output.close()
            output.join_thread()
        self.addCleanup(cleanup)
        result = output.get(timeout=15)
        self.assertNotIn("error", result)
        with self.assertRaises(CloudBudgetError):
            with self.admission(session)(time.monotonic() + 0.1):
                self.fail("子进程请求在途不能放行")
        finish.set()
        process.join(8)
        self.assertFalse(process.is_alive())
        with self.assertRaises(CloudBudgetError):
            with self.admission(session)(time.monotonic() + 0.1):
                self.fail("进程退出也不能自动恢复")
        self.assertEqual([("sending",)], self.sql("SELECT phase FROM cloud_drive_rpc_lease"))
        releaseCloudLeaseAfterReview(self.settings, result["leaseId"], confirmClientsStopped=True, confirmRemoteIdle=True)
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])
        self.assertEqual("paused", cloudBudgetStatus(self.settings)["state"])
        self.assertEqual([(1, "paused")], self.sql("SELECT used_rpc,status FROM cloud_drive_read_sessions"))
