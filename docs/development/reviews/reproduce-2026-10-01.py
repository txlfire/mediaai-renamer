"""日志与异常专项证据：只使用临时资源、虚构凭据和故障注入。

断言确认当前缺陷，不是修复后的产品回归标准；不导入默认应用入口。
"""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

projectRoot = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(projectRoot / "backend"))

from app.api.users import UserCreateRequest, create_user_api
from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.audit_service import sanitize_audit_detail
from app.service.ai_provider import AiProviderConfig, OpenAiCompatibleProvider
from app.service.auth_service import bootstrap_admin, list_users
from app.service.media_source_service import create_media_source, test_media_source_connection_payload
from app.service.operation_log_service import record_operation_log
from app.service.scan_service import run_full_scan
from app.service.settings_service import update_setting_values
from app.service.shared_protocols.base import ConnectionTestResult
from app.service.shared_protocols.webdav import WebDavProtocol, _normalize_webdav_url, _propfind
from app.service.tmdb_client import TmdbClient


class ObservabilityEvidence(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempDir.name)
        self.mediaDir = self.root / "media"
        self.mediaDir.mkdir()
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "review.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
        )
        ensure_database(self.settings)
        update_setting_values(self.settings, {
            "scan.minimum_file_size": 0,
            "scan.batch_interval_seconds": 0,
        })

    def tearDown(self):
        self.tempDir.cleanup()

    def writeLog(self, **kwargs):
        return record_operation_log(
            self.settings, task_type="scan_job", task_id=1,
            level="info", stage="evidence", message="review", **kwargs,
        )

    def test_R27_serialization_is_outside_log_fallback(self):
        with self.assertRaises(TypeError):
            self.writeLog(detail={"unsupported": object()})

    def test_R27_connection_is_outside_log_fallback(self):
        with patch("app.service.operation_log_service._connect", side_effect=sqlite3.OperationalError("synthetic connect failure")):
            with self.assertRaises(sqlite3.OperationalError):
                self.writeLog()

    def test_R28_audit_failure_after_user_is_committed(self):
        actor = bootstrap_admin(self.settings, "reviewadmin", "review", "temporary-review-password")
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings=self.settings)),
            client=None, headers={},
        )
        payload = UserCreateRequest(
            username="reviewuser", displayName="review", password="temporary-review-password",
            permissions=[], enabled=True,
        )
        with patch("app.api.users.record_audit_event", side_effect=sqlite3.OperationalError("synthetic audit failure")):
            with self.assertRaises(sqlite3.OperationalError):
                create_user_api(payload, request, actor)
        self.assertIn("reviewuser", [user.username for user in list_users(self.settings)])

    def test_R29_cleanup_failure_masks_committed_scan(self):
        (self.mediaDir / "Review.2024.mkv").write_bytes(b"review")
        source = create_media_source(self.settings, "review", self.mediaDir, True)
        with patch("app.service.scan_service.cleanup_operation_logs", side_effect=RuntimeError("synthetic cleanup failure")):
            with self.assertRaises(sqlite3.OperationalError) as caught:
                run_full_scan(self.settings, source.id)
        self.assertIn("no such savepoint", str(caught.exception))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            row = connection.execute("SELECT status FROM scan_jobs ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row[0], "completed")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_files").fetchone()[0], 1)

    def test_R30_tmdb_detail_failure_disappears(self):
        with patch.object(TmdbClient, "_get_json", side_effect=RuntimeError("synthetic detail failure")):
            self.assertEqual(TmdbClient()._detail_for_item({"id": 1}, "movie"), {})

    def test_R32_malformed_webdav_xml_escapes_conversion(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<invalid"
        with patch("app.service.shared_protocols.webdav._request", return_value=response):
            with self.assertRaises(ET.ParseError):
                _propfind("https://review.invalid/dav/", None)

    def test_R32_invalid_ai_encoding_escapes_conversion(self):
        provider = OpenAiCompatibleProvider(AiProviderConfig(
            provider="openai", model="review", api_key="synthetic-key",
            base_url="https://review.invalid/v1", timeout_ms=1000, max_retries=0,
        ))
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b"\xff"
        with patch("app.service.ai_provider.url_request.urlopen", return_value=response):
            with self.assertRaises(UnicodeDecodeError):
                provider.complete_chat([], max_tokens=1, temperature=0)

    def test_R33_embedded_fake_secret_is_kept_and_logged(self):
        fakeUrl = "https://review:synthetic-secret@review.invalid/dav/"
        self.assertIn("synthetic-secret", _normalize_webdav_url(fakeUrl))
        self.assertIn("synthetic-secret", sanitize_audit_detail({"path": fakeUrl})["path"])
        with patch.object(WebDavProtocol, "test_connection", return_value=ConnectionTestResult(True, "mock")):
            with patch("app.service.media_source_service.get_batch_logger") as logger:
                test_media_source_connection_payload("webdav", fakeUrl)
                self.assertIn("synthetic-secret", str(logger.return_value.info.call_args))

    def test_R34_options_success_does_not_prove_directory_readable(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b"<invalid"
        with patch("app.service.shared_protocols.webdav._request", return_value=response):
            result = WebDavProtocol().test_connection("https://review.invalid/dav/")
            self.assertTrue(result.success)
            self.assertTrue(result.readable)
            with self.assertRaises(ET.ParseError):
                WebDavProtocol().list_directories("https://review.invalid/dav/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
