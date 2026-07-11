"""M10 多源元数据匹配服务测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.metadata_multi_source_service import (
    MULTI_MATCH_MODE_FALLBACK,
    MULTI_MATCH_MODE_PARALLEL,
    match_multi_source_metadata_candidates,
)
from app.service.metadata_provider_registry import (
    PlaceholderMetadataProvider,
    ProviderRegistryItem,
)


class FakeRegisteredProvider:
    def __init__(
        self,
        provider: str,
        label: str,
        priority: int,
        candidates: list[MetadataCandidate],
    ):
        self.provider = provider
        self.label = label
        self.priority = priority
        self.candidates = candidates
        self.called = False

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        self.called = True
        return self.candidates


class FailingRegisteredProvider:
    provider = "failing"
    label = "失败源"
    priority = 20

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        raise RuntimeError("网络超时")


class MetadataMultiSourceServiceTest(unittest.TestCase):
    """多源匹配编排行为。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_fallback_stops_after_high_confidence_primary_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            primary = FakeRegisteredProvider(
                "tmdb",
                "TMDB",
                1,
                [MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, "")],
            )
            secondary = FakeRegisteredProvider(
                "bangumi",
                "Bangumi",
                30,
                [MetadataCandidate("Bangumi", "1", "movie", "The Matrix", "", 1999, None, None, "")],
            )
            registry = [
                ProviderRegistryItem("tmdb", "TMDB", 1, True, True, primary),
                ProviderRegistryItem("bangumi", "Bangumi", 30, True, True, secondary),
            ]

            result = match_multi_source_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                mode=MULTI_MATCH_MODE_FALLBACK,
                registry=registry,
            )

            self.assertTrue(primary.called)
            self.assertFalse(secondary.called)
            self.assertEqual("high_confidence", result.summary.status)
            self.assertEqual("TMDB", result.summary.metadata_source)
            self.assertEqual(1, len(result.provider_results))

    def test_parallel_isolates_failed_and_placeholder_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            primary = FakeRegisteredProvider(
                "tmdb",
                "TMDB",
                1,
                [MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, "")],
            )
            registry = [
                ProviderRegistryItem("tmdb", "TMDB", 1, True, True, primary),
                ProviderRegistryItem("bangumi", "Bangumi", 30, True, False, PlaceholderMetadataProvider("bangumi", "Bangumi", 30)),
                ProviderRegistryItem("failing", "失败源", 40, True, True, FailingRegisteredProvider()),
            ]

            result = match_multi_source_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                mode=MULTI_MATCH_MODE_PARALLEL,
                registry=registry,
            )

            self.assertEqual("high_confidence", result.summary.status)
            statuses = {item.provider: item.status for item in result.provider_results}
            self.assertEqual("success", statuses["tmdb"])
            self.assertEqual("skipped", statuses["bangumi"])
            self.assertEqual("failed", statuses["failing"])

    def test_parallel_dedupes_candidates_and_keeps_more_complete_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            primary = FakeRegisteredProvider(
                "tmdb",
                "TMDB",
                1,
                [MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, "")],
            )
            secondary = FakeRegisteredProvider(
                "bangumi",
                "Bangumi",
                30,
                [
                    MetadataCandidate(
                        "Bangumi",
                        "bgm-603",
                        "movie",
                        "The Matrix",
                        "The Matrix",
                        1999,
                        None,
                        None,
                        "完整简介",
                        genres=["科幻"],
                    )
                ],
            )
            registry = [
                ProviderRegistryItem("tmdb", "TMDB", 1, True, True, primary),
                ProviderRegistryItem("bangumi", "Bangumi", 30, True, True, secondary),
            ]

            result = match_multi_source_metadata_candidates(
                settings,
                ParsedMediaName("movie", "The Matrix", 1999, None, None),
                mode=MULTI_MATCH_MODE_PARALLEL,
                registry=registry,
            )

            self.assertEqual(1, len(result.summary.matches))
            candidate = result.summary.matches[0].candidate
            self.assertEqual("Bangumi", candidate.provider)
            self.assertEqual("movie:thematrix:1999::", candidate.raw_data["merge_key"])
            self.assertEqual(30, candidate.raw_data["source_priority"])
            self.assertGreater(candidate.raw_data["field_completeness"], 4)
            self.assertEqual("Bangumi", candidate.raw_data["field_sources"]["genres"])

    def test_bangumi_match_reason_is_exposed_in_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            reason = "Bangumi 动画条目命中，依据：中文标题、原始标题。"
            provider = FakeRegisteredProvider(
                "bangumi",
                "Bangumi",
                30,
                [
                    MetadataCandidate(
                        "Bangumi",
                        "400602",
                        "episode",
                        "葬送的芙莉莲",
                        "葬送のフリーレン",
                        2023,
                        None,
                        None,
                        "",
                        raw_data={"match_reason": reason},
                    )
                ],
            )
            registry = [
                ProviderRegistryItem("bangumi", "Bangumi", 30, True, True, provider),
            ]

            result = match_multi_source_metadata_candidates(
                settings,
                ParsedMediaName("episode", "葬送的芙莉莲", 2023, 1, 1),
                mode=MULTI_MATCH_MODE_PARALLEL,
                registry=registry,
            )

            self.assertEqual(reason, result.summary.message)


if __name__ == "__main__":
    unittest.main()
