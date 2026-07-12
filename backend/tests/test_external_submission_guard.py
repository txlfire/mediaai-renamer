"""External submission protection service tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.external_submission_guard import (
    check_external_submission,
    list_external_submission_blocks,
)
from app.service.settings_service import update_setting_values


class ExternalSubmissionGuardTest(unittest.TestCase):
    """Sensitive word filtering and block record persistence."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_allows_clean_submission_without_writing_block_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=1,
                file_name="The.Matrix.1999.mkv",
                file_path="/media/movie/The.Matrix.1999.mkv",
                match_title="The Matrix",
                target_service="tmdb",
            )

            self.assertTrue(result.allowed)
            self.assertEqual([], result.matched_words)
            self.assertEqual([], list_external_submission_blocks(settings))

    def test_blocks_default_media_risk_word_and_persists_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=7,
                file_name="血腥暴力电影.2024.mp4",
                file_path="/media/血腥暴力电影.2024.mp4",
                match_title="血腥暴力电影",
                target_service="tmdb",
            )
            records = list_external_submission_blocks(settings)

            self.assertFalse(result.allowed)
            self.assertEqual(["暴力", "血腥"], result.matched_words)
            self.assertIsNotNone(result.block_record)
            self.assertEqual(1, len(records))
            self.assertEqual("rename_preview", records[0].source_module)
            self.assertEqual(7, records[0].source_record_id)
            self.assertEqual("tmdb", records[0].target_service)
            self.assertEqual("sensitive_word", records[0].block_rule_type)
            self.assertEqual("blocked", records[0].status)
            self.assertIn("暴*", records[0].matched_value_masked)
            self.assertIn("血*", records[0].matched_value_masked)

    def test_short_ascii_sensitive_word_does_not_match_inside_safe_word(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=12,
                file_name="The.Matrix.1999.mkv",
                file_path="/media/save-cache/The.Matrix.1999.mkv",
                match_title="The Matrix",
                target_service="tmdb",
            )

            self.assertTrue(result.allowed)
            self.assertEqual([], result.matched_words)

    def test_short_ascii_sensitive_word_still_matches_path_segment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=13,
                file_name="movie.mkv",
                file_path="/media/AV/movie.mkv",
                match_title="movie",
                target_service="tmdb",
            )

            self.assertFalse(result.allowed)
            self.assertEqual(["AV"], result.matched_words)

    def test_can_disable_default_sensitive_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {"privacy.default_sensitive_words_enabled": "false"},
                operator="admin",
            )

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=8,
                file_name="血腥暴力电影.2024.mp4",
                file_path="/media/血腥暴力电影.2024.mp4",
                match_title="血腥暴力电影",
                target_service="tmdb",
            )

            self.assertTrue(result.allowed)
            self.assertEqual([], list_external_submission_blocks(settings))

    def test_can_replace_default_sensitive_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {"privacy.default_sensitive_words": '["剧透风险"]'},
                operator="admin",
            )

            clean_default_result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=8,
                file_name="血腥暴力电影.2024.mp4",
                file_path="/media/血腥暴力电影.2024.mp4",
                match_title="血腥暴力电影",
                target_service="tmdb",
            )
            replaced_result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=9,
                file_name="剧透风险电影.2024.mp4",
                file_path="/media/剧透风险电影.2024.mp4",
                match_title="剧透风险电影",
                target_service="tmdb",
            )

            self.assertTrue(clean_default_result.allowed)
            self.assertFalse(replaced_result.allowed)
            self.assertEqual(["剧透风险"], replaced_result.matched_words)

    def test_blocks_custom_sensitive_word_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {"privacy.custom_sensitive_words": '["LeakedCut"]'},
                operator="admin",
            )

            result = check_external_submission(
                settings,
                source_module="pending_file",
                source_record_id=3,
                file_name="movie.mkv",
                file_path="/media/movie.mkv",
                match_title="leakedcut version",
                target_service="ai",
            )

            self.assertFalse(result.allowed)
            self.assertEqual(["LeakedCut"], result.matched_words)

    def test_skips_duplicate_block_record_for_same_source_target_and_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            for _ in range(2):
                check_external_submission(
                    settings,
                    source_module="rename_preview",
                    source_record_id=9,
                    file_name="暴力电影.mkv",
                    file_path="/media/暴力电影.mkv",
                    match_title="暴力电影",
                    target_service="tmdb",
                )

            records = list_external_submission_blocks(settings)

            self.assertEqual(1, len(records))
            self.assertEqual("blocked", records[0].status)

    def test_block_record_schema_can_store_decision_audit_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            result = check_external_submission(
                settings,
                source_module="rename_preview",
                source_record_id=11,
                file_name="血腥电影.mkv",
                file_path="/media/血腥电影.mkv",
                match_title="血腥电影",
                target_service="tmdb",
            )

            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute(
                    "UPDATE external_submission_blocks "
                    "SET status = ?, user_decision = ?, override_reason = ?, operator = ?, decided_at = ? "
                    "WHERE id = ?",
                    (
                        "override_submitted",
                        "用户确认继续提交",
                        "测试环境确认",
                        "admin",
                        "2026-06-30T00:00:00+00:00",
                        result.block_record.id,
                    ),
                )
                connection.commit()

            record = list_external_submission_blocks(settings)[0]

            self.assertEqual("override_submitted", record.status)
            self.assertEqual("用户确认继续提交", record.user_decision)
            self.assertEqual("测试环境确认", record.override_reason)
            self.assertEqual("admin", record.operator)


if __name__ == "__main__":
    unittest.main()
