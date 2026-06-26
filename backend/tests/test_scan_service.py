"""扫描服务测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source
from app.service.scan_service import list_media_files, list_scan_jobs, run_full_scan


class ScanServiceTest(unittest.TestCase):
    """全量扫描和分批扫描测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        """构建隔离的测试配置。"""

        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=2, batch_interval_seconds=0),
        )

    def test_run_full_scan_persists_only_video_files_with_batch_config(self):
        """全量扫描应按批配置执行，并只保存视频文件。"""

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
            source = create_media_source(settings, "测试", media_dir, True)

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


if __name__ == "__main__":
    unittest.main()
