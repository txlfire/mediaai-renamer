import tempfile
import unittest
import sqlite3
import json
from contextlib import closing
from unittest.mock import patch
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source, update_media_source
from app.service.shared_protocols.base import ConnectionTestResult
from app.service.pending_file_service import list_pending_files
from app.service.preview_service import generate_rename_previews, list_rename_previews
from app.service.scan_service import (
    _upsert_scan_file_index,
    get_scan_mode_suggestion,
    list_media_files,
    list_scan_jobs,
    recover_interrupted_scan_jobs,
    run_full_scan,
)
from app.service.settings_service import update_setting_values
from app.service.operation_log_service import list_operation_logs


class ScanServiceTest(unittest.TestCase):
    def test_local_scan_reports_progress_before_reading_the_next_file(self):
        from app.service.scan_service import _stat_with_nfs_retry
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            mediaRoot = root / "media"
            mediaRoot.mkdir()
            for name in ("a.mkv", "b.mkv"):
                (mediaRoot / name).write_bytes(b"video")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "stream", mediaRoot)
            update_setting_values(settings, {"scan.batch_size": 1, "scan.batch_interval_seconds": 0})
            observed = []

            def inspectBeforeStat(path, retryCount, timeoutSeconds):
                if path.name == "b.mkv":
                    with closing(sqlite3.connect(settings.database_path)) as connection:
                        jobId = connection.execute("SELECT MAX(id) FROM scan_jobs").fetchone()[0]
                        self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM media_files").fetchone()[0])
                    page = list_operation_logs(settings, task_type="scan_job", task_id=jobId)
                    observed.extend(item.progress_current for item in page.items if item.stage == "scan_progress")
                return _stat_with_nfs_retry(path, retryCount, timeoutSeconds)

            with patch("app.service.scan_service._stat_with_nfs_retry", side_effect=inspectBeforeStat):
                job = run_full_scan(settings, source.id)
            self.assertEqual([1], observed)
            self.assertEqual(2, job.video_count)

    def test_scan_audit_failure_prevents_task_creation(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            (media / "movie.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "audit", media, True)
            with patch("app.service.scan_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic audit outage")):
                with self.assertRaises(sqlite3.OperationalError):
                    run_full_scan(settings, source.id)
            self.assertEqual([], list_scan_jobs(settings))
            self.assertEqual([], list_media_files(settings))

    def test_scan_completion_audit_failure_rolls_back_published_results(self):
        from app.service.audit_service import record_audit_event
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            (media / "movie.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "audit", media, True)

            def failSuccessfulCompletion(*args, **kwargs):
                if kwargs["result"] == "success":
                    raise sqlite3.OperationalError("synthetic final audit outage")
                return record_audit_event(*args, **kwargs)

            with patch("app.service.scan_service.record_audit_event", side_effect=failSuccessfulCompletion):
                with self.assertRaises(sqlite3.OperationalError):
                    run_full_scan(settings, source.id)
            self.assertEqual([], list_media_files(settings))
            self.assertEqual("failed", list_scan_jobs(settings)[0].status)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                events = connection.execute("SELECT result FROM audit_events WHERE target_type='scan_job' ORDER BY id").fetchall()
                self.assertEqual([("pending",), ("failed",)], events)

    def test_progress_logs_are_readable_before_business_results_are_published(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            mediaRoot = root / "media"
            mediaRoot.mkdir()
            for name in ("a.mkv", "b.mkv"):
                (mediaRoot / name).write_bytes(b"video")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "source", mediaRoot)
            update_setting_values(settings, {"scan.batch_size": 1, "scan.batch_interval_seconds": 1})
            observed = []

            def inspectProgress(_):
                with closing(sqlite3.connect(settings.database_path, timeout=0.2)) as connection:
                    jobId = connection.execute("SELECT id FROM scan_jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM media_files").fetchone()[0])
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scan_file_index").fetchone()[0])
                    page = list_operation_logs(settings, task_type="scan_job", task_id=jobId)
                    self.assertTrue(page.running)
                    observed.append([item.progress_current for item in page.items if item.stage == "scan_progress"])

            with patch("app.service.scan_service.time.sleep", side_effect=inspectProgress):
                job = run_full_scan(settings, source.id)
            self.assertEqual([[1], [1, 2]], observed)
            self.assertEqual("completed", job.status)
            self.assertEqual(2, len(list_media_files(settings, scan_job_id=job.id)))

    def test_progress_survives_failure_while_staged_business_results_are_discarded(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            mediaRoot = root / "media"
            mediaRoot.mkdir()
            (mediaRoot / "a.mkv").write_bytes(b"video")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "source", mediaRoot)
            update_setting_values(settings, {"scan.batch_size": 1, "scan.batch_interval_seconds": 1})
            with patch("app.service.scan_service.time.sleep", side_effect=RuntimeError("controlled interruption")):
                with self.assertRaisesRegex(RuntimeError, "controlled interruption"):
                    run_full_scan(settings, source.id)
            job = list_scan_jobs(settings, media_source_id=source.id)[0]
            page = list_operation_logs(settings, task_type="scan_job", task_id=job.id)
            self.assertEqual("failed", job.status)
            self.assertIn("scan_progress", [item.stage for item in page.items])
            self.assertIn("scan_failed", [item.stage for item in page.items])
            self.assertEqual([], list_media_files(settings, scan_job_id=job.id))
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scan_file_index").fetchone()[0])

    def test_cleanup_failure_does_not_rollback_completed_scan_or_mask_primary_error(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            mediaRoot = root / "media"
            mediaRoot.mkdir()
            (mediaRoot / "a.mkv").write_bytes(b"video")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "source", mediaRoot)
            with patch("app.service.scan_service.cleanup_operation_logs", side_effect=RuntimeError("controlled cleanup failure")):
                job = run_full_scan(settings, source.id)
            self.assertEqual("completed", job.status)
            self.assertEqual(1, len(list_media_files(settings, scan_job_id=job.id)))
            page = list_operation_logs(settings, task_type="scan_job", task_id=job.id)
            self.assertIn("log_cleanup_failed", [item.stage for item in page.items])

    def test_concurrent_index_write_is_not_overwritten_by_staged_scan(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            mediaRoot = root / "media"
            mediaRoot.mkdir()
            mediaFile = mediaRoot / "a.mkv"
            mediaFile.write_bytes(b"video")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "source", mediaRoot)
            update_setting_values(settings, {"scan.batch_size": 1, "scan.batch_interval_seconds": 1})

            def independentWrite(_):
                with closing(sqlite3.connect(settings.database_path, timeout=0.2)) as connection:
                    _upsert_scan_file_index(connection, source.id, 999, mediaFile, mediaRoot, 999, "external", "active", "external")
                    connection.commit()

            with patch("app.service.scan_service.time.sleep", side_effect=independentWrite):
                with self.assertRaisesRegex(ValueError, "索引已被其他操作修改"):
                    run_full_scan(settings, source.id)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual((999, "external"), connection.execute("SELECT file_size, updated_at FROM scan_file_index").fetchone())

    def test_source_change_between_readiness_and_scan_claim_never_creates_job(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            oldRoot, newRoot = root / "old", root / "new"
            oldRoot.mkdir()
            newRoot.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "source", oldRoot)

            def changeSource(*args):
                update_media_source(settings, source.id, "changed", newRoot, True, clear_history_on_path_change=True)
                return ConnectionTestResult(True, "ready")

            with patch("app.service.shared_protocols.local.LocalProtocol.check_scan_ready", side_effect=changeSource):
                with self.assertRaisesRegex(ValueError, "媒体源已变化"):
                    run_full_scan(settings, source.id)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0])

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=2, batch_interval_seconds=0),
        )

    def test_run_full_scan_persists_only_video_files_with_batch_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("a", encoding="utf-8")
            (media_dir / "b.MP4").write_text("b", encoding="utf-8")
            (media_dir / "poster.jpg").write_text("poster", encoding="utf-8")
            nested = media_dir / "season"
            nested.mkdir()
            (nested / "c.rmvb").write_text("c", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {"scan.batch_size": 2, "scan.batch_interval_seconds": 0},
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)

            job = run_full_scan(settings, source.id)
            files = list_media_files(settings)
            jobs = list_scan_jobs(settings)

            self.assertEqual("completed", job.status)
            self.assertEqual(4, job.scanned_count)
            self.assertEqual(3, job.video_count)
            self.assertEqual(2, job.batch_size)
            self.assertEqual(0, job.batch_interval_seconds)
            self.assertEqual(3, len(files))
            self.assertEqual(1, len(jobs))
            self.assertEqual({"a.mkv", "b.MP4", "c.rmvb"}, {file.file_name for file in files})
            self.assertEqual("full", job.scan_mode)
            self.assertEqual(3, job.new_count)
            self.assertEqual(0, job.changed_count)
            self.assertEqual(0, job.skipped_count)
            self.assertEqual(0, job.missing_count)
            self.assertEqual(3, job.indexed_count)

    def test_incremental_scan_skips_unchanged_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            first_job = run_full_scan(settings, source.id)
            second_job = run_full_scan(settings, source.id, scan_mode="incremental")
            second_files = list_media_files(settings, scan_job_id=second_job.id)

            self.assertEqual("completed", first_job.status)
            self.assertEqual("incremental", second_job.scan_mode)
            self.assertEqual(0, second_job.video_count)
            self.assertEqual(0, second_job.new_count)
            self.assertEqual(0, second_job.changed_count)
            self.assertEqual(1, second_job.skipped_count)
            self.assertEqual(0, second_job.missing_count)
            self.assertEqual(1, second_job.indexed_count)
            self.assertEqual([], second_files)

    def test_incremental_scan_skips_unchanged_files_without_regenerating_previews(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            show_dir = media_dir / "Better Show"
            show_dir.mkdir(parents=True)
            episode = show_dir / "S01E02.mkv"
            episode.write_text("episode", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            first_job = run_full_scan(settings, source.id)
            first_summary = generate_rename_previews(settings, scan_job_id=first_job.id)
            second_job = run_full_scan(settings, source.id, scan_mode="incremental")
            second_summary = generate_rename_previews(settings, scan_job_id=second_job.id)

            self.assertEqual(1, first_summary.generated_count)
            self.assertEqual("completed", second_job.status)
            self.assertEqual(1, second_job.skipped_count)
            self.assertEqual(0, second_summary.generated_count)
            self.assertEqual(0, second_summary.needs_review_count)
            self.assertEqual([], list_rename_previews(settings, scan_job_id=second_job.id))

    def test_full_and_incremental_preview_generation_share_same_title_recognition_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            show_dir = media_dir / "Better Show"
            show_dir.mkdir(parents=True)
            episode = show_dir / "S01E02.mkv"
            episode.write_text("episode", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {"naming.title_recognition_mode": "parent_folder_first"},
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)

            first_job = run_full_scan(settings, source.id)
            generate_rename_previews(settings, scan_job_id=first_job.id)
            first_preview = list_rename_previews(settings, scan_job_id=first_job.id)[0]

            episode.write_text("episode-updated", encoding="utf-8")
            second_job = run_full_scan(settings, source.id, scan_mode="incremental")
            generate_rename_previews(settings, scan_job_id=second_job.id)
            second_preview = list_rename_previews(settings, scan_job_id=second_job.id)[0]

            self.assertEqual("parent_folder_first", first_preview.recognition_mode)
            self.assertEqual("Better Show", first_preview.parsed_title)
            self.assertEqual("parent_folder", first_preview.title_source)
            self.assertEqual("parent_folder_first", second_preview.recognition_mode)
            self.assertEqual("Better Show", second_preview.parsed_title)
            self.assertEqual("parent_folder", second_preview.title_source)

    def test_scan_mode_suggestion_prefers_incremental_after_index_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            before_scan = get_scan_mode_suggestion(settings, source.id)
            run_full_scan(settings, source.id)
            after_scan = get_scan_mode_suggestion(settings, source.id)

            self.assertEqual("full", before_scan.recommended_mode)
            self.assertFalse(before_scan.has_index)
            self.assertEqual("incremental", after_scan.recommended_mode)
            self.assertTrue(after_scan.has_index)
            self.assertEqual(1, after_scan.indexed_count)

    def test_incremental_scan_detects_new_changed_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            changed_file = media_dir / "changed.mkv"
            missing_file = media_dir / "missing.mkv"
            changed_file.write_text("old", encoding="utf-8")
            missing_file.write_text("missing", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            run_full_scan(settings, source.id)
            changed_file.write_text("changed content", encoding="utf-8")
            missing_file.unlink()
            (media_dir / "new.mkv").write_text("new", encoding="utf-8")

            job = run_full_scan(settings, source.id, scan_mode="incremental")
            files = list_media_files(settings, scan_job_id=job.id)

            self.assertEqual("completed", job.status)
            self.assertEqual(2, job.video_count)
            self.assertEqual(1, job.new_count)
            self.assertEqual(1, job.changed_count)
            self.assertEqual(0, job.skipped_count)
            self.assertEqual(1, job.missing_count)
            self.assertEqual(2, job.indexed_count)
            self.assertEqual({"changed.mkv", "new.mkv"}, {file.file_name for file in files})

    def test_narrower_scan_scope_does_not_mark_excluded_history_missing(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            (media / "season").mkdir(parents=True)
            (media / "season" / "nested.mkv").write_bytes(b"media")
            (media / ".hidden.mkv").write_bytes(b"media")
            (media / "visible.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "scope", media, True)
            update_setting_values(settings, {"scan.skip_hidden_files": False})
            run_full_scan(settings, source.id)
            update_setting_values(settings, {"scan.skip_hidden_files": True, "scan.recursive": False})
            job = run_full_scan(settings, source.id, scan_mode="incremental")
            self.assertEqual(0, job.missing_count)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                statuses = dict(connection.execute("SELECT normalized_path, status FROM scan_file_index WHERE media_source_id=?", (source.id,)))
            self.assertEqual("active", statuses["season/nested.mkv"])
            self.assertEqual("active", statuses[".hidden.mkv"])

    def test_read_error_does_not_mark_previous_file_missing(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            (media / "movie.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "partial", media, True)
            run_full_scan(settings, source.id)
            with patch("app.service.scan_service._stat_with_nfs_retry", side_effect=TimeoutError("synthetic timeout")):
                job = run_full_scan(settings, source.id, scan_mode="incremental")
            self.assertEqual("partial_completed", job.status)
            self.assertEqual(0, job.missing_count)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                status = connection.execute("SELECT status FROM scan_file_index WHERE media_source_id=?", (source.id,)).fetchone()[0]
            self.assertEqual("active", status)

    def test_subdirectory_enumeration_error_does_not_publish_missing_indexes(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            (media / "season").mkdir(parents=True)
            (media / "season" / "movie.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "scope error", media, True)
            run_full_scan(settings, source.id)

            def unreadableWalk(path, *, onerror, followlinks):
                onerror(PermissionError(13, "synthetic denied"))
                return iter(())

            with patch("app.service.scan_service.os.walk", side_effect=unreadableWalk):
                with self.assertRaises(PermissionError):
                    run_full_scan(settings, source.id, scan_mode="incremental")
            with closing(sqlite3.connect(settings.database_path)) as connection:
                status = connection.execute("SELECT status FROM scan_file_index WHERE media_source_id=?", (source.id,)).fetchone()[0]
            self.assertEqual("active", status)
            self.assertEqual("failed", max(list_scan_jobs(settings), key=lambda job: job.id).status)

    def test_incremental_scan_rechecks_both_directions_of_size_policy_change(self):
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            (media / "movie.mkv").write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "policy", media, True)
            update_setting_values(settings, {"scan.minimum_file_size": 100})
            first = run_full_scan(settings, source.id)
            self.assertEqual(0, first.video_count)
            update_setting_values(settings, {"scan.minimum_file_size": 0})
            second = run_full_scan(settings, source.id, scan_mode="incremental")
            with closing(sqlite3.connect(settings.database_path)) as connection:
                firstPolicy = json.loads(connection.execute("SELECT policy_json FROM scan_jobs WHERE id=?", (first.id,)).fetchone()[0])
                secondPolicy = json.loads(connection.execute("SELECT policy_json FROM scan_jobs WHERE id=?", (second.id,)).fetchone()[0])
            self.assertEqual(100, firstPolicy["minimum_file_size"])
            self.assertEqual(0, secondPolicy["minimum_file_size"])
            self.assertEqual(1, secondPolicy["version"])
            self.assertEqual([], list_pending_files(settings))
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual("removed", connection.execute("SELECT status FROM pending_files WHERE scan_job_id=?", (first.id,)).fetchone()[0])
            self.assertEqual(1, second.video_count)
            self.assertEqual(1, second.changed_count)
            self.assertEqual(0, second.skipped_count)
            update_setting_values(settings, {"scan.minimum_file_size": 100})
            third = run_full_scan(settings, source.id, scan_mode="incremental")
            self.assertEqual(0, third.video_count)
            self.assertEqual(1, third.changed_count)
            self.assertEqual(0, third.skipped_count)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual("ignored", connection.execute("SELECT status FROM scan_file_index WHERE media_source_id=?", (source.id,)).fetchone()[0])

    def test_reclassification_preserves_inflight_pending_move(self):
        from app.service.local_file_action_service import prepareLocalFileAction
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            movie = media / "movie.mkv"
            movie.write_bytes(b"media")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "inflight", media, True)
            update_setting_values(settings, {"scan.minimum_file_size": 100})
            run_full_scan(settings, source.id)
            pending = list_pending_files(settings)[0]
            prepareLocalFileAction(settings, mediaSourceId=source.id, actionType="pending_move", recordId=pending.id,
                                   sourcePath=str(movie), targetPath=str(root / "held.mkv"))
            update_setting_values(settings, {"scan.minimum_file_size": 0})
            run_full_scan(settings, source.id, scan_mode="incremental")
            self.assertEqual("moving", list_pending_files(settings)[0].status)
            self.assertTrue(movie.exists())
            self.assertFalse((root / "held.mkv").exists())

    def test_run_full_scan_uses_hot_scan_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "visible.mkv").write_text("visible", encoding="utf-8")
            (media_dir / ".hidden.mkv").write_text("hidden", encoding="utf-8")
            nested = media_dir / "season"
            nested.mkdir()
            (nested / "nested.mkv").write_text("nested", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "scan.batch_size": 5,
                    "scan.batch_interval_seconds": 0,
                    "scan.skip_hidden_files": True,
                    "scan.recursive": False,
                },
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)

            job = run_full_scan(settings, source.id)
            files = list_media_files(settings)

            self.assertEqual(5, job.batch_size)
            self.assertEqual(0, job.batch_interval_seconds)
            self.assertEqual(1, job.video_count)
            self.assertEqual(["visible.mkv"], [file.file_name for file in files])

    def test_run_full_scan_passes_nfs_operation_timeout_to_stat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            media_file = media_dir / "movie.mkv"
            media_file.write_text("visible", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {"shared.nfs_operation_timeout_seconds": 12, "scan.batch_interval_seconds": 0},
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)
            captured_timeouts: list[int] = []

            def fake_stat(file_path: Path, retry_count: int, timeout_seconds: int) -> object:
                captured_timeouts.append(timeout_seconds)
                return file_path.stat()

            with patch("app.service.scan_service._stat_with_nfs_retry", side_effect=fake_stat):
                job = run_full_scan(settings, source.id)

            self.assertEqual("completed", job.status)
            self.assertEqual([12], captured_timeouts)

    def test_run_full_scan_marks_partial_completed_when_files_are_filtered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "tiny.mkv").write_text("tiny", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {"scan.minimum_file_size": 1024, "scan.batch_interval_seconds": 0},
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)

            job = run_full_scan(settings, source.id)

            self.assertEqual("partial_completed", job.status)
            self.assertEqual(1, job.warning_count)
            self.assertEqual(0, job.video_count)

    def test_list_scan_jobs_and_media_files_support_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "first.mkv").write_text("first", encoding="utf-8")
            (second_dir / "second.mkv").write_text("second", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            first_source = create_media_source(settings, "first", first_dir, True)
            second_source = create_media_source(settings, "second", second_dir, True)
            first_job = run_full_scan(settings, first_source.id)
            second_job = run_full_scan(settings, second_source.id)

            first_source_jobs = list_scan_jobs(settings, media_source_id=first_source.id)
            second_job_files = list_media_files(settings, scan_job_id=second_job.id)
            first_source_files = list_media_files(settings, media_source_id=first_source.id)

            self.assertEqual([first_job.id], [job.id for job in first_source_jobs])
            self.assertEqual(["second.mkv"], [file.file_name for file in second_job_files])
            self.assertEqual(["first.mkv"], [file.file_name for file in first_source_files])

    def test_run_full_scan_rejects_disabled_media_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "disabled", media_dir, False)

            with self.assertRaises(ValueError):
                run_full_scan(settings, source.id)

            self.assertEqual([], list_scan_jobs(settings))

    def test_run_full_scan_rejects_unavailable_source_before_creating_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_dir = root / "missing"
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "missing", missing_dir, True)

            with self.assertRaises(ValueError):
                run_full_scan(settings, source.id)

            self.assertEqual([], list_scan_jobs(settings))

    def test_run_full_scan_marks_partial_completed_for_file_level_nfs_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "nfs", media_dir, True, path_type="mounted_nfs")

            with patch("app.service.scan_service._stat_with_nfs_retry", side_effect=TimeoutError("timed out")):
                job = run_full_scan(settings, source.id)

            jobs = list_scan_jobs(settings)
            self.assertEqual("partial_completed", job.status)
            self.assertEqual("partial_completed", jobs[0].status)
            self.assertEqual(1, jobs[0].warning_count)
            self.assertEqual(0, jobs[0].video_count)
            self.assertTrue(jobs[0].error_message)

    def test_incremental_scan_failure_keeps_historical_indexes_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            first = media_dir / "first.mkv"
            second = media_dir / "second.mkv"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)
            first_job = run_full_scan(settings, source.id)
            first.write_text("first-updated", encoding="utf-8")

            original_upsert = _upsert_scan_file_index
            call_count = {"value": 0}

            def flaky_upsert(*args, **kwargs):
                call_count["value"] += 1
                if call_count["value"] == 2:
                    raise RuntimeError("simulated incremental failure")
                return original_upsert(*args, **kwargs)

            with self.assertRaises(RuntimeError):
                with patch("app.service.scan_service._upsert_scan_file_index", side_effect=flaky_upsert):
                    run_full_scan(settings, source.id, scan_mode="incremental")

            jobs = list_scan_jobs(settings, media_source_id=source.id)
            latest_job = jobs[-1]
            self.assertEqual("failed", latest_job.status)
            self.assertEqual("扫描任务失败：simulated incremental failure", latest_job.error_message)
            self.assertEqual([], list_media_files(settings, scan_job_id=latest_job.id))

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT normalized_path, last_scan_job_id, status FROM scan_file_index WHERE media_source_id = ? ORDER BY normalized_path",
                    (source.id,),
                ).fetchall()

            self.assertEqual(2, len(rows))
            self.assertTrue(all(int(row["last_scan_job_id"]) == first_job.id for row in rows))
            self.assertTrue(all(str(row["status"]) == "active" for row in rows))

    def test_incremental_scan_reclassifies_historical_small_file_after_threshold_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            tiny_file = media_dir / "tiny.mkv"
            tiny_file.write_text("tiny", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            first_job = run_full_scan(settings, source.id)
            self.assertEqual(1, first_job.video_count)

            update_setting_values(
                settings,
                {"scan.minimum_file_size": 1024, "scan.batch_interval_seconds": 0},
                operator="test",
            )
            second_job = run_full_scan(settings, source.id, scan_mode="incremental")

            self.assertEqual("partial_completed", second_job.status)
            self.assertEqual(0, second_job.skipped_count)
            self.assertEqual(1, second_job.warning_count)
            self.assertEqual(0, second_job.video_count)
            self.assertEqual(1, len(list_pending_files(settings, scan_job_id=second_job.id)))

    def test_webdav_scan_persists_video_files_and_uses_etag_for_incremental_skip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings,
                "webdav",
                "https://nas.example/dav/",
                True,
                path_type="webdav",
                username="user",
                secret="password",
            )
            update_setting_values(
                settings,
                {"scan.batch_interval_seconds": 0, "scan.minimum_file_size": 1024},
                operator="test",
            )

            class FakeResponse:
                status = 207

                def __init__(self, body: str = ""):
                    self.body = body.encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def read(self, size=-1):
                    return self.body if size < 0 else self.body[:size]

            root_xml = """<?xml version="1.0" encoding="utf-8"?>
            <d:multistatus xmlns:d="DAV:">
              <d:response>
                <d:href>/dav/</d:href>
                <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
              </d:response>
              <d:response>
                <d:href>/dav/Show/</d:href>
                <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
              </d:response>
              <d:response>
                <d:href>/dav/poster.jpg</d:href>
                <d:propstat><d:prop>
                  <d:resourcetype />
                  <d:getcontentlength>2048</d:getcontentlength>
                  <d:getlastmodified>Tue, 14 Jul 2026 01:02:03 GMT</d:getlastmodified>
                  <d:getetag>"poster-etag"</d:getetag>
                </d:prop></d:propstat>
              </d:response>
            </d:multistatus>
            """
            show_xml = """<?xml version="1.0" encoding="utf-8"?>
            <d:multistatus xmlns:d="DAV:">
              <d:response>
                <d:href>/dav/Show/</d:href>
                <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
              </d:response>
              <d:response>
                <d:href>/dav/Show/S01E01.mkv</d:href>
                <d:propstat><d:prop>
                  <d:resourcetype />
                  <d:getcontentlength>4096</d:getcontentlength>
                  <d:getlastmodified>Tue, 14 Jul 2026 02:03:04 GMT</d:getlastmodified>
                  <d:getetag>"episode-etag"</d:getetag>
                </d:prop></d:propstat>
              </d:response>
              <d:response>
                <d:href>/dav/Show/tiny.mp4</d:href>
                <d:propstat><d:prop>
                  <d:resourcetype />
                  <d:getcontentlength>10</d:getcontentlength>
                  <d:getlastmodified>Tue, 14 Jul 2026 02:03:05 GMT</d:getlastmodified>
                  <d:getetag>"tiny-etag"</d:getetag>
                </d:prop></d:propstat>
              </d:response>
            </d:multistatus>
            """

            def fake_urlopen(request, timeout=5):
                if request.method == "OPTIONS":
                    return FakeResponse()
                if request.full_url.endswith("/Show/"):
                    return FakeResponse(show_xml)
                return FakeResponse(root_xml)

            with patch("app.service.shared_protocols.webdav.urlopen", side_effect=fake_urlopen):
                first_job = run_full_scan(settings, source.id)
                second_job = run_full_scan(settings, source.id, scan_mode="incremental")

            first_files = list_media_files(settings, scan_job_id=first_job.id)
            second_files = list_media_files(settings, scan_job_id=second_job.id)
            pending_files = list_pending_files(settings, scan_job_id=first_job.id)

            self.assertEqual("partial_completed", first_job.status)
            self.assertEqual(3, first_job.scanned_count)
            self.assertEqual(1, first_job.video_count)
            self.assertEqual(1, first_job.warning_count)
            self.assertEqual(["S01E01.mkv"], [file.file_name for file in first_files])
            self.assertEqual(["tiny.mp4"], [file.file_name for file in pending_files])
            self.assertEqual("completed", second_job.status)
            self.assertEqual(0, second_job.video_count)
            self.assertEqual(2, second_job.skipped_count)
            self.assertEqual([], second_files)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                originalIndexes = connection.execute("SELECT * FROM scan_file_index ORDER BY id").fetchall()

            def incompleteChildListing(request, timeout=5):
                if request.full_url.endswith("/Show/"):
                    return FakeResponse('<d:multistatus xmlns:d="DAV:"/>')
                return FakeResponse(root_xml)

            with patch("app.service.shared_protocols.webdav.urlopen", side_effect=incompleteChildListing):
                with self.assertRaisesRegex(ValueError, "响应不完整"):
                    run_full_scan(settings, source.id, scan_mode="incremental")
            latestJob = max(list_scan_jobs(settings), key=lambda job: job.id)
            self.assertEqual("failed", latestJob.status)
            self.assertEqual([], list_media_files(settings, scan_job_id=latestJob.id))
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(originalIndexes, connection.execute("SELECT * FROM scan_file_index ORDER BY id").fetchall())

    def test_recover_interrupted_scan_jobs_marks_running_jobs_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = AppSettings(
                data_dir=root,
                database_path=root / "mediaai.sqlite3",
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute(
                    "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                    "VALUES (1, 'test', ?, 1, 'now', 'now')",
                    (str(root),),
                )
                connection.execute(
                    "INSERT INTO scan_jobs "
                    "(id, media_source_id, status, batch_size, batch_interval_seconds, started_at, created_at) "
                    "VALUES (1, 1, 'running', 100, 0, 'now', 'now')"
                )
                connection.commit()

            recovered_count = recover_interrupted_scan_jobs(settings)
            jobs = list_scan_jobs(settings)

            self.assertEqual(1, recovered_count)
            self.assertEqual("failed", jobs[0].status)
            self.assertIsNotNone(jobs[0].ended_at)
            self.assertTrue(jobs[0].error_message)


if __name__ == "__main__":
    unittest.main()
