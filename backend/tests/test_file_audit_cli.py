"""审计维护命令必须默认只读、显式配置且不初始化实例。"""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.recover_file_audits import main


class FileAuditCliTest(unittest.TestCase):
    def test_default_only_inspects_and_does_not_flush(self):
        with tempfile.TemporaryDirectory() as tempDir:
            configPath = Path(tempDir) / "config.toml"
            configPath.touch()
            with patch("sys.argv", ["recover_file_audits", "--config", str(configPath)]), \
                 patch("app.recover_file_audits.inspectFileActionAuditIntents", return_value={"items": [], "has_more": 0, "next_id": 0}) as inspect, \
                 patch("app.recover_file_audits.flushFileActionAuditIntents") as flush, redirect_stdout(io.StringIO()):
                self.assertEqual(0, main())
            inspect.assert_called_once()
            flush.assert_not_called()

    def test_apply_requires_stopped_confirmation_before_database_access(self):
        with tempfile.TemporaryDirectory() as tempDir:
            configPath = Path(tempDir) / "config.toml"
            configPath.touch()
            with patch("sys.argv", ["recover_file_audits", "--config", str(configPath), "--apply"]), \
                 patch("app.recover_file_audits.load_settings") as load:
                with self.assertRaises(SystemExit) as exc:
                    main()
            self.assertEqual(2, exc.exception.code)
            load.assert_not_called()

    def test_missing_database_is_not_created_and_does_not_initialize_app(self):
        with tempfile.TemporaryDirectory() as tempDir:
            configPath = Path(tempDir) / "config.toml"
            databasePath = Path(tempDir) / "missing.sqlite3"
            configPath.write_text(f'[paths]\ndatabase_path = "{databasePath.as_posix()}"\n', encoding="utf-8")
            output = io.StringIO()
            with patch("sys.argv", ["recover_file_audits", "--config", str(configPath)]), redirect_stdout(output):
                self.assertEqual(1, main())
            self.assertFalse(databasePath.exists())
            self.assertIn("不会初始化数据库", output.getvalue())
