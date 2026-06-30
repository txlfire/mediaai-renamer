"""Rename preview metadata matching tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate, MetadataMatchResult, MetadataMatchSummary
from app.service.preview_service import (
    METADATA_MATCH_SOURCE_ORIGINAL_FILE_NAME,
    METADATA_MATCH_SOURCE_PARSED_TITLE,
    apply_metadata_candidate,
    generate_rename_previews,
    list_metadata_candidates,
    list_rename_previews,
    match_rename_preview_metadata,
    match_rename_previews_metadata,
)
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

        self.assertEqual("tmdb_matched", updated.status)
        self.assertEqual("TMDB", updated.metadata_source)
        self.assertEqual("high_confidence", updated.metadata_match_status)
        self.assertEqual(100, updated.metadata_match_score)
        self.assertEqual(1999, updated.parsed_year)
        self.assertEqual("黑客帝国.1999.mkv", updated.current_target_name)

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

        self.assertEqual("tmdb_matched", updated.status)
        self.assertEqual("TMDB (V4)", updated.metadata_source)

    def test_low_confidence_match_keeps_preview_and_exposes_candidates(self):
        provider = FakeMetadataProvider(
            [
                MetadataCandidate("TMDB", "low", "movie", "The Matrix Reloaded", "", 1999, None, None, ""),
            ]
        )

        updated = match_rename_preview_metadata(self.settings, self.preview.id, provider=provider)
        candidates = list_metadata_candidates(self.settings, self.preview.id, provider=provider)

        self.assertEqual("needs_review", updated.status)
        self.assertEqual("low_confidence", updated.metadata_match_status)
        self.assertGreaterEqual(updated.metadata_match_score, 30)
        self.assertEqual("The.Matrix.1999.mkv", updated.current_target_name)
        self.assertEqual("low", candidates[0].candidate.provider_id)

    def test_manual_candidate_selection_backfills_preview(self):
        candidate = MetadataCandidate("TMDB", "603", "movie", "黑客帝国", "The Matrix", 1999, None, None, "")

        updated = apply_metadata_candidate(self.settings, self.preview.id, candidate, score=72)

        self.assertEqual("tmdb_selected", updated.status)
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
        self.assertEqual("tmdb_matched", result["items"][0].status)


if __name__ == "__main__":
    unittest.main()
