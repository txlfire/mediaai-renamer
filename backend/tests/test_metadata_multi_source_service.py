"""M10 多源元数据匹配服务测试。"""

import tempfile
import unittest
from threading import Barrier, Event
from time import monotonic
from app.service.settings_service import update_setting_values
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.metadata_multi_source_service import (
    MULTI_MATCH_MODE_FALLBACK,
    MULTI_MATCH_MODE_PARALLEL,
    MULTI_MATCH_MODE_PRIMARY_ONLY,
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
        settings = AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )
        ensure_database(settings)
        return settings

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

    def test_parallel_actually_starts_two_sites_before_either_finishes(self):
        barrier = Barrier(2)
        class Simultaneous(FakeRegisteredProvider):
            def search(self, parsed):
                barrier.wait(timeout=2)
                return []
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            registry = [ProviderRegistryItem(name, name, 1, True, True, Simultaneous(name, name, 1, []))
                        for name in ('tmdb', 'bangumi')]
            result = match_multi_source_metadata_candidates(settings, ParsedMediaName('movie', 'synthetic', 2024, None, None),
                        mode=MULTI_MATCH_MODE_PARALLEL, registry=registry)
            self.assertEqual(['success', 'success'], [item.status for item in result.provider_results])

    def test_transport_budget_rejection_keeps_explicit_not_sent_message(self):
        from app.service.external_request_budget_service import ExternalBudgetWaitError
        class Waiting(FakeRegisteredProvider):
            def search(self, parsed):
                raise ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            registry = [ProviderRegistryItem("tmdb", "TMDB", 1, True, True, Waiting("tmdb", "TMDB", 1, []))]
            result = match_multi_source_metadata_candidates(settings, ParsedMediaName("movie", "Example", None, None, None),
                mode=MULTI_MATCH_MODE_PARALLEL, registry=registry)
            self.assertEqual("failed", result.provider_results[0].status)
            self.assertIn("未发送", result.provider_results[0].message)

    def test_timeout_keeps_site_slot_and_never_replays_still_running_request(self):
        release, ended = Event(), Event()
        class Slow(FakeRegisteredProvider):
            def search(self, parsed):
                self.called = True
                try:
                    release.wait(5)
                    return []
                finally:
                    ended.set()
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            update_setting_values(settings, {'metadata.total_timeout_seconds': '1'})
            slow = Slow('tmdb', 'TMDB', 1, [])
            registry = [ProviderRegistryItem('tmdb', 'TMDB', 1, True, True, slow)]
            parsed = ParsedMediaName('movie', 'synthetic', 2024, None, None)
            try:
                started = monotonic()
                first = match_multi_source_metadata_candidates(settings, parsed, mode=MULTI_MATCH_MODE_PARALLEL, registry=registry)
                self.assertLess(monotonic() - started, 2.5)
                self.assertEqual('timeout', first.provider_results[0].status)
                second = match_multi_source_metadata_candidates(settings, parsed, mode=MULTI_MATCH_MODE_PARALLEL, registry=registry)
                self.assertEqual('skipped', second.provider_results[0].status)
                self.assertIn('未发起', second.provider_results[0].message)
            finally:
                release.set()
                self.assertTrue(ended.wait(2))

    def test_fallback_does_not_start_later_site_after_budget_expiry(self):
        release, ended = Event(), Event()
        class Slow(FakeRegisteredProvider):
            def search(self, parsed):
                try:
                    release.wait(5)
                    return []
                finally:
                    ended.set()
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            update_setting_values(settings, {'metadata.total_timeout_seconds': '1'})
            nextSite = FakeRegisteredProvider('bangumi', 'Bangumi', 2, [])
            registry = [ProviderRegistryItem('tmdb', 'TMDB', 1, True, True, Slow('tmdb', 'TMDB', 1, [])),
                        ProviderRegistryItem('bangumi', 'Bangumi', 2, True, True, nextSite)]
            try:
                result = match_multi_source_metadata_candidates(settings, ParsedMediaName('movie', 'synthetic', 2024, None, None), registry=registry)
                self.assertEqual(['timeout', 'skipped'], [item.status for item in result.provider_results])
                self.assertFalse(nextSite.called)
            finally:
                release.set()
                self.assertTrue(ended.wait(2))

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

    def test_primary_only_uses_tmdb_even_when_other_provider_is_first(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            primary = FakeRegisteredProvider("tmdb", "TMDB", 99, [])
            other = FakeRegisteredProvider("bangumi", "Bangumi", 1, [])
            registry = [ProviderRegistryItem("bangumi", "Bangumi", 1, True, True, other),
                        ProviderRegistryItem("tmdb", "TMDB", 99, True, True, primary)]
            result = match_multi_source_metadata_candidates(settings, ParsedMediaName("movie", "synthetic", 2024, None, None),
                mode=MULTI_MATCH_MODE_PRIMARY_ONLY, registry=registry)
            self.assertTrue(primary.called)
            self.assertFalse(other.called)
            self.assertEqual(["tmdb"], [item.provider for item in result.provider_results])

    def test_disabled_or_placeholder_primary_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            for enabled, realSearch in [(False, True), (True, False)]:
                with self.subTest(enabled=enabled, realSearch=realSearch):
                    primary = FakeRegisteredProvider("tmdb", "TMDB", 99, [])
                    other = FakeRegisteredProvider("bangumi", "Bangumi", 1, [])
                    registry = [ProviderRegistryItem("bangumi", "Bangumi", 1, True, True, other),
                                ProviderRegistryItem("tmdb", "TMDB", 99, enabled, realSearch, primary)]
                    result = match_multi_source_metadata_candidates(settings, ParsedMediaName("movie", "synthetic", 2024, None, None),
                        mode=MULTI_MATCH_MODE_PRIMARY_ONLY, registry=registry)
                    self.assertFalse(primary.called)
                    self.assertFalse(other.called)
                    self.assertEqual("failed", result.summary.status)

    def test_provider_exception_does_not_expose_raw_secrets_in_result_or_logs(self):
        from unittest.mock import patch
        class PrivateFailure(FailingRegisteredProvider):
            def search(self, parsed):
                raise RuntimeError("Bearer synthetic-secret /private/media/path")
        with tempfile.TemporaryDirectory() as tempDir:
            settings = self.build_settings(Path(tempDir))
            registry = [ProviderRegistryItem("failing", "失败源", 40, True, True, PrivateFailure())]
            with patch("app.service.metadata_multi_source_service.logger") as logger:
                result = match_multi_source_metadata_candidates(settings, ParsedMediaName("movie", "synthetic", 2024, None, None), registry=registry)
            self.assertEqual("failed", result.provider_results[0].status)
            self.assertNotIn("synthetic-secret", str(result))
            self.assertNotIn("synthetic-secret", str(logger.mock_calls))


if __name__ == "__main__":
    unittest.main()
