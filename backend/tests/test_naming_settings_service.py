"""命名规则导入导出服务测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.naming_settings_service import (
    export_naming_template_bundle,
    validate_naming_template_bundle_text,
)
from app.service.settings_service import update_setting_values


class NamingSettingsServiceTest(unittest.TestCase):
    """命名规则导入导出与结构校验。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_export_bundle_uses_current_effective_settings(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "naming.movie_template": '[{"key":"title","label":"标题","variable":"title"}]',
                    "naming.episode_template": '[{"key":"title","label":"标题","variable":"title"},{"key":"episode","label":"集","variable":"episode"}]',
                    "naming.separator": "-",
                    "naming.keep_year": False,
                },
                operator="admin",
            )

            bundle = export_naming_template_bundle(settings)

            self.assertEqual(1, bundle.schema_version)
            self.assertEqual('[{"key":"title","label":"标题","variable":"title"}]', bundle.movie_template)
            self.assertEqual(
                '[{"key":"title","label":"标题","variable":"title"},{"key":"episode","label":"集","variable":"episode"}]',
                bundle.episode_template,
            )
            self.assertEqual("-", bundle.separator)
            self.assertEqual(False, bundle.keep_year)

    def test_import_bundle_validation_returns_normalized_payload(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            bundle = validate_naming_template_bundle_text(
                settings,
                """
                {
                  "schema_version": 1,
                  "movie_template": "[{\\"key\\":\\"title\\",\\"label\\":\\"标题\\",\\"variable\\":\\"title\\"}]",
                  "episode_template": "[{\\"key\\":\\"title\\",\\"label\\":\\"标题\\",\\"variable\\":\\"title\\"}]",
                  "separator": ".",
                  "keep_year": true
                }
                """,
            )

            self.assertEqual(1, bundle.schema_version)
            self.assertEqual(".", bundle.separator)
            self.assertEqual(True, bundle.keep_year)
            self.assertIn('"variable":"title"', bundle.movie_template)

    def test_import_bundle_validation_rejects_unsupported_schema_version(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            with self.assertRaisesRegex(ValueError, "模板版本不受支持"):
                validate_naming_template_bundle_text(
                    settings,
                    """
                    {
                      "schema_version": 0,
                      "movie_template": "[{\\"key\\":\\"title\\",\\"label\\":\\"标题\\",\\"variable\\":\\"title\\"}]",
                      "episode_template": "[{\\"key\\":\\"title\\",\\"label\\":\\"标题\\",\\"variable\\":\\"title\\"}]"
                    }
                    """,
                )


if __name__ == "__main__":
    unittest.main()
