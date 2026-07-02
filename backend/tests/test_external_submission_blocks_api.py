"""External submission block record API tests."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.main import create_app
from app.service.external_submission_guard import check_external_submission


class ExternalSubmissionBlocksApiTest(unittest.TestCase):
    """HTTP API behavior for external submission block records."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def seed_block_record(self, settings: AppSettings):
        result = check_external_submission(
            settings,
            source_module="rename_preview",
            source_record_id=12,
            file_name="暴力电影.mp4",
            file_path="/media/暴力电影.mp4",
            match_title="暴力电影",
            target_service="tmdb",
        )
        self.assertIsNotNone(result.block_record)
        return result.block_record

    def test_lists_external_submission_blocks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            self.seed_block_record(settings)
            client = TestClient(create_app(settings))

            response = client.get("/api/external-submission-blocks?status=blocked")

            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual(1, data["total"])
            self.assertEqual("rename_preview", data["items"][0]["source_module"])
            self.assertEqual("blocked", data["items"][0]["status"])

    def test_updates_external_submission_block_decision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            record = self.seed_block_record(settings)
            client = TestClient(create_app(settings))

            response = client.patch(
                f"/api/external-submission-blocks/{record.id}",
                json={"status": "ignored", "user_decision": "不进行匹配"},
            )

            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual("ignored", data["status"])
            self.assertEqual("不进行匹配", data["user_decision"])
            self.assertEqual("admin", data["operator"])
            self.assertIsNotNone(data["decided_at"])

    def test_override_submission_requires_reason(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            record = self.seed_block_record(settings)
            client = TestClient(create_app(settings))

            response = client.patch(
                f"/api/external-submission-blocks/{record.id}",
                json={"status": "override_submitted"},
            )

            self.assertEqual(400, response.status_code)
            self.assertIn("风险确认原因", response.json()["detail"])

    def test_missing_block_returns_404(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            client = TestClient(create_app(settings))

            response = client.patch(
                "/api/external-submission-blocks/999",
                json={"status": "ignored"},
            )

            self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
