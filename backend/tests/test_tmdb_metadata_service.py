"""TMDB metadata service tests."""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.metadata_service import match_metadata_candidates
from app.service.settings_service import update_setting_values


class FakeMetadataProvider:
    def __init__(self, candidates: list[MetadataCandidate]):
        self.candidates = candidates
        self.called = False

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        self.called = True
        return self.candidates


class TmdbMetadataServiceTest(unittest.TestCase):
    """Metadata service downgrade and scoring behavior."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_disabled_tmdb_downgrades_without_calling_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            provider = FakeMetadataProvider([])

            result = match_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                provider=provider,
            )

            self.assertFalse(provider.called)
            self.assertEqual("failed", result.status)
            self.assertEqual("TMDB 未启用", result.message)
            self.assertEqual([], result.matches)

    def test_missing_api_key_downgrades_without_calling_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(settings, {"tmdb.enabled": "true"}, operator="admin")
            provider = FakeMetadataProvider([])

            result = match_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                provider=provider,
            )

            self.assertFalse(provider.called)
            self.assertEqual("failed", result.status)
            self.assertEqual("未配置 TMDB V4 令牌且无 V3 API 密钥", result.message)

    def test_candidates_are_scored_and_sorted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {"tmdb.enabled": "true", "tmdb.api_key": "abcdef123456"},
                operator="admin",
            )
            provider = FakeMetadataProvider(
                [
                    MetadataCandidate("TMDB", "bad", "movie", "Other Movie", "", 2022, None, None, ""),
                    MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, ""),
                ]
            )

            result = match_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                provider=provider,
            )

            self.assertTrue(provider.called)
            self.assertEqual("high_confidence", result.status)
            self.assertEqual(2, len(result.matches))
            self.assertEqual("603", result.matches[0].candidate.provider_id)
            self.assertEqual(100, result.matches[0].score)


if __name__ == "__main__":
    unittest.main()
