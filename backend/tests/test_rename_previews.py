"""M2 rename preview service and API tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.preview_service import (
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
)


class RenamePreviewTestCase(unittest.TestCase):
    """Shared test fixture for rename previews."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.media_file = self.media_dir / "The.Matrix.1999.1080p.mkv"
        self.media_file.write_text("sample", encoding="utf-8")
        self.episode_file = self.media_dir / "Show.Name.S02E03.2160p.WEB-DL.mp4"
        self.episode_file.write_text("episode", encoding="utf-8")
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            scan=ScanSettings(batch_size=100, batch_interval_seconds=0),
        )
        ensure_database(self.settings)
        self._insert_media_files()

    def tearDown(self):
        shutdown_logging()
        self.temp_dir.cleanup()

    def _insert_media_files(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                "VALUES (1, 'test', ?, 1, 'now', 'now')",
                (str(self.media_dir),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (?, 1, 1, ?, ?, ?, ?, 'now', 'now')",
                (
                    1,
                    str(self.media_file),
                    self.media_file.name,
                    ".mkv",
                    self.media_file.stat().st_size,
                ),
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (?, 1, 1, ?, ?, ?, ?, 'now', 'now')",
                (
                    2,
                    str(self.episode_file),
                    self.episode_file.name,
                    ".mp4",
                    self.episode_file.stat().st_size,
                ),
            )
            connection.commit()


class RenamePreviewServiceTest(RenamePreviewTestCase):
    """Rename preview service behavior."""

    def test_generate_preview_does_not_modify_real_file(self):
        summary = generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)

        self.assertEqual(2, summary.generated_count)
        self.assertEqual(0, summary.needs_review_count)
        self.assertEqual(
            {"The.Matrix.1999.mkv", "Show.Name.S02E03.mp4"},
            {preview.suggested_name for preview in previews},
        )
        self.assertTrue(self.media_file.exists())
        self.assertEqual("The.Matrix.1999.1080p.mkv", self.media_file.name)

    def test_update_preview_sets_edited_name_and_keeps_extension(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]

        updated = update_rename_preview(self.settings, preview.id, "Matrix.Custom")

        self.assertEqual("Matrix.Custom.mkv", updated.edited_name)
        self.assertEqual("Matrix.Custom.mkv", updated.current_target_name)
        self.assertEqual("edited", updated.status)
        self.assertEqual("The.Matrix.1999.1080p.mkv", self.media_file.name)

    def test_filters_by_media_type_and_keyword(self):
        generate_rename_previews(self.settings)

        previews = list_rename_previews(
            self.settings,
            media_type="episode",
            keyword="Show",
        )

        self.assertEqual(1, len(previews))
        self.assertEqual("Show.Name.S02E03.mp4", previews[0].current_target_name)


class RenamePreviewApiTest(RenamePreviewTestCase):
    """Rename preview HTTP API behavior."""

    def test_generate_and_list_preview_api(self):
        app = create_app(self.settings)
        client = TestClient(app)

        response = client.post("/api/rename-previews/generate", json={})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()["generated_count"])

        list_response = client.get("/api/rename-previews")
        self.assertEqual(200, list_response.status_code)
        self.assertEqual(2, len(list_response.json()))
        self.assertIn("current_target_name", list_response.json()[0])

    def test_update_preview_api(self):
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        preview_id = client.get("/api/rename-previews").json()[0]["id"]

        response = client.put(
            f"/api/rename-previews/{preview_id}",
            json={"target_name": "Matrix.Custom"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("Matrix.Custom.mkv", response.json()["current_target_name"])


if __name__ == "__main__":
    unittest.main()
