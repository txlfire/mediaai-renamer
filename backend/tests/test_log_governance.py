"""日志清理、尾读和导出在隔离目录验证，不访问正式日志。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import setup_logging, shutdown_logging, get_logger, get_llm_logger
from app.service.settings_service import get_effective_settings, list_setting_values, update_setting_values
from app.service.log_service import cleanup_logs, read_log_page, export_logs_text, MAX_TAIL_BYTES, getLogStorageStats
from app.service.operation_log_service import cleanup_operation_logs, export_operation_logs_text


class LogGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempDir.name)
        self.logDir = self.root / "isolated-logs"
        self.logDir.mkdir()
        self.settings = AppSettings(data_dir=self.root, database_path=self.root / "app.sqlite3",
                                    logging=LoggingSettings(log_dir=self.logDir, level="WARNING", console_output=False))
        ensure_database(self.settings)

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def test_hot_logging_path_and_level_apply_without_restarting(self):
        setup_logging(self.settings.logging)
        logger = get_logger(__name__)
        logger.warning("old marker")
        nextDir = self.root / "new-logs"
        update_setting_values(self.settings, {"logging.path": str(nextDir), "logging.level": "DEBUG"})
        logger.debug("new debug marker")
        get_llm_logger().debug("new llm marker")
        self.assertIn("new debug marker", (nextDir / "app.log").read_text(encoding="utf-8"))
        self.assertIn("new llm marker", (nextDir / "llm.log").read_text(encoding="utf-8"))
        self.assertNotIn("new debug marker", (self.logDir / "app.log").read_text(encoding="utf-8"))

    def test_logging_prepare_or_audit_failure_keeps_old_runtime_and_database(self):
        setup_logging(self.settings.logging)
        nextDir = self.root / "new-logs"
        for failure in ("prepare", "audit"):
            with self.subTest(failure=failure):
                target = "app.core.logger._create_file_handler" if failure == "prepare" else "app.service.audit_service.record_audit_event"
                exception = OSError("controlled prepare failure") if failure == "prepare" else sqlite3.OperationalError("controlled audit failure")
                with patch(target, side_effect=exception):
                    with self.assertRaises(ValueError if failure == "prepare" else sqlite3.OperationalError):
                        update_setting_values(self.settings, {"logging.path": str(nextDir), "logging.level": "DEBUG"})
                self.assertEqual(str(self.logDir), get_effective_settings(self.settings)["logging.path"])
                self.assertEqual("WARNING", get_effective_settings(self.settings)["logging.level"])
                get_logger(__name__).warning(f"kept old {failure}")
                self.assertIn(f"kept old {failure}", (self.logDir / "app.log").read_text(encoding="utf-8"))

    def test_concurrent_hot_configuration_matches_last_committed_settings(self):
        setup_logging(self.settings.logging)
        values = [{"logging.path": str(self.root / "first"), "logging.level": "DEBUG"},
                  {"logging.path": str(self.root / "second"), "logging.level": "ERROR"}]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda item: update_setting_values(self.settings, item), values))
        effective = get_effective_settings(self.settings)
        import logging
        logger = logging.getLogger("mediaai")
        self.assertEqual(getattr(logging, effective["logging.level"]), logger.level)
        self.assertTrue(all(Path(handler.baseFilename).parent == Path(effective["logging.path"])
                            for handler in logger.handlers if hasattr(handler, "baseFilename")))

    def _age(self, path: Path, days: int):
        timestamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        os.utime(path, (timestamp, timestamp))
        return timestamp

    def test_instance_config_defaults_do_not_point_cleanup_to_global_logs(self):
        effective = get_effective_settings(self.settings)
        self.assertEqual(str(self.logDir), effective["logging.path"])
        self.assertEqual("WARNING", effective["logging.level"])
        shown = {item.key: item.value for item in list_setting_values(self.settings)}
        self.assertEqual(str(self.logDir), shown["logging.path"])
        update_setting_values(self.settings, {"logging.path": str(self.root / "saved-logs")})
        self.assertEqual(str(self.root / "saved-logs"), get_effective_settings(self.settings)["logging.path"])

    def test_tail_limit_keeps_newest_timestamp_not_last_file(self):
        (self.logDir / "app.log").write_text("2026-10-02 12:00:00 - app - newest\n", encoding="utf-8")
        (self.logDir / "llm.log").write_text("2026-10-01 12:00:00 - llm - old\n", encoding="utf-8")
        page = read_log_page(self.settings, limit=1)
        self.assertEqual("app.log", page["items"][0]["file"])
        self.assertIn("newest", page["items"][0]["message"])
        self.assertTrue(page["truncated"])

    def test_default_log_limit_uses_hot_setting_but_explicit_limit_wins(self):
        (self.logDir / "app.log").write_text("first\nsecond\nthird\n", encoding="utf-8")
        update_setting_values(self.settings, {"operations.log_default_limit": 1})
        page = read_log_page(self.settings)
        self.assertEqual(["third"], [item["message"] for item in page["items"]])
        self.assertEqual(3, len(read_log_page(self.settings, limit=3)["items"]))
        update_setting_values(self.settings, {"operations.log_default_limit": 2})
        self.assertEqual(2, len(read_log_page(self.settings)["items"]))

    def test_tail_read_is_bounded_redacted_and_reports_omission(self):
        path = self.logDir / "app.log"
        path.write_bytes(b"old\n" * MAX_TAIL_BYTES + b"https://user:synthetic-secret@example.invalid/dav/\nlatest\n")
        with patch.object(Path, "read_text", side_effect=AssertionError("full text read forbidden")):
            page = read_log_page(self.settings, limit=2)
            exported = export_logs_text(self.settings)
        self.assertTrue(page["truncated"])
        self.assertIn("尾部", page["message"])
        self.assertEqual("latest", page["items"][-1]["message"])
        self.assertNotIn("synthetic-secret", str(page) + exported)
        self.assertLessEqual(len(exported.encode("utf-8")), 1024 * 1024)

    def test_partial_long_line_is_not_shown_as_unredacted_fragment(self):
        path = self.logDir / "app.log"
        path.write_bytes(b"https://user:" + b"x" * MAX_TAIL_BYTES + b"synthetic-secret@example.invalid/no-newline")
        page = read_log_page(self.settings)
        self.assertEqual([], page["items"])
        self.assertTrue(page["truncated"])
        self.assertNotIn("synthetic-secret", str(page))

    def test_archive_retention_uses_original_timestamp_and_preserves_unknown_files(self):
        archiveDir = self.logDir / "archive"
        archiveDir.mkdir()
        expired = archiveDir / "app.log.1.zip"
        expired.write_bytes(b"expired archive")
        self._age(expired, 100)
        valid = archiveDir / "error.log.2.zip"
        valid.write_bytes(b"valid archive")
        self._age(valid, 10)
        unknown = self.logDir / "important.txt"
        unknown.write_text("must remain", encoding="utf-8")
        self._age(unknown, 100)
        active = self.logDir / "app.log"
        active.write_text("active", encoding="utf-8")
        self._age(active, 100)
        rotated = self.logDir / "batch.log.1"
        rotated.write_text("rotated", encoding="utf-8")
        originalTime = self._age(rotated, 8)
        report = cleanup_logs(self.settings)
        self.assertEqual(1, report["deleted_count"])
        self.assertEqual(1, report["archived_count"])
        self.assertFalse(expired.exists())
        self.assertTrue(valid.exists() and unknown.exists() and active.exists())
        self.assertAlmostEqual(originalTime, (archiveDir / "batch.log.1.zip").stat().st_mtime, places=4)

    def test_existing_archive_is_not_overwritten_or_original_deleted(self):
        archiveDir = self.logDir / "archive"
        archiveDir.mkdir()
        archive = archiveDir / "app.log.1.zip"
        archive.write_bytes(b"keep archive")
        rotated = self.logDir / "app.log.1"
        rotated.write_bytes(b"keep original")
        self._age(rotated, 8)
        report = cleanup_logs(self.settings)
        self.assertEqual(0, report["archived_count"])
        self.assertEqual(b"keep archive", archive.read_bytes())
        self.assertEqual(b"keep original", rotated.read_bytes())

    def test_database_cleanup_has_one_global_row_budget(self):
        update_setting_values(self.settings, {"operation_logs.cleanup_batch_size": 100})
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO operation_logs (task_type, task_id, level, stage, message, approx_bytes, created_at) "
                                   "VALUES ('scan_job', 1, 'info', 'progress', 'old', 10, '2000-01-01T00:00:00+00:00')", [()] * 250)
            connection.commit()
        first = cleanup_operation_logs(self.settings)
        self.assertEqual(100, first.deleted)
        self.assertTrue(first.budget_exhausted)
        self.assertEqual(100, cleanup_operation_logs(self.settings).deleted)
        self.assertEqual(50, cleanup_operation_logs(self.settings).deleted)

    def test_archive_keeps_replacement_created_during_compression(self):
        import zipfile
        rotated = self.logDir / "app.log.1"
        rotated.write_bytes(b"original old log")
        self._age(rotated, 8)
        originalWrite = zipfile.ZipFile.write

        def replaceDuringWrite(archive, filename, *args, **kwargs):
            originalWrite(archive, filename, *args, **kwargs)
            Path(filename).unlink()
            Path(filename).write_bytes(b"new rotation replacement")

        with patch.object(zipfile.ZipFile, "write", new=replaceDuringWrite):
            report = cleanup_logs(self.settings)
        self.assertEqual(b"new rotation replacement", rotated.read_bytes())
        self.assertEqual(0, report["archived_count"])
        self.assertTrue(report["has_more"])
        self.assertFalse((self.logDir / "archive" / "app.log.1.zip").exists())

    def test_delete_skips_changed_identity(self):
        from app.service.log_service import _fileIdentity, _removeUnchanged
        rotated = self.logDir / "app.log.1"
        rotated.write_bytes(b"old")
        identity = _fileIdentity(rotated)
        rotated.write_bytes(b"new longer content")
        self.assertFalse(_removeUnchanged(rotated, identity, self.logDir))
        self.assertTrue(rotated.exists())

    def test_task_export_is_byte_bounded_and_does_not_include_detail_secrets(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO operation_logs (task_type, task_id, level, stage, message, detail_json, approx_bytes, created_at) "
                                   "VALUES ('scan_job', 1, 'info', 'progress', ?, ?, 4000, 'now')",
                                   [("x" * 4000, '{"token":"synthetic-secret"}')] * 300)
            connection.commit()
        text = export_operation_logs_text(self.settings, task_type="scan_job", task_id=1)
        self.assertIn("导出达到", text)
        self.assertLessEqual(len(text.encode("utf-8")), 1024 * 1024)
        self.assertNotIn("synthetic-secret", text)

    def test_file_size_budget_trims_rotated_logs_but_keeps_active_and_unknown_files(self):
        update_setting_values(self.settings, {"logging.max_total_mb": 16})
        rotated = self.logDir / "app.log.1"
        with rotated.open("wb") as stream:
            stream.truncate(20 * 1024 * 1024)
        active = self.logDir / "app.log"
        active.write_bytes(b"active")
        unknown = self.logDir / "unrelated.bin"
        unknown.write_bytes(b"unknown")
        report = cleanup_logs(self.settings)
        self.assertFalse(rotated.exists())
        self.assertTrue(active.exists() and unknown.exists())
        self.assertEqual(1, report["deleted_count"])
        self.assertFalse(report["over_budget"])
        self.assertEqual(7, report["unknown_file_bytes"])

    def test_active_logs_over_budget_are_reported_not_deleted(self):
        update_setting_values(self.settings, {"logging.max_total_mb": 16})
        for name in ("app.log", "llm.log"):
            with (self.logDir / name).open("wb") as stream:
                stream.truncate(10 * 1024 * 1024)
        report = cleanup_logs(self.settings)
        self.assertTrue(report["over_budget"])
        self.assertEqual(0, report["deleted_count"])
        self.assertIn("活动日志不删除", report["message"])
        self.assertTrue((self.logDir / "app.log").exists())

    def test_capacity_distinguishes_logical_content_from_database_and_reusable_space(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO operation_logs (task_type, task_id, level, stage, message, approx_bytes, created_at) "
                                   "VALUES ('scan_job', 1, 'info', 'progress', ?, 4000, 'now')", [("x" * 4000,)] * 100)
            connection.commit()
        before = getLogStorageStats(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("DELETE FROM operation_logs")
            connection.commit()
        after = getLogStorageStats(self.settings)
        self.assertEqual(400000, before["operation_log_bytes"])
        self.assertEqual(0, after["operation_log_bytes"])
        self.assertEqual(before["database_file_bytes"], after["database_file_bytes"])
        self.assertGreater(after["database_reusable_bytes"], 0)
        self.assertIn("不一定缩小", after["message"])

    def test_auxiliary_failure_summary_does_not_echo_raw_exception_secrets(self):
        from app.service.operation_log_service import _warnLogFailure
        with patch("app.service.operation_log_service.logger") as logger:
            _warnLogFailure(OSError("Bearer synthetic-secret /private/path"))
        self.assertNotIn("synthetic-secret", str(logger.mock_calls))
        self.assertIn("OSError", str(logger.mock_calls))
