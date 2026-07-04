"""M2 rename preview service and API tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.preview_service import (
    exclude_rename_preview,
    exclude_rename_previews,
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
)
from app.service.pending_file_service import list_pending_files
from app.service.settings_service import update_setting_values


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

    def test_generate_preview_uses_latest_database_naming_settings(self):
        update_setting_values(self.settings, {"naming.keep_year": False}, operator="admin")

        generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)

        self.assertIn("The.Matrix.mkv", {preview.suggested_name for preview in previews})

    def test_generate_preview_marks_same_target_name_as_no_rename(self):
        same_name_file = self.media_dir / "Plain.Movie.2024.mkv"
        same_name_file.write_text("same", encoding="utf-8")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (?, 1, 1, ?, ?, ?, ?, 'now', 'now')",
                (
                    3,
                    str(same_name_file),
                    same_name_file.name,
                    ".mkv",
                    same_name_file.stat().st_size,
                ),
            )
            connection.commit()

        generate_rename_previews(self.settings, media_file_ids=[3])
        preview = list_rename_previews(self.settings, keyword="Plain")[0]

        self.assertEqual("Plain.Movie.2024.mkv", preview.current_target_name)
        self.assertEqual("no_rename", preview.status)

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

    def test_generate_preview_can_use_parent_folder_title_for_episode_file(self):
        show_dir = self.media_dir / "Better Show"
        show_dir.mkdir()
        episode = show_dir / "S01E02.mp4"
        episode.write_text("episode", encoding="utf-8")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (?, 1, 1, ?, ?, ?, ?, 'now', 'now')",
                (
                    3,
                    str(episode),
                    episode.name,
                    ".mp4",
                    episode.stat().st_size,
                ),
            )
            connection.commit()

        generate_rename_previews(self.settings, media_file_ids=[3])
        preview = list_rename_previews(self.settings, keyword="Better")[0]

        self.assertEqual("episode", preview.media_type)
        self.assertEqual("Better Show", preview.parsed_title)
        self.assertEqual(1, preview.season)
        self.assertEqual(2, preview.episode)
        self.assertEqual("parent_folder", preview.title_source)
        self.assertEqual("Better Show", preview.parent_folder_title)

    def test_preview_can_be_manually_excluded_to_pending_list(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]

        excluded = exclude_rename_preview(self.settings, preview.id)

        self.assertEqual("excluded", excluded.status)
        self.assertEqual(1, len(list_pending_files(self.settings)))
        self.assertNotIn(preview.id, {item.id for item in list_rename_previews(self.settings)})
        self.assertEqual(preview.id, list_rename_previews(self.settings, status="excluded")[0].id)

    def test_previews_can_be_manually_excluded_in_batch(self):
        generate_rename_previews(self.settings)
        preview_ids = [preview.id for preview in list_rename_previews(self.settings)]

        result = exclude_rename_previews(self.settings, preview_ids)

        self.assertEqual(2, result["success_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertEqual(2, len(list_pending_files(self.settings)))
        self.assertEqual(2, len(list_rename_previews(self.settings, status="excluded")))


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
        self.assertIn("metadata_match_score", list_response.json()[0])
        self.assertIn("metadata_candidate_count", list_response.json()[0])

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

    def test_manual_metadata_candidate_api(self):
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        preview_id = client.get("/api/rename-previews").json()[0]["id"]

        response = client.post(
            f"/api/rename-previews/{preview_id}/metadata-candidate",
            json={
                "score": 91,
                "candidate": {
                    "provider": "TMDB",
                    "provider_id": "603",
                    "media_type": "movie",
                    "title": "黑客帝国",
                    "original_title": "The Matrix",
                    "year": 1999,
                    "season": None,
                    "episode": None,
                    "overview": "",
                },
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("generated", response.json()["status"])
        self.assertEqual("TMDB", response.json()["metadata_source"])
        self.assertEqual(91, response.json()["metadata_match_score"])

    def test_ai_parse_preview_api_returns_structured_candidates(self):
        update_setting_values(
            self.settings,
            {
                "ai.enabled": "true",
                "ai.provider": "deepseek",
                "ai.model": "deepseek-chat",
                "ai.api_key": "sk-secret123456",
                "ai.base_url": "https://api.deepseek.com/v1",
            },
            operator="admin",
        )
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        preview_id = client.get("/api/rename-previews").json()[0]["id"]

        class FakeDeepSeekProvider:
            def __init__(self, config):
                self.config = config

            def complete_chat(self, messages, max_tokens: int, temperature: float):
                return {
                    "status": "success",
                    "content": (
                        '{"title":"黑客帝国","media_type":"movie","year":1999,'
                        '"season":null,"episode":null,"confidence":92,"reason":"标题和年份匹配"}'
                    ),
                    "response_ms": 15,
                    "usage": {"total_tokens": 60},
                }

        with patch("app.service.ai_parse_service.DeepSeekProvider", FakeDeepSeekProvider):
            response = client.post(f"/api/rename-previews/{preview_id}/ai-parse")

        payload = response.json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("success", payload["status"])
        self.assertEqual("黑客帝国", payload["candidates"][0]["title"])
        self.assertEqual(92, payload["candidates"][0]["confidence"])
        self.assertNotIn("sk-secret123456", str(payload))

    def test_batch_ai_parse_preview_api_returns_summary(self):
        update_setting_values(
            self.settings,
            {
                "ai.enabled": "true",
                "ai.provider": "deepseek",
                "ai.model": "deepseek-chat",
                "ai.api_key": "sk-secret123456",
                "ai.base_url": "https://api.deepseek.com/v1",
            },
            operator="admin",
        )
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        previews = client.get("/api/rename-previews").json()
        preview_ids = [item["id"] for item in previews[:2]]

        class FakeDeepSeekProvider:
            def __init__(self, config):
                self.config = config

            def complete_chat(self, messages, max_tokens: int, temperature: float):
                return {
                    "status": "success",
                    "content": (
                        '{"title":"黑客帝国","media_type":"movie","year":1999,'
                        '"season":null,"episode":null,"confidence":92,"reason":"标题和年份匹配"}'
                    ),
                    "response_ms": 15,
                    "usage": {"total_tokens": 60, "prompt_tokens": 40},
                }

        with patch("app.service.ai_parse_service.DeepSeekProvider", FakeDeepSeekProvider):
            response = client.post(
                "/api/rename-previews/ai-parse/batch",
                json={"rename_preview_ids": preview_ids},
            )

        payload = response.json()
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, payload["total_count"])
        self.assertEqual(2, payload["success_count"])
        self.assertEqual(0, payload["failed_count"])
        self.assertEqual(0, payload["blocked_count"])
        self.assertEqual(120, payload["usage"]["total_tokens"])
        self.assertEqual(80, payload["usage"]["prompt_tokens"])
        self.assertEqual("success", payload["items"][0]["result"]["status"])
        self.assertEqual("黑客帝国", payload["items"][0]["result"]["candidates"][0]["title"])
        self.assertNotIn("sk-secret123456", str(payload))

    def test_apply_ai_parse_candidate_api(self):
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        preview_id = client.get("/api/rename-previews").json()[0]["id"]

        response = client.post(
            f"/api/rename-previews/{preview_id}/ai-candidate",
            json={
                "candidate": {
                    "title": "黑客帝国",
                    "media_type": "movie",
                    "year": 1999,
                    "season": None,
                    "episode": None,
                    "confidence": 90,
                    "reason": "AI 识别到中文标题和年份",
                    "raw_data": {"source": "ai"},
                },
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("generated", response.json()["status"])
        self.assertEqual("AI", response.json()["metadata_source"])
        self.assertEqual(90, response.json()["metadata_match_score"])
        self.assertEqual("黑客帝国.1999.mkv", response.json()["current_target_name"])


if __name__ == "__main__":
    unittest.main()
