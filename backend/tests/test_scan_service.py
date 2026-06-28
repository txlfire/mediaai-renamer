import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source
from app.service.scan_service import list_media_files, list_scan_jobs, run_full_scan
from app.service.settings_service import update_setting_values


class ScanServiceTest(unittest.TestCase):
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

    def test_run_full_scan_marks_connection_lost_for_nfs_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "nfs", media_dir, True, path_type="mounted_nfs")

            with patch("app.service.scan_service._stat_with_nfs_retry", side_effect=TimeoutError("timed out")):
                with self.assertRaises(TimeoutError):
                    run_full_scan(settings, source.id)

            jobs = list_scan_jobs(settings)
            self.assertEqual("connection_lost", jobs[0].status)
            self.assertIn("timed out", jobs[0].error_message)


if __name__ == "__main__":
    unittest.main()
