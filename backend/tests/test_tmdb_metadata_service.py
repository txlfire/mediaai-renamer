"""TMDB metadata service tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class FakeTmdbClient:
    calls: list[dict[str, str]] = []

    def __init__(
        self,
        api_key: str = "",
        v4_token: str = "",
        language: str = "zh-CN",
        region: str = "CN",
        timeout_ms: int = 10000,
    ):
        self.api_key = api_key
        self.v4_token = v4_token
        FakeTmdbClient.calls.append({"api_key": api_key, "v4_token": v4_token})

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        if self.v4_token == "bad-v4-token":
            raise RuntimeError("V4 token rejected")
        if self.api_key == "bad-v3-key":
            raise RuntimeError("V3 key rejected")
        return [
            MetadataCandidate(
                "TMDB",
                "603",
                "movie",
                "The Matrix",
                "",
                1999,
                None,
                None,
                "",
            )
        ]


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

    def test_enabled_imdb_supplement_searches_tmdb_first_and_applies_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "tmdb.enabled": "true",
                    "tmdb.api_key": "abcdef123456",
                    "imdb.enabled": "true",
                    "imdb.priority": "imdb_first",
                },
                operator="admin",
            )
            tmdb_provider = FakeMetadataProvider(
                [MetadataCandidate("TMDB", "603", "movie", "The Matrix", "", 1999, None, None, "tmdb")]
            )
            imdb_provider = FakeMetadataProvider(
                [MetadataCandidate("IMDb", "tt0133093", "movie", "The Matrix", "", 1999, None, None, "imdb")]
            )

            result = match_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                provider=tmdb_provider,
                imdb_provider=imdb_provider,
            )

            self.assertTrue(tmdb_provider.called)
            self.assertTrue(imdb_provider.called)
            self.assertEqual("TMDB + IMDb", result.metadata_source)
            self.assertEqual("IMDb", result.matches[0].candidate.provider)

    def test_v4_token_takes_priority_over_v3_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "tmdb.enabled": "true",
                    "tmdb.v4_token": "good-v4-token",
                    "tmdb.api_key": "good-v3-key",
                },
                operator="admin",
            )
            FakeTmdbClient.calls = []

            with patch("app.service.metadata_service.TmdbClient", FakeTmdbClient):
                result = match_metadata_candidates(
                    settings,
                    ParsedMediaName("movie", "The Matrix", 1999, None, None),
                )

            self.assertEqual("TMDB (V4)", result.metadata_source)
            self.assertEqual("high_confidence", result.status)
            self.assertEqual([{"api_key": "", "v4_token": "good-v4-token"}], FakeTmdbClient.calls)

    def test_v4_failure_falls_back_to_v3_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "tmdb.enabled": "true",
                    "tmdb.v4_token": "bad-v4-token",
                    "tmdb.api_key": "good-v3-key",
                },
                operator="admin",
            )
            FakeTmdbClient.calls = []

            with patch("app.service.metadata_service.TmdbClient", FakeTmdbClient):
                result = match_metadata_candidates(
                    settings,
                    ParsedMediaName("movie", "The Matrix", 1999, None, None),
                )

            self.assertEqual("TMDB (V3)", result.metadata_source)
            self.assertEqual("high_confidence", result.status)
            self.assertEqual(
                [
                    {"api_key": "", "v4_token": "bad-v4-token"},
                    {"api_key": "good-v3-key", "v4_token": ""},
                ],
                FakeTmdbClient.calls,
            )

    def test_v4_and_v3_failures_return_failed_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "tmdb.enabled": "true",
                    "tmdb.v4_token": "bad-v4-token",
                    "tmdb.api_key": "bad-v3-key",
                },
                operator="admin",
            )

            with patch("app.service.metadata_service.TmdbClient", FakeTmdbClient):
                result = match_metadata_candidates(
                    settings,
                    ParsedMediaName("movie", "The Matrix", 1999, None, None),
                )

            self.assertEqual("failed", result.status)
            self.assertEqual([], result.matches)


if __name__ == "__main__":
    unittest.main()
