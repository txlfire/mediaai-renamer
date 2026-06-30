"""Settings page test result persistence tests."""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service import settings_service


class SettingsTestResultPersistenceTest(unittest.TestCase):
    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_tmdb_result_is_persisted_and_cleared_after_settings_save(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            original_client = settings_service.TmdbClient

            class FakeTmdbClient:
                def __init__(self, *args, **kwargs):
                    pass

                def test_connection(self):
                    return None

            settings_service.TmdbClient = FakeTmdbClient
            try:
                settings_service.update_setting_values(
                    settings,
                    {"tmdb.v4_token": "token", "tmdb.priority": 1},
                )
                result = settings_service.test_tmdb_connection(settings)
                saved = settings_service.get_page_test_result(
                    settings,
                    settings_service.TMDB_TEST_PAGE_KEY,
                )
                settings_service.update_setting_values(settings, {"tmdb.priority": 2})
                cleared = settings_service.get_page_test_result(
                    settings,
                    settings_service.TMDB_TEST_PAGE_KEY,
                )
            finally:
                settings_service.TmdbClient = original_client

            self.assertEqual("success", result["v4"]["status"])
            self.assertIsNotNone(saved)
            self.assertEqual("v4", saved.effective)
            self.assertIsNone(cleared)


if __name__ == "__main__":
    unittest.main()
