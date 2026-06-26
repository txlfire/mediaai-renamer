"""Pending file operation tests."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.media_source_service import create_media_source
from app.service.pending_file_service import clear_pending_files, list_pending_files, remove_pending_file
from app.service.scan_service import list_media_files, run_full_scan
from app.service.settings_service import update_setting_values


class PendingFileTest(unittest.TestCase):
    """Small file filtering and pending queue behavior."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=10, batch_interval_seconds=0),
        )

    def test_small_video_files_are_routed_to_pending_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "small.mkv").write_text("x", encoding="utf-8")
            (media_dir / "large.mkv").write_text("x" * 20, encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)

            job = run_full_scan(settings, source.id)

            self.assertEqual(1, job.video_count)
            self.assertEqual(1, job.warning_count)
            self.assertEqual(["large.mkv"], [file.file_name for file in list_media_files(settings)])
            pending = list_pending_files(settings, scan_job_id=job.id)
            self.assertEqual(1, len(pending))
            self.assertEqual("small.mkv", pending[0].file_name)
            self.assertEqual("size_filtered", pending[0].reason)

    def test_pending_files_can_be_removed_and_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("x", encoding="utf-8")
            (media_dir / "b.mkv").write_text("x", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)
            job = run_full_scan(settings, source.id)

            first = list_pending_files(settings, scan_job_id=job.id)[0]
            remove_pending_file(settings, first.id)
            remaining = list_pending_files(settings, scan_job_id=job.id)
            self.assertEqual(1, len(remaining))

            removed_count = clear_pending_files(settings, scan_job_id=job.id)

            self.assertEqual(1, removed_count)
            self.assertEqual([], list_pending_files(settings, scan_job_id=job.id))

    def test_pending_file_api_lists_removes_and_clears_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("x", encoding="utf-8")
            (media_dir / "b.mkv").write_text("x", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)
            job = run_full_scan(settings, source.id)
            client = TestClient(create_app(settings))

            try:
                response = client.get(f"/api/pending-files?scan_job_id={job.id}")
                self.assertEqual(200, response.status_code)
                self.assertEqual(2, len(response.json()))

                pending_id = response.json()[0]["id"]
                remove_response = client.delete(f"/api/pending-files/{pending_id}")
                self.assertEqual(200, remove_response.status_code)

                clear_response = client.post(f"/api/pending-files/clear?scan_job_id={job.id}")
                self.assertEqual(200, clear_response.status_code)
                self.assertEqual(1, clear_response.json()["removed_count"])
            finally:
                shutdown_logging()


if __name__ == "__main__":
    unittest.main()
