"""Runtime configuration loading tests."""

from pathlib import Path
import tempfile
import unittest

from app.core.config import AppSettings, load_settings


class ConfigLoadingTest(unittest.TestCase):
    def test_missing_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_settings(Path(temp_dir) / "missing.toml")

        self.assertEqual(AppSettings().data_dir, settings.data_dir)
        self.assertEqual(AppSettings().scan.batch_size, settings.scan.batch_size)

    def test_load_settings_reads_formal_config_toml(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[app]
name = "Custom MediaAI"
version = "9.9.9"

[paths]
data_dir = "runtime-data"
database_path = "runtime-data/custom.sqlite3"

[logging]
level = "DEBUG"
log_dir = "runtime-logs"
max_size_mb = 2
backup_count = 3
console_output = false

[scan]
batch_size = 7
batch_interval_seconds = 0.5
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path)

        self.assertEqual("Custom MediaAI", settings.app_name)
        self.assertEqual("9.9.9", settings.version)
        self.assertEqual(Path("runtime-data"), settings.data_dir)
        self.assertEqual(Path("runtime-data/custom.sqlite3"), settings.database_path)
        self.assertEqual("DEBUG", settings.logging.level)
        self.assertEqual(Path("runtime-logs"), settings.logging.log_dir)
        self.assertEqual(2, settings.logging.max_size_mb)
        self.assertEqual(3, settings.logging.backup_count)
        self.assertFalse(settings.logging.console_output)
        self.assertEqual(7, settings.scan.batch_size)
        self.assertEqual(0.5, settings.scan.batch_interval_seconds)


if __name__ == "__main__":
    unittest.main()
