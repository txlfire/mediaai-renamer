"""历史维护工具隔离回归，不访问正式日志或真实凭据。"""

from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.redact_history import main
from app.service.history_redaction_service import redactHistory


class HistoryRedactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.logDir = root / "logs"
        self.logDir.mkdir()
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3", logging=LoggingSettings(log_dir=self.logDir))
        ensure_database(self.settings)
        self.url = "https://synthetic-user:synthetic-password@nas.invalid/dav/"
        self.logFile = self.logDir / "batch.log"
        self.logFile.write_bytes((self.url + "\r\nnormal\r\n").encode("utf-8"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO audit_events(event_type,target_type,action,result,summary,detail_json,created_at) VALUES ('test','source','test','success',?,?,'now')",
                (self.url, '{"path":"' + self.url + '"}'),
            )
            connection.execute(
                "INSERT INTO operation_logs(task_type,task_id,level,stage,message,detail_json,approx_bytes,created_at) VALUES ('scan_job',1,'info','test',?,?,999,'now')",
                (self.url, '{"path":"' + self.url + '"}'),
            )
            connection.commit()

    def _row(self, table):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            return connection.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()

    def test_preview_preserves_database_and_file(self):
        beforeRow = self._row("audit_events")
        beforeBytes = self.logFile.read_bytes()
        report = redactHistory(self.settings)
        self.assertEqual(2, report.databaseRows)
        self.assertEqual(1, report.matchedFiles)
        self.assertEqual(0, report.changedFiles)
        self.assertEqual(beforeRow, self._row("audit_events"))
        self.assertEqual(beforeBytes, self.logFile.read_bytes())
        self.assertNotIn("synthetic-password", str(report.toDict()))

    def test_apply_requires_stopped_confirmation(self):
        with self.assertRaisesRegex(ValueError, "停止"):
            redactHistory(self.settings, apply=True)
        self.assertIn("synthetic-password", self.logFile.read_text())

    def test_apply_preserves_ids_and_recalculates_operation_bytes(self):
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(2, report.databaseRows)
        self.assertEqual(1, report.changedFiles)
        self.assertEqual("complete", report.auditStatus)
        self.assertNotIn("synthetic-password", str(self._row("audit_events")))
        self.assertEqual(1, self._row("operation_logs")[0])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            message, detail, size = connection.execute("SELECT message,detail_json,approx_bytes FROM operation_logs WHERE id=1").fetchone()
        self.assertEqual(len(message.encode()) + len(detail.encode()), size)
        self.assertTrue(self.logFile.read_bytes().endswith(b"\r\nnormal\r\n"))

    def test_second_apply_is_idempotent(self):
        redactHistory(self.settings, apply=True, serviceStopped=True)
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(0, report.databaseRows)
        self.assertEqual(0, report.changedFiles)

    def test_zip_archive_contents_are_redacted_without_extraction(self):
        archiveDir = self.logDir / "archive"
        archiveDir.mkdir()
        archivePath = archiveDir / "batch.log.1.zip"
        with zipfile.ZipFile(archivePath, "w") as archive:
            archive.writestr("nested/batch.log.1", self.url)
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(2, report.changedFiles)
        with zipfile.ZipFile(archivePath) as archive:
            self.assertEqual(["nested/batch.log.1"], archive.namelist())
            self.assertNotIn(b"synthetic-password", archive.read("nested/batch.log.1"))
        self.assertFalse((archiveDir / "nested").exists())

    def test_invalid_utf8_is_skipped_without_replacement(self):
        self.logFile.write_bytes(b"\xff" + self.url.encode())
        before = self.logFile.read_bytes()
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report.skippedFiles)
        self.assertEqual(before, self.logFile.read_bytes())
        self.assertEqual([], list(self.logDir.glob(".redaction-*")))

    def test_overlong_line_preserves_original_file(self):
        self.logFile.write_bytes(self.url.encode() + b"x" * (1024 * 1024))
        before = self.logFile.read_bytes()
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report.skippedFiles)
        self.assertEqual(before, self.logFile.read_bytes())

    def test_non_log_zip_member_is_skipped(self):
        path = self.logDir / "batch.log.1.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("config.toml", self.url)
        before = path.read_bytes()
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report.skippedFiles)
        self.assertEqual(before, path.read_bytes())

    def test_replace_failure_reports_partial_result_without_losing_file(self):
        before = self.logFile.read_bytes()
        with patch("app.service.history_redaction_service.os.replace", side_effect=PermissionError("synthetic")):
            report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report.failedFiles)
        self.assertEqual(before, self.logFile.read_bytes())
        self.assertNotIn("synthetic-password", str(self._row("audit_events")))

    def test_business_paths_and_unrelated_files_are_not_modified(self):
        unrelated = self.logDir / "database-backup.txt"
        unrelated.write_text(self.url)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO media_sources(name,path,path_type,protocol,enabled,created_at,updated_at) VALUES ('historical',?,'webdav','webdav',0,'now','now')", (self.url,))
            connection.commit()
        redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(self.url, unrelated.read_text())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(self.url, connection.execute("SELECT path FROM media_sources").fetchone()[0])

    def test_locked_database_rejects_apply_before_file_changes(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                redactHistory(self.settings, apply=True, serviceStopped=True)
            connection.rollback()
        self.assertIn("synthetic-password", self.logFile.read_text())

    def test_cli_preview_only_prints_safe_counts(self):
        config = self.settings.data_dir / "test.toml"
        config.write_text('[paths]\ndata_dir="' + self.settings.data_dir.as_posix() + '"\ndatabase_path="' + self.settings.database_path.as_posix() + '"\n[logging]\nlog_dir="' + self.logDir.as_posix() + '"\n')
        output = StringIO()
        with patch("sys.argv", ["redact_history", "--config", str(config)]), redirect_stdout(output):
            self.assertEqual(0, main())
        self.assertNotIn("synthetic-password", output.getvalue())
        self.assertIn('"apply": false', output.getvalue())

    def test_completion_audit_failure_does_not_hide_applied_changes(self):
        from app.service.history_redaction_service import _recordMaintenance
        def record(connection, result, summary):
            if result != "started":
                raise sqlite3.OperationalError("synthetic")
            _recordMaintenance(connection, result, summary)
        with patch("app.service.history_redaction_service._recordMaintenance", side_effect=record):
            report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual("completion_failed", report.auditStatus)
        self.assertEqual(1, report.changedFiles)
        self.assertNotIn("synthetic-password", self.logFile.read_text())

    def test_start_audit_failure_prevents_database_commit_and_file_changes(self):
        before = self._row("audit_events")
        with patch("app.service.history_redaction_service._recordMaintenance", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(before, self._row("audit_events"))
        self.assertIn("synthetic-password", self.logFile.read_text())

    def test_oversized_zip_expansion_is_skipped(self):
        path = self.logDir / "batch.log.1.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("batch.log.1", "x" * (2 * 1024 * 1024))
        before = path.read_bytes()
        report = redactHistory(self.settings, apply=True, serviceStopped=True, maxFileMb=1)
        self.assertEqual(1, report.skippedFiles)
        self.assertEqual(before, path.read_bytes())

    def test_effective_log_path_and_file_size_limit_are_respected(self):
        alternate = self.settings.data_dir / "alternate"
        alternate.mkdir()
        alternateFile = alternate / "app.log"
        alternateFile.write_bytes(self.url.encode() + b"x" * (2 * 1024 * 1024))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO system_settings(key,category,value,description,sensitive,operator,created_at,updated_at) VALUES ('logging.path','logging',?,'test',0,'test','now','now')", (str(alternate),))
            connection.commit()
        report = redactHistory(self.settings, apply=True, serviceStopped=True, maxFileMb=1)
        self.assertEqual(1, report.skippedFiles)
        self.assertIn("synthetic-password", self.logFile.read_text())

    def test_access_control_failure_keeps_original_file(self):
        before = self.logFile.read_bytes()
        with patch("app.service.history_redaction_service._copyAccessControl", side_effect=OSError("synthetic")):
            report = redactHistory(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report.failedFiles)
        self.assertEqual(before, self.logFile.read_bytes())

    @unittest.skipUnless(os.name == "nt", "仅 Windows 验证文件 DACL")
    def test_windows_protected_access_control_is_preserved(self):
        env = dict(os.environ, MEDIAAI_REDACT_SOURCE=str(self.logFile))
        prefix = "$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
        protect = "$acl=Get-Acl -LiteralPath $env:MEDIAAI_REDACT_SOURCE; $acl.SetAccessRuleProtection($true,$true); Set-Acl -LiteralPath $env:MEDIAAI_REDACT_SOURCE -AclObject $acl; (Get-Acl -LiteralPath $env:MEDIAAI_REDACT_SOURCE).Sddl"
        before = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + protect], env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        report = redactHistory(self.settings, apply=True, serviceStopped=True)
        after = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + "(Get-Acl -LiteralPath $env:MEDIAAI_REDACT_SOURCE).Sddl"], env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        self.assertEqual(1, report.changedFiles)
        self.assertEqual(before, after)
