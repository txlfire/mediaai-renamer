"""本地探测核对CLI不创建数据库、不替换默认配置、不发云请求。"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import recover_cloud_directory as cli


class DirectoryProbeCliTest(unittest.TestCase):
    def invoke(self, arguments):
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["recover_cloud_directory", *arguments]), redirect_stdout(output), redirect_stderr(error):
            try:
                cli.main()
            except SystemExit as exc:
                return exc.code, output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()

    def test_missing_explicit_config_does_not_fall_back_to_default_instance(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "load_settings") as load:
            code, _, error = self.invoke(["--config", str(Path(temp) / "absent.toml")])
            self.assertEqual(2, code)
            self.assertIn("不读取默认实例", error)
            load.assert_not_called()
            self.assertEqual([], list(Path(temp).iterdir()))

    def test_default_inspection_never_calls_release_and_sanitizes_config_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text("[paths]\n", encoding="utf-8")
            with patch.object(cli, "load_settings", return_value=object()), \
                    patch.object(cli, "inspectDirectoryProbes", return_value=[]) as inspect, \
                    patch.object(cli, "releaseDirectoryProbeAfterReview") as release:
                code, output, _ = self.invoke(["--config", str(config)])
                self.assertEqual(0, code)
                self.assertEqual({"probes": [], "networkAccess": False, "replayed": False, "applied": False}, json.loads(output))
                inspect.assert_called_once()
                release.assert_not_called()
            with patch.object(cli, "load_settings", side_effect=ValueError("synthetic-private-config")):
                code, _, error = self.invoke(["--config", str(config)])
                self.assertEqual(2, code)
                self.assertNotIn("synthetic-private", error)

    def test_apply_requires_target_and_passes_explicit_review_flags_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text("[paths]\n", encoding="utf-8")
            with patch.object(cli, "load_settings", return_value=object()) as load, \
                    patch.object(cli, "inspectDirectoryProbes", return_value=[]), \
                    patch.object(cli, "releaseDirectoryProbeAfterReview") as release:
                self.assertEqual(2, self.invoke(["--config", str(config), "--apply"])[0])
                load.assert_not_called()
                code, output, _ = self.invoke(["--config", str(config), "--release-probe", "synthetic", "--apply",
                    "--confirm-process-stopped", "--confirm-remote-idle"])
                self.assertEqual(0, code)
                self.assertTrue(json.loads(output)["applied"])
                self.assertEqual({"confirmProcessStopped": True, "confirmRemoteIdle": True}, release.call_args.kwargs)
