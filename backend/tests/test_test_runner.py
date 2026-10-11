"""标准回归入口必须隔离默认应用配置并保留失败退出码。"""

import importlib.util
from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("mediaai_test_runner", Path(__file__).parents[1] / "run_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TestRunnerTest(unittest.TestCase):
    def test_isolated_config_exists_during_child_and_is_removed_afterwards(self):
        seen = []
        def child(command, **kwargs):
            config = Path(kwargs["env"]["MEDIAAI_CONFIG_PATH"])
            seen.append(config)
            payload = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertTrue(Path(payload["paths"]["data_dir"]).is_absolute())
            self.assertFalse(payload["logging"]["console_output"])
            self.assertEqual(["discover", "backend/tests", "-v"], command[-3:])
            return SimpleNamespace(returncode=0)
        with patch.object(runner.subprocess, "run", side_effect=child), patch("sys.argv", ["run_tests"]):
            original = os.environ.get("MEDIAAI_CONFIG_PATH")
            self.assertEqual(0, runner.main())
            self.assertEqual(original, os.environ.get("MEDIAAI_CONFIG_PATH"))
        self.assertFalse(seen[0].exists())

    def test_targeted_arguments_and_child_failure_are_preserved(self):
        with patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=3)) as child, \
             patch("sys.argv", ["run_tests", "test_task_governance", "-v"]):
            self.assertEqual(3, runner.main())
        self.assertEqual(["test_task_governance", "-v"], child.call_args.args[0][-2:])

    def test_child_failure_is_preserved_when_cleanup_also_fails(self):
        actual = tempfile.TemporaryDirectory(prefix="mediaai-runner-test-")
        temporary = SimpleNamespace(name=actual.name)
        temporary.cleanup = Mock(side_effect=PermissionError("synthetic cleanup failure"))
        stderr = StringIO()
        try:
            with patch.object(runner.tempfile, "TemporaryDirectory", return_value=temporary), \
                 patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=3)), \
                 patch("sys.argv", ["run_tests"]), redirect_stderr(stderr):
                self.assertEqual(3, runner.main())
            self.assertIn(str(Path(actual.name)), stderr.getvalue())
            self.assertIn("PermissionError", stderr.getvalue())
        finally:
            actual.cleanup()

    def test_successful_child_returns_failure_when_cleanup_fails(self):
        actual = tempfile.TemporaryDirectory(prefix="mediaai-runner-test-")
        temporary = SimpleNamespace(name=actual.name)
        temporary.cleanup = Mock(side_effect=PermissionError("synthetic cleanup failure"))
        stderr = StringIO()
        try:
            with patch.object(runner.tempfile, "TemporaryDirectory", return_value=temporary), \
                 patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                 patch("sys.argv", ["run_tests"]), redirect_stderr(stderr):
                self.assertEqual(1, runner.main())
            self.assertIn("测试临时目录清理失败", stderr.getvalue())
        finally:
            actual.cleanup()
