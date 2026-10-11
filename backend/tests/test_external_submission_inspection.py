"""外发核对只提供有界脱敏摘要，人工观察不得重放或解除未知保护。"""

from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.inspect_external_submissions import inspectExternalSubmissions, recordExternalObservation, main


class ExternalSubmissionInspectionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = AppSettings(data_dir=self.root, database_path=self.root / "test.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            for index, outcome in enumerate((None, '{"status":"unknown"}', '{"status":"completed"}', 'broken')):
                connection.execute("INSERT INTO external_submission_authorizations "
                    "(id,block_id,actor_id,actor_name,fingerprint,snapshot_json,context_json,created_at,expires_at,status,outcome_json) "
                    "VALUES (?,1,1,'private-user','hash','private-path-secret','private-token','2026','2027','consumed',?)",
                    (str(index), outcome))
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.directory.cleanup()

    def test_readonly_bounded_summary_omits_payloads_and_classifies_unknowns(self):
        result = inspectExternalSubmissions(self.settings, limit=2)
        self.assertTrue(result["has_more"])
        self.assertEqual("1", result["next_id"])
        self.assertTrue(result["items"][0]["may_still_be_running"])
        remaining = inspectExternalSubmissions(self.settings, limit=2, afterId=result["next_id"])
        self.assertTrue(remaining["items"][0]["saved_result_pending_audit"])
        self.assertTrue(remaining["items"][1]["corrupt_outcome"])
        for secret in ("private-user", "private-path", "private-token"):
            self.assertNotIn(secret, str(result) + str(remaining))
        self.assertEqual(0, result["network_requests"])

    def test_observation_creates_audit_without_overwriting_or_unlocking(self):
        report = recordExternalObservation(self.settings, "1", "verified_received")
        self.assertTrue(report["audit_recorded"])
        self.assertFalse(report["resubmission_enabled"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(("consumed", '{"status":"unknown"}'), connection.execute(
                "SELECT status,outcome_json FROM external_submission_authorizations WHERE id='1'").fetchone())
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='external_submission.manual_observation'").fetchone()[0])

    def test_audit_failure_rolls_back_and_leaves_unknown_unchanged(self):
        with patch("app.inspect_external_submissions.record_audit_event", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.Error):
                recordExternalObservation(self.settings, "1", "undetermined")
        self.assertTrue(inspectExternalSubmissions(self.settings)["items"][1]["requires_manual_check"])

    def test_missing_database_not_created(self):
        settings = AppSettings(database_path=self.root / "missing.sqlite3")
        with self.assertRaises(sqlite3.Error):
            inspectExternalSubmissions(settings)
        self.assertFalse(settings.database_path.exists())

    def test_cli_write_requires_stopped_confirmation_before_loading(self):
        config = self.root / "config.toml"
        config.touch()
        with patch("sys.argv", ["inspect", "--config", str(config), "--grant-id", "1",
              "--record-observation", "undetermined"]), patch("app.inspect_external_submissions.load_settings") as load:
            with self.assertRaises(SystemExit):
                main()
        load.assert_not_called()

    def test_invalid_observation_and_missing_id_are_rejected(self):
        with self.assertRaises(ValueError):
            recordExternalObservation(self.settings, "1", "success")
        with self.assertRaises(LookupError):
            recordExternalObservation(self.settings, "missing", "undetermined")
