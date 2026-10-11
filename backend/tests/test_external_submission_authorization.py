"""一次性外部提交：快照、消费、故障和审计补写的隔离回归。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from app.main import create_app
from app.schema.ai_parse import AiParseCandidate, AiParseResult
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import bootstrap_admin, login
from app.service.external_submission_authorization_service import (
    prepareExternalSubmission, submitExternalSubmission, getExternalSubmissionOutcome, flushExternalSubmissionAudits,
)
from app.service.external_submission_guard import check_external_submission, list_external_submission_blocks
from app.service.settings_service import update_setting_values
import test_rename_preview_metadata as previewFixtures

MODULE = "app.service.external_submission_authorization_service"


class ExternalSubmissionAuthorizationTest(unittest.TestCase):
    _insert_media_file = previewFixtures.RenamePreviewMetadataTest._insert_media_file

    def setUp(self):
        previewFixtures.RenamePreviewMetadataTest.setUp(self)
        self.actor = bootstrap_admin(self.settings, "review-author", "测试用户", "SyntheticPassword123!")
        self.context = AuditWriteContext(self.actor, "127.0.0.1", "synthetic-test")
        update_setting_values(self.settings, {"privacy.custom_sensitive_words": '["Matrix"]', "ai.enabled": "true",
            "ai.api_key": "synthetic-key-not-a-real-secret"}, operator=self.actor.username)
        self.block = self._block("tmdb")

    def tearDown(self):
        from app.core.logger import shutdown_logging
        shutdown_logging()
        previewFixtures.RenamePreviewMetadataTest.tearDown(self)

    def _block(self, target):
        return check_external_submission(self.settings, "rename_preview", self.preview.id, self.media_file.name,
            str(self.media_file), self.preview.parsed_title, target).block_record

    def _sql(self, sql, params=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            result = connection.execute(sql, params).fetchall()
            connection.commit()
            return result

    def _prepare(self):
        return prepareExternalSubmission(self.settings, self.block.id, self.context)

    def _submit(self, grant):
        return submitExternalSubmission(self.settings, self.block.id, grant["id"], "隔离测试确认", self.context)

    def test_prepare_has_no_external_call_and_no_secrets_in_snapshot(self):
        with patch(f"{MODULE}.TmdbClient") as client, patch(f"{MODULE}.parse_media_with_ai") as ai:
            grant = self._prepare()
        client.assert_not_called()
        ai.assert_not_called()
        self.assertEqual("V3 API Key", grant["snapshot"]["service"]["auth_channel"])
        saved = self._sql("SELECT snapshot_json,context_json FROM external_submission_authorizations")[0]
        self.assertNotIn("abcdef123456", str(saved))
        self.assertNotIn("synthetic-key", str(saved))
        self.assertNotIn("fingerprint", json.dumps(grant))

    def test_prepare_recomputes_current_policy_matches_without_overwriting_original_block(self):
        originalMasked = self.block.matched_value_masked
        update_setting_values(
            self.settings,
            {"privacy.custom_sensitive_words": '["Matrix", "The"]'},
            operator=self.actor.username,
        )

        grant = self._prepare()

        evidence = grant["snapshot"]["policy_evidence"]
        self.assertEqual(2, evidence["matched_count"])
        self.assertCountEqual(["Ma***ix", "T***e"], evidence["matched_words_masked"])
        self.assertEqual(
            originalMasked,
            self._sql("SELECT matched_value_masked FROM external_submission_blocks WHERE id=?", (self.block.id,))[0][0],
        )
        with patch(f"{MODULE}.TmdbClient") as client:
            client.return_value.search.return_value = []
            self.assertEqual("completed", self._submit(grant)["status"])
            client.return_value.search.assert_called_once()

    def test_each_prepared_grant_keeps_immutable_policy_evidence(self):
        first = self._prepare()
        update_setting_values(
            self.settings,
            {"privacy.custom_sensitive_words": '["Matrix", "The"]'},
            operator=self.actor.username,
        )
        second = self._prepare()

        self.assertEqual(1, first["snapshot"]["policy_evidence"]["matched_count"])
        self.assertEqual(2, second["snapshot"]["policy_evidence"]["matched_count"])
        saved = [json.loads(row[0]) for row in self._sql(
            "SELECT snapshot_json FROM external_submission_authorizations ORDER BY created_at,id",
        )]
        self.assertEqual([1, 2], [item["policy_evidence"]["matched_count"] for item in saved])

    def test_policy_change_after_prepare_rejects_without_external_call(self):
        grant = self._prepare()
        update_setting_values(
            self.settings,
            {"privacy.custom_sensitive_words": '["Matrix", "The"]'},
            operator=self.actor.username,
        )

        with patch(f"{MODULE}.TmdbClient") as client, self.assertRaises(ValueError):
            self._submit(grant)

        client.assert_not_called()
        self.assertEqual(
            "prepared",
            self._sql("SELECT status FROM external_submission_authorizations WHERE id=?", (grant["id"],))[0][0],
        )

    def test_tmdb_consumed_once_and_normal_guard_still_blocks(self):
        grant = self._prepare()
        with patch(f"{MODULE}.TmdbClient") as client:
            client.return_value.search.return_value = []
            outcome = self._submit(grant)
            self.assertEqual("completed", outcome["status"])
            self.assertFalse(outcome["audit_pending"])
            with self.assertRaises(ValueError):
                self._submit(grant)
            client.return_value.search.assert_called_once()
        self.assertEqual("override_completed", self._sql("SELECT status FROM external_submission_blocks WHERE id=?", (self.block.id,))[0][0])
        self.assertIsNotNone(self._block("tmdb"))
        self.assertEqual(self.preview.suggested_name, self._sql("SELECT suggested_name FROM rename_previews")[0][0])

    def test_ai_actual_guard_consumes_permit_and_returns_candidate(self):
        self.block = self._block("ai")
        grant = self._prepare()
        candidate = AiParseCandidate("The Matrix", "movie", 1999, None, None, 95, "synthetic")
        with patch("app.service.ai_parse_service._build_provider") as provider:
            provider.return_value.complete_chat.return_value = {"status": "success", "content": json.dumps({
                "title": candidate.title, "media_type": "movie", "year": 1999, "confidence": 95})}
            outcome = self._submit(grant)
        self.assertEqual("completed", outcome["status"])
        self.assertEqual("The Matrix", outcome["candidates"][0]["title"])
        provider.return_value.complete_chat.assert_called_once()
        self.assertEqual(0, provider.call_args.args[0]["ai.max_retries"])
        self.assertIsNotNone(self._block("ai"))

    def test_expired_changed_preview_source_and_config_do_not_call_network(self):
        changes = [
            ("UPDATE external_submission_authorizations SET expires_at='2000-01-01'", ()),
            ("UPDATE rename_previews SET parsed_year=2000", ()),
            ("UPDATE media_sources SET enabled=0", ()),
            ("UPDATE scan_jobs SET source_invalidated_at='now'", ()),
        ]
        for sql, params in changes:
            with self.subTest(sql=sql):
                grant = self._prepare()
                self._sql(sql, params)
                with patch(f"{MODULE}.TmdbClient") as client, self.assertRaises(ValueError):
                    self._submit(grant)
                client.assert_not_called()
                self._sql("UPDATE rename_previews SET parsed_year=1999")
                self._sql("UPDATE media_sources SET enabled=1")
                self._sql("UPDATE scan_jobs SET source_invalidated_at=NULL")
        grant = self._prepare()
        update_setting_values(self.settings, {"tmdb.api_key": "changed-synthetic-key"}, operator="review-author")
        with patch(f"{MODULE}.TmdbClient") as client, self.assertRaises(ValueError):
            self._submit(grant)
        client.assert_not_called()

    def test_disabled_or_permission_revoked_actor_cannot_consume(self):
        grant = self._prepare()
        for update in ["enabled=0", "enabled=1,permissions_json='[]'"]:
            self._sql(f"UPDATE users SET {update} WHERE id=?", (self.actor.id,))
            with patch(f"{MODULE}.TmdbClient") as client, self.assertRaises(PermissionError):
                self._submit(grant)
            client.assert_not_called()

    def test_other_actor_cannot_read_or_consume_grant(self):
        from dataclasses import replace
        grant = self._prepare()
        self._sql("INSERT INTO users (username,display_name,password_hash,permissions_json,enabled,created_at,updated_at) "
                  "VALUES ('other','other','unused','[\"metadata:submit\"]',1,'now','now')")
        actorId = self._sql("SELECT id FROM users WHERE username='other'")[0][0]
        context = AuditWriteContext(replace(self.actor, id=actorId, username="other"))
        with self.assertRaises(LookupError):
            getExternalSubmissionOutcome(self.settings, self.block.id, grant["id"], context)
        with self.assertRaises(LookupError):
            submitExternalSubmission(self.settings, self.block.id, grant["id"], "测试", context)

    def test_two_prepared_grants_only_one_submission(self):
        grants = [self._prepare(), self._prepare()]
        def run(grant):
            try:
                return self._submit(grant)["status"]
            except ValueError:
                return "rejected"
        with patch(f"{MODULE}.TmdbClient") as client:
            client.return_value.search.return_value = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, grants))
            client.return_value.search.assert_called_once()
        self.assertCountEqual(["completed", "rejected"], results)

    def test_initial_audit_failure_rolls_back_consumption_without_network(self):
        grant = self._prepare()
        with patch(f"{MODULE}.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")), \
                patch(f"{MODULE}.TmdbClient") as client:
            with self.assertRaises(sqlite3.OperationalError):
                self._submit(grant)
        client.assert_not_called()
        self.assertEqual("prepared", self._sql("SELECT status FROM external_submission_authorizations")[0][0])
        self.assertEqual("blocked", list_external_submission_blocks(self.settings).items[0].status)

    def test_outcome_audit_failure_preserves_result_and_maintenance_never_replays(self):
        grant = self._prepare()
        with patch(f"{MODULE}.TmdbClient") as client, patch(f"{MODULE}._finishAudit", side_effect=RuntimeError("synthetic")):
            client.return_value.search.return_value = []
            outcome = self._submit(grant)
        self.assertEqual("completed", outcome["status"])
        self.assertTrue(outcome["audit_pending"])
        with patch(f"{MODULE}.TmdbClient") as client:
            self.assertEqual(1, flushExternalSubmissionAudits(self.settings)["completed"])
            self.assertEqual(0, flushExternalSubmissionAudits(self.settings)["completed"])
        client.assert_not_called()
        self.assertFalse(getExternalSubmissionOutcome(self.settings, self.block.id, grant["id"], self.context)["audit_pending"])

    def test_network_unknown_is_consumed_and_not_replayed(self):
        grant = self._prepare()
        with patch(f"{MODULE}.TmdbClient") as client:
            client.return_value.search.side_effect = TimeoutError("do-not-persist-sensitive-response")
            outcome = self._submit(grant)
            self.assertEqual("unknown", outcome["status"])
            with self.assertRaises(ValueError):
                self._submit(grant)
            with self.assertRaises(ValueError):
                self._prepare()
            client.return_value.search.assert_called_once()
        self.assertNotIn("do-not-persist", self._sql("SELECT outcome_json FROM external_submission_authorizations")[0][0])

    def test_missing_outcome_is_unknown_and_maintenance_does_not_infer_success(self):
        grant = self._prepare()
        self._sql("UPDATE external_submission_authorizations SET status='consumed'")
        self.assertEqual("unknown", getExternalSubmissionOutcome(self.settings, self.block.id, grant["id"], self.context)["status"])
        self.assertEqual(0, flushExternalSubmissionAudits(self.settings)["completed"])

    def test_guard_permit_cannot_expand_payload_or_be_reused(self):
        from dataclasses import asdict
        from app.service.external_submission_guard import ExternalSubmissionPermit, submissionPayloadFingerprint
        from app.service.preview_service import _parsed_from_preview, _get_preview_row
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            parsed = _parsed_from_preview(_get_preview_row(connection, self.preview.id))
        args = ["rename_preview", self.preview.id, self.media_file.name, str(self.media_file), parsed.title, "tmdb"]
        permit = ExternalSubmissionPermit(submissionPayloadFingerprint(*args, asdict(parsed)))
        with self.assertRaises(ValueError):
            check_external_submission(self.settings, *args[:-1], "ai", authorization=permit, parsedPayload=asdict(parsed))
        self.assertTrue(check_external_submission(self.settings, *args, authorization=permit, parsedPayload=asdict(parsed)).allowed)
        with self.assertRaises(ValueError):
            check_external_submission(self.settings, *args, authorization=permit, parsedPayload=asdict(parsed))

    def test_prepare_audit_failure_does_not_leave_grant(self):
        with patch(f"{MODULE}.record_audit_event", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self._prepare()
        self.assertEqual([], self._sql("SELECT id FROM external_submission_authorizations"))

    def test_unknown_cannot_be_bypassed_using_a_new_block_or_decision_patch(self):
        from app.service.external_submission_guard import update_external_submission_block_decision
        grant = self._prepare()
        with patch(f"{MODULE}.TmdbClient") as client:
            client.return_value.search.side_effect = TimeoutError()
            self._submit(grant)
        with self.assertRaises(ValueError):
            update_external_submission_block_decision(self.settings, self.block.id, "blocked", None, None, self.actor.username)
        self.block = self._block("tmdb")
        with self.assertRaises(ValueError):
            self._prepare()

    def test_ai_failed_response_is_unknown_and_raw_data_is_not_persisted(self):
        self.block = self._block("ai")
        grant = self._prepare()
        with patch("app.service.ai_parse_service._build_provider") as provider:
            provider.return_value.complete_chat.return_value = {"status": "failed", "message": "raw-sensitive-response"}
            outcome = self._submit(grant)
        self.assertEqual("unknown", outcome["status"])
        self.assertNotIn("raw-sensitive-response", self._sql("SELECT outcome_json FROM external_submission_authorizations")[0][0])

    def test_known_auth_rejection_is_failed_and_new_confirmation_is_required(self):
        self.block = self._block("ai")
        grant = self._prepare()
        with patch("app.service.ai_parse_service._build_provider") as provider:
            provider.return_value.complete_chat.return_value = {"status": "failed", "error_type": "client", "http_status": 401}
            outcome = self._submit(grant)
        self.assertEqual("failed", outcome["status"])
        self.assertEqual("override_failed", list_external_submission_blocks(self.settings).items[0].status)
        self.assertNotEqual(grant["id"], self._prepare()["id"])

    def test_budget_rejection_is_consumed_but_outcome_audit_records_not_sent(self):
        from app.service.external_request_budget_service import ExternalBudgetWaitError
        for target in ("tmdb", "ai"):
            with self.subTest(target=target):
                self.block = self._block(target)
                grant = self._prepare()
                with patch(f"{MODULE}.TmdbClient") as client, patch("app.service.ai_parse_service._build_provider") as ai:
                    client.return_value.search.side_effect = ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")
                    ai.return_value.complete_chat.return_value = {"status": "failed", "error_type": "budget_wait",
                        "message": "外部请求等待预算已耗尽，本次未发送请求"}
                    outcome = self._submit(grant)
                    self.assertEqual("failed", outcome["status"])
                    self.assertFalse(outcome["submission_executed"])
                    self.assertIn("未发送", outcome["message"])
                    with self.assertRaises(ValueError): self._submit(grant)
                saved = json.loads(self._sql("SELECT outcome_json FROM external_submission_authorizations WHERE id=?", (grant["id"],))[0][0])
                self.assertFalse(saved["submission_executed"])
                details = [json.loads(row[0]) for row in self._sql("SELECT detail_json FROM audit_events")]
                self.assertTrue(any(detail.get("submission_executed") is False for detail in details))

    def test_api_prepare_submit_query_legacy_patch_and_pending_header(self):
        from fastapi.testclient import TestClient
        session = login(self.settings, self.actor.username, "SyntheticPassword123!")
        client = TestClient(create_app(self.settings))
        client.headers["Authorization"] = f"Bearer {session.access_token}"
        url = f"/api/external-submission-blocks/{self.block.id}"
        prepared = client.post(url + "/prepare")
        self.assertEqual(200, prepared.status_code)
        grantId = prepared.json()["id"]
        with patch(f"{MODULE}.TmdbClient") as provider, patch(f"{MODULE}._finishAudit", side_effect=RuntimeError("synthetic")):
            provider.return_value.search.return_value = []
            submitted = client.post(url + "/submit", json={"authorization_id": grantId, "override_reason": "隔离测试确认"})
        self.assertEqual(200, submitted.status_code)
        self.assertEqual("completed", submitted.json()["status"])
        self.assertEqual("pending", submitted.headers["X-MediaAI-Audit-State"])
        self.assertEqual("completed", client.get(url + "/outcome").json()["status"])
        self.assertEqual(409, client.post(url + "/submit", json={"authorization_id": grantId, "override_reason": "隔离测试确认"}).status_code)
        response = client.patch(url, json={"status": "override_submitted", "override_reason": "隔离测试确认"})
        self.assertEqual(400, response.status_code)
        self.assertIn("仅修改状态不会提交", response.json()["detail"])

    def test_schema29_upgrade_preserves_previews_and_is_idempotent(self):
        from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
        self._sql("DROP TABLE external_submission_authorizations")
        self._sql("UPDATE app_meta SET value='29' WHERE key='schema_version'")
        ensure_database(self.settings)
        ensure_database(self.settings)
        self.assertEqual(CURRENT_SCHEMA_VERSION, int(self._sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0]))
        self.assertEqual(1, self._sql("SELECT COUNT(*) FROM rename_previews")[0][0])
        self.assertEqual([], self._sql("SELECT * FROM external_submission_authorizations"))
