"""历史 URL 凭据的读出保护与新审计脱敏。"""

from pathlib import Path
import tempfile
import unittest

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.audit_service import sanitize_audit_detail
from app.service.log_service import read_log_items, export_logs_text
from app.utils.redaction import redactUrlCredentials


class UrlRedactionTest(unittest.TestCase):
    def test_embedded_credentials_are_redacted(self):
        for value in (
            "https://review:synthetic-password@nas.invalid/dav/",
            'path="https://review:synthetic-password@nas.invalid/dav/"',
            "HTTPS://review:synthetic-password@nas.invalid:5006/dav/",
        ):
            result = redactUrlCredentials(value)
            self.assertNotIn("synthetic-password", result)
            self.assertIn("nas.invalid", result)

    def test_nested_details_keep_non_sensitive_structure(self):
        value = {"path": "https://review:synthetic-password@nas.invalid/dav/", "items": [{"api_key": "synthetic-key"}], "count": 3}
        result = sanitize_audit_detail(value)
        self.assertNotIn("synthetic-password", str(result))
        self.assertNotIn("synthetic-key", str(result))
        self.assertEqual(3, result["count"])

    def test_historical_file_logs_are_safe_when_read_and_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "batch.log").write_text("path=https://review:synthetic-password@nas.invalid/dav/\n", encoding="utf-8")
            settings = AppSettings(data_dir=root / "data", database_path=root / "data/test.sqlite3", logging=LoggingSettings(log_dir=root))
            ensure_database(settings)
            self.assertNotIn("synthetic-password", str(read_log_items(settings)))
            self.assertNotIn("synthetic-password", export_logs_text(settings))
