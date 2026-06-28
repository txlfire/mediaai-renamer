"""System hot settings service tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.settings_service import (
    get_effective_settings,
    list_setting_values,
    update_setting_values,
)


class SettingsServiceTest(unittest.TestCase):
    """Hot settings storage, validation, masking, and priority tests."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_default_tmdb_settings_are_available_without_database_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            effective = get_effective_settings(settings)

            self.assertEqual("zh-CN", effective["tmdb.language"])
            self.assertEqual("CN", effective["tmdb.region"])
            self.assertEqual(10000, effective["tmdb.timeout_ms"])
            self.assertEqual(False, effective["tmdb.enabled"])
            self.assertEqual(0, effective["scan.minimum_file_size"])

    def test_default_m5_non_tmdb_settings_are_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            effective = get_effective_settings(settings)

            self.assertEqual(100, effective["scan.batch_size"])
            self.assertEqual(1, effective["scan.batch_interval_seconds"])
            self.assertEqual(True, effective["scan.skip_hidden_files"])
            self.assertEqual(True, effective["scan.recursive"])
            self.assertEqual(True, effective["scan.validate_path_before_scan"])
            self.assertEqual("{title}.{year}", effective["naming.movie_template"])
            self.assertEqual("{title}.{year}.S{season:02d}E{episode:02d}", effective["naming.episode_template"])
            self.assertEqual(".", effective["naming.separator"])
            self.assertEqual(True, effective["naming.keep_year"])
            self.assertEqual(True, effective["naming.clean_illegal_chars"])
            self.assertEqual(50, effective["naming.text_truncate_bytes"])
            self.assertEqual(80, effective["naming.path_truncate_bytes"])
            self.assertEqual(30, effective["operations.log_retention_days"])
            self.assertEqual(200, effective["operations.log_default_limit"])
            self.assertEqual(True, effective["operations.force_dry_run"])
            self.assertEqual(True, effective["operations.require_second_confirmation"])
            self.assertEqual(True, effective["operations.persist_failure_detail"])
            self.assertEqual(200, effective["operations.batch_limit"])
            self.assertEqual("local", effective["shared.default_path_type"])
            self.assertEqual(5, effective["shared.connection_timeout_seconds"])
            self.assertEqual(500, effective["shared.directory_browse_limit"])
            self.assertEqual(True, effective["shared.force_scan_connection_test"])
            self.assertEqual(True, effective["shared.force_rename_write_test"])
            self.assertEqual(30, effective["shared.nfs_operation_timeout_seconds"])
            self.assertEqual(3, effective["shared.nfs_retry_count"])
            self.assertEqual(True, effective["shared.prefer_nfsv4"])
            self.assertEqual(60, effective["shared.mount_check_interval_seconds"])

    def test_environment_values_override_database_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {"tmdb.api_key": "db-secret", "tmdb.language": "en-US"},
                operator="admin",
            )

            with patch.dict(os.environ, {"TMDB_API_KEY": "env-secret", "TMDB_LANGUAGE": "zh-CN"}):
                effective = get_effective_settings(settings)

            self.assertEqual("env-secret", effective["tmdb.api_key"])
            self.assertEqual("zh-CN", effective["tmdb.language"])

    def test_sensitive_values_are_masked_for_list_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(settings, {"tmdb.api_key": "abcdef123456"}, operator="admin")

            values = list_setting_values(settings)
            api_key = next(item for item in values if item.key == "tmdb.api_key")

            self.assertTrue(api_key.sensitive)
            self.assertEqual("********3456", api_key.value)

    def test_v4_token_accepts_jwt_like_characters_and_masks_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            token = "eyJhbGciOiJIUzI1NiJ9.payload-part_signature"

            update_setting_values(settings, {"tmdb.v4_token": token}, operator="admin")
            effective = get_effective_settings(settings)
            values = list_setting_values(settings)
            v4_token = next(item for item in values if item.key == "tmdb.v4_token")

            self.assertEqual(token, effective["tmdb.v4_token"])
            self.assertTrue(v4_token.sensitive)
            self.assertEqual("********ture", v4_token.value)

    def test_invalid_values_are_rejected_and_do_not_update_effective_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            invalid_payloads = [
                {"tmdb.timeout_ms": "999"},
                {"tmdb.timeout_ms": "30001"},
                {"tmdb.language": "bad"},
                {"tmdb.region": "China"},
                {"tmdb.enabled": "yes"},
                {"scan.minimum_file_size": "-1"},
            ]

            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(ValueError):
                        update_setting_values(settings, payload, operator="admin")

            effective = get_effective_settings(settings)
            self.assertEqual(10000, effective["tmdb.timeout_ms"])
            self.assertEqual("zh-CN", effective["tmdb.language"])
            self.assertEqual("CN", effective["tmdb.region"])
            self.assertEqual(False, effective["tmdb.enabled"])
            self.assertEqual(0, effective["scan.minimum_file_size"])

    def test_m5_non_tmdb_settings_validate_and_persist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            update_setting_values(
                settings,
                {
                    "scan.batch_size": "50",
                    "scan.batch_interval_seconds": "0",
                    "naming.movie_template": "{title}",
                    "naming.separator": "-",
                    "operations.log_retention_days": "7",
                    "shared.default_path_type": "mounted_nfs",
                    "shared.directory_browse_limit": "300",
                },
                operator="admin",
            )

            effective = get_effective_settings(settings)
            self.assertEqual(50, effective["scan.batch_size"])
            self.assertEqual(0, effective["scan.batch_interval_seconds"])
            self.assertEqual("{title}", effective["naming.movie_template"])
            self.assertEqual("-", effective["naming.separator"])
            self.assertEqual(7, effective["operations.log_retention_days"])
            self.assertEqual("mounted_nfs", effective["shared.default_path_type"])
            self.assertEqual(300, effective["shared.directory_browse_limit"])

    def test_m5_non_tmdb_settings_reject_invalid_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            invalid_payloads = [
                {"scan.batch_size": "0"},
                {"scan.batch_interval_seconds": "-1"},
                {"naming.movie_template": ""},
                {"naming.movie_template": "***"},
                {"naming.separator": ""},
                {"operations.batch_limit": "0"},
                {"shared.default_path_type": "webdav"},
                {"shared.connection_timeout_seconds": "0"},
                {"shared.directory_browse_limit": "0"},
                {"shared.nfs_retry_count": "-1"},
            ]

            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(ValueError):
                        update_setting_values(settings, payload, operator="admin")

    def test_database_updates_are_visible_without_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            update_setting_values(settings, {"tmdb.timeout_ms": "15000"}, operator="admin")
            first = get_effective_settings(settings)
            update_setting_values(settings, {"tmdb.timeout_ms": "25000"}, operator="admin")
            second = get_effective_settings(settings)

            self.assertEqual(15000, first["tmdb.timeout_ms"])
            self.assertEqual(25000, second["tmdb.timeout_ms"])


if __name__ == "__main__":
    unittest.main()
