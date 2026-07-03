"""Rename preview metadata matching tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.schema.ai_parse import AiParseCandidate
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchResult, MetadataMatchSummary
from app.service.preview_service import (
    METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME,
    METADATA_MATCH_SOURCE_PARSED_TITLE,
    apply_ai_parse_candidate,
    apply_metadata_candidate,
    generate_rename_previews,
    list_metadata_candidates,
    list_rename_previews,
    match_rename_preview_metadata,
    match_rename_previews_metadata,
)
from app.service.external_submission_guard import list_external_submission_blocks
from app.service.settings_service import update_setting_values


class FakeMetadataProvider:
    def __init__(self, candidates: list[MetadataCandidate]):
        self.candidates = candidates
        self.searched: list[ParsedMediaName] = []

    def search(self, parsed: ParsedMediaName) -> list[MetadataCandidate]:
        self.searched.append(parsed)
        return self.candidates


class RenamePreviewMetadataTest(unittest.TestCase):
    """TMDB matching and preview backfill behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.media_file = self.media_dir / "The.Matrix.1999.1080p.mkv"
        self.media_file.write_text("sample", encoding="utf-8")
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
            scan=ScanSettings(batch_size=100, batch_interval_seconds=0),
        )
        ensure_database(self.settings)
        self._insert_media_file()
        update_setting_values(
            self.settings,
            {"tmdb.enabled": "true", "tmdb.api_key": "abcdef123456"},
            operator="admin",
        )
        generate_rename_previews(self.settings)
        self.preview = list_rename_previews(self.settings)[0]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_media_file(self):
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
            connection.commit()

    def test_high_confidence_match_backfills_preview_automatically(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, ""),
            ]
        )

        updated = match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)

        self.assertEqual("generated", updated.status)
        self.assertEqual("TMDB", updated.metadata_source)
        self.assertEqual("high_confidence", updated.metadata_match_status)
        self.assertEqual(100, updated.metadata_match_score)
        self.assertEqual(1999, updated.parsed_year)
        self.assertEqual("黑客帝国.1999.mkv", updated.current_target_name)

    def test_sensitive_word_blocks_metadata_match_without_provider_call(self):
        update_setting_values(
            self.settings,
            {"privacy.custom_sensitive_words": '["The Matrix"]'},
            operator="admin",
        )
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, ""),
            ]
        )

        updated = match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)
        candidates = list_metadata_candidates(self.settings, self.preview.id, provider=provider)
        blocks = list_external_submission_blocks(self.settings, status="blocked")

        self.assertEqual([], provider.searched)
        self.assertEqual([], candidates)
        self.assertEqual("needs_review", updated.status)
        self.assertEqual("blocked", updated.metadata_match_status)
        self.assertEqual("external_submission_guard", updated.metadata_source)
        self.assertEqual(1, blocks.total)
        self.assertEqual("tmdb", blocks.items[0].target_service)

    def test_high_confidence_match_records_effective_metadata_source(self):
        candidate = MetadataCandidate("TMDB", "603", "movie", "榛戝甯濆浗", "The Matrix", 1999, None, None, "")
        summary = MetadataMatchSummary(
            status="high_confidence",
            message=None,
            matches=[MetadataMatchResult(candidate, 100, "high_confidence")],
            metadata_source="TMDB (V4)",
        )

        with patch("app.service.preview_service.match_metadata_candidates", return_value=summary):
            updated = match_rename_preview_metadata(self.settings, self.preview.id)

        self.assertEqual("generated", updated.status)
        self.assertEqual("TMDB (V4)", updated.metadata_source)

    def test_low_confidence_match_keeps_preview_and_exposes_candidates(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "low", "movie", "The Matrix Reloaded", "", 1999, None, None, ""),
            ]
        )

        updated = match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)
        candidates = list_metadata_candidates(self.settings, self.preview.id, provider=provider)

        self.assertEqual("generated", updated.status)
        self.assertEqual("low_confidence", updated.metadata_match_status)
        self.assertGreaterEqual(updated.metadata_match_score, 30)
        self.assertEqual("The.Matrix.Reloaded.1999.mkv", updated.current_target_name)
        self.assertEqual("low", candidates[0].candidate.provider_id)

    def test_match_backfills_template_fields_when_candidate_has_data(self):
        update_setting_values(
            self.settings,
            {
                "naming.movie_template": (
                    '[{"key":"title","label":"标题","variable":"title"},'
                    '{"key":"original_title","label":"英文标题","variable":"english_title"},'
                    '{"key":"year","label":"年份","variable":"year"},'
                    '{"key":"tmdb_id","label":"TMDB ID","variable":"tmdb_id","format":{"prefix":"tmdb-","bracketStyle":"square"}},'
                    '{"key":"imdb_id","label":"IMDb ID","variable":"imdb_id","format":{"prefix":"tt","bracketStyle":"square"}}]'
                ),
            },
            operator="admin",
        )
        provider = FakeMetadataProvider(
            [
                MetadataCandidate(
                    "TMDB",
                    "603",
                    "movie",
                    "黑客帝国",
                    "The Matrix",
                    1999,
                    None,
                    None,
                    "",
                    english_title="The Matrix",
                    tmdb_id="603",
                    imdb_id="0133093",
                ),
            ]
        )

        updated = match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)

        self.assertEqual("generated", updated.status)
        self.assertEqual("high_confidence", updated.metadata_match_status)
        self.assertEqual("黑客帝国.The.Matrix.1999.[tmdb-603].[tt0133093].mkv", updated.current_target_name)

    def test_manual_candidate_selection_backfills_preview(self):
        candidate = MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, "")

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=72)

        self.assertEqual("generated", updated.status)
        self.assertEqual("TMDB", updated.metadata_source)
        self.assertEqual("manual_selected", updated.metadata_match_status)
        self.assertEqual(72, updated.metadata_match_score)
        self.assertEqual(1999, updated.parsed_year)
        self.assertEqual("黑客帝国.1999.mkv", updated.current_target_name)

    def test_manual_candidate_selection_overrides_existing_edited_name(self):
        candidate = MetadataCandidate("TMDB", "603", "movie", "Matrix CN", "The Matrix", 1999, None, None, "")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET edited_name = ? WHERE id = ?",
                ("Manual.Name.mkv", self.preview.id),
            )
            connection.commit()

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=72)

        self.assertEqual("Matrix.CN.1999.mkv", updated.current_target_name)
        self.assertIsNone(updated.edited_name)
        self.assertEqual("Matrix CN", updated.parsed_title)
        self.assertEqual(1999, updated.parsed_year)

    def test_manual_candidate_selection_respects_selected_fields(self):
        update_setting_values(
            self.settings,
            {
                "naming.movie_template": (
                    '[{"key":"title","label":"标题","variable":"title"},'
                    '{"key":"year","label":"年份","variable":"year"},'
                    '{"key":"tmdb_id","label":"TMDB ID","variable":"tmdb_id","format":{"prefix":"tmdb-","bracketStyle":"square"}}]'
                ),
            },
            operator="admin",
        )
        candidate = MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, "")

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=72, selected_fields={"title", "year"})

        self.assertEqual("黑客帝国.1999.mkv", updated.current_target_name)

    def test_manual_movie_candidate_selection_preserves_template_required_year(self):
        update_setting_values(
            self.settings,
            {
                "naming.movie_template": '[{"key":"title","label":"标题","variable":"title"},{"key":"year","label":"年份","variable":"year"}]',
            },
            operator="admin",
        )
        candidate = MetadataCandidate("TMDB", "movie-1", "movie", "The Matrix", "", None, None, None, "")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET media_type = ?, parsed_title = ?, parsed_year = ? WHERE id = ?",
                ("movie", "The Matrix", 1999, self.preview.id),
            )
            connection.commit()

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=72)

        self.assertEqual("generated", updated.status)
        self.assertEqual("manual_selected", updated.metadata_match_status)
        self.assertEqual(72, updated.metadata_match_score)
        self.assertEqual("The Matrix", updated.parsed_title)
        self.assertEqual(1999, updated.parsed_year)
        self.assertEqual("The.Matrix.1999.mkv", updated.current_target_name)

    def test_manual_episode_candidate_selection_preserves_template_required_fields(self):
        update_setting_values(
            self.settings,
            {
                "naming.episode_template": '[{"key":"title","label":"标题","variable":"title"},{"key":"year","label":"年份","variable":"year"},{"key":"season_episode","label":"季集组合","variable":"season_episode"}]',
            },
            operator="admin",
        )
        candidate = MetadataCandidate("TMDB", "tv-1", "episode", "廉政追缉令", "ICAC Investigators", 1997, None, None, "")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET media_type = ?, parsed_title = ?, parsed_year = ?, season = ?, episode = ? WHERE id = ?",
                ("episode", "廉政追缉令", None, 1, 17, self.preview.id),
            )
            connection.commit()

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=70)

        self.assertEqual("generated", updated.status)
        self.assertEqual("manual_selected", updated.metadata_match_status)
        self.assertEqual(70, updated.metadata_match_score)
        self.assertEqual("廉政追缉令", updated.parsed_title)
        self.assertEqual(1997, updated.parsed_year)
        self.assertEqual(1, updated.season)
        self.assertEqual(17, updated.episode)
        self.assertEqual("廉政追缉令.1997.S01E17.mkv", updated.current_target_name)

    def test_ai_candidate_selection_backfills_preview_with_template(self):
        update_setting_values(
            self.settings,
            {
                "naming.movie_template": (
                    '[{"key":"title","label":"标题","variable":"title"},'
                    '{"key":"year","label":"年份","variable":"year"}]'
                ),
            },
            operator="admin",
        )
        candidate = AiParseCandidate(
            title="黑客帝国",
            media_type="movie",
            year=1999,
            season=None,
            episode=None,
            confidence=88,
            reason="AI 识别到中文标题和年份",
            raw_data={"source": "ai"},
        )

        updated = apply_ai_parse_candidate(self.settings, self.preview.id, candidate)

        self.assertEqual("generated", updated.status)
        self.assertEqual("AI", updated.metadata_source)
        self.assertEqual("manual_selected", updated.metadata_match_status)
        self.assertEqual(88, updated.metadata_match_score)
        self.assertEqual("AI 识别到中文标题和年份", updated.metadata_message)
        self.assertEqual("黑客帝国.1999.mkv", updated.current_target_name)

    def test_metadata_match_uses_parsed_title_by_default(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, ""),
            ]
        )
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET edited_name = ?, media_type = ?, parsed_title = ?, parsed_year = ? WHERE id = ?",
                ("Better.Title.2001.mkv", "movie", "The Matrix", 1999, self.preview.id),
            )
            connection.commit()

        match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)

        self.assertEqual("The Matrix", provider.searched[0].title)
        self.assertEqual(1999, provider.searched[0].year)

    def test_metadata_match_can_use_original_file_name(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, ""),
            ]
        )
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE rename_previews SET edited_name = ?, media_type = ?, parsed_title = ?, parsed_year = ? WHERE id = ?",
                ("Better.Title.2001.mkv", "movie", "Wrong Title", 2001, self.preview.id),
            )
            connection.commit()

        match_rename_preview_metadata(
            self.settings,
            self.preview.id,
            provider=provider,
            metadata_match_source=METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME,
        )

        self.assertEqual("The Matrix", provider.searched[0].title)
        self.assertEqual(1999, provider.searched[0].year)

    def test_metadata_match_rejects_unknown_source(self):
        provider = FakeMetadataProvider([])

        with self.assertRaises(ValueError):
            match_rename_preview_metadata(
                self.settings,
                self.preview.id,
                provider=provider,
                metadata_match_source="target_name",
            )

    def test_metadata_candidate_list_uses_requested_source(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "The Matrix", "The Matrix", 1999, None, None, ""),
            ]
        )

        list_metadata_candidates(
            self.settings,
            self.preview.id,
            provider=provider,
            metadata_match_source=METADATA_MATCH_SOURCE_PARSED_TITLE,
        )

        self.assertEqual("The Matrix", provider.searched[0].title)

    def test_batch_metadata_match_updates_selected_previews(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, ""),
            ]
        )

        result = match_rename_previews_metadata(self.settings, [self.preview.id], provider=provider)

        self.assertEqual(1, result["total_count"])
        self.assertEqual(1, result["success_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertEqual("generated", result["items"][0].status)

    def test_batch_metadata_match_keeps_candidates_without_backfilling(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, ""),
            ]
        )

        result = match_rename_previews_metadata(self.settings, [self.preview.id], provider=provider)
        updated = result["items"][0]
        candidates = list_metadata_candidates(self.settings, self.preview.id)

        self.assertEqual("generated", updated.status)
        self.assertEqual("The.Matrix.1999.mkv", updated.current_target_name)
        self.assertEqual("high_confidence", updated.metadata_match_status)
        self.assertEqual(1, updated.metadata_candidate_count)
        self.assertEqual("603", candidates[0].candidate.provider_id)


if __name__ == "__main__":
    unittest.main()
