"""公共worker启动契约；Windows虚拟环境必须避免额外PID启动器。"""

import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.core.config import AppSettings
from app.service import isolated_task_service as isolated


class WorkerLauncherTest(unittest.TestCase):
    def test_command_uses_base_python_with_venv_marker_only_on_windows_venv(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3")
        events = []
        class Input(io.BytesIO):
            def write(self, data):
                events.append("stdin")
                return super().write(data)
        process = Mock(pid=123, stdin=Input())
        with (patch.object(isolated, "resolveRuntimeLogging", return_value=settings.logging),
              patch.object(isolated.subprocess, "Popen", return_value=process) as spawn):
            isolated.startTaskProcess(settings, "app.cloud_inventory_worker", {"test": True}, lambda pid: events.append("pid"))
        args, kwargs = spawn.call_args
        self.assertEqual(["pid", "stdin"], events)
        self.assertEqual(["-m", "app.cloud_inventory_worker"], args[0][1:])
        self.assertNotIn("test", args[0])
        if os.name == "nt" and sys.prefix != sys.base_prefix:
            self.assertEqual(sys._base_executable, args[0][0])
            self.assertEqual(sys.executable, kwargs["env"]["__PYVENV_LAUNCHER__"])
            self.assertEqual(subprocess.CREATE_NO_WINDOW, kwargs["creationflags"])
        else:
            self.assertEqual(sys.executable, args[0][0])

    @unittest.skipUnless(os.name == "nt" and sys.prefix != sys.base_prefix, "仅Windows虚拟环境")
    def test_cpython_venv_launch_keeps_actual_pid_and_dependency_environment(self):
        environment = {**os.environ, "__PYVENV_LAUNCHER__": sys.executable}
        with subprocess.Popen([sys._base_executable, "-c",
                "import os,sys,grpc; print(os.getpid()); print(sys.prefix)"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW) as process:
            output, errors = process.communicate(timeout=15)
        self.assertEqual(0, process.returncode, errors.decode(errors="replace"))
        lines = output.decode().splitlines()
        self.assertEqual(process.pid, int(lines[0]))
        self.assertEqual(Path(sys.prefix).resolve(), Path(lines[1]).resolve())
