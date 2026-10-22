"""预算管理API与本机维护入口验证，绝不连接CD2。"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings
from app.main import create_app


class CloudDriveBudgetApiTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / "test.sqlite3"
        settings = AppSettings(data_dir=self.root, database_path=self.database,
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False), cloudDrive=CloudDriveSettings("/cloud/movies"))
        self.client = TestClient(create_app(settings))
        bindCloudConfiguration(settings)
        self.addCleanup(self.client.close)
        self.client.post("/api/auth/bootstrap-admin", json={"username": "admin", "displayName": "test", "password": "TestOnly123!"})
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + response.json()["accessToken"]}

    def test_permission_guard_for_read_and_mutations(self):
        self.assertEqual(401, self.client.get("/api/cloud-drive/budget/status").status_code)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE users SET permissions_json=?", (json.dumps(["scan:run"]),))
            connection.commit()
        for method, path, kwargs in (
            (self.client.get, "/api/cloud-drive/budget/status", {}),
            (self.client.post, "/api/cloud-drive/budget/pause", {}),
            (self.client.post, "/api/cloud-drive/budget/resume", {"json": {"expectedRevision": 0, "confirmRemoteIdle": True}}),
            (self.client.post, "/api/cloud-drive/budget/release-directory-manual-pause", {"json": {
                "expectedRevision": 0, "confirmRemoteIdle": True, "confirmClientsStopped": True}}),
        ):
            self.assertEqual(403, method(path, headers=self.headers, **kwargs).status_code)

    def test_pause_resume_revision_and_audit_without_network(self):
        status = self.client.get("/api/cloud-drive/budget/status", headers=self.headers)
        self.assertEqual(200, status.status_code)
        self.assertNotIn("lease", status.json())
        paused = self.client.post("/api/cloud-drive/budget/pause", headers=self.headers)
        self.assertEqual("paused", paused.json()["state"])
        stale = self.client.post("/api/cloud-drive/budget/resume", headers=self.headers,
            json={"expectedRevision": 0, "confirmRemoteIdle": True})
        self.assertEqual(409, stale.status_code)
        denied = self.client.post("/api/cloud-drive/budget/resume", headers=self.headers,
            json={"expectedRevision": paused.json()["revision"], "confirmRemoteIdle": False})
        self.assertEqual(400, denied.status_code)
        resumed = self.client.post("/api/cloud-drive/budget/resume", headers=self.headers,
            json={"expectedRevision": paused.json()["revision"], "confirmRemoteIdle": True})
        self.assertEqual(200, resumed.status_code)
        self.assertEqual("ready", resumed.json()["state"])
        self.assertFalse(resumed.json()["executionAvailable"])
        with closing(sqlite3.connect(self.database)) as connection:
            actions = connection.execute("SELECT action FROM audit_events WHERE event_type='cloud_drive.budget' ORDER BY id").fetchall()
            scans = connection.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0]
        self.assertEqual([("pause",), ("resume",)], actions)
        self.assertEqual(0, scans)

    def test_strict_payload_and_storage_error_redaction(self):
        for payload in ({"expectedRevision": True, "confirmRemoteIdle": True},
                        {"expectedRevision": 0, "confirmRemoteIdle": 1},
                        {"expectedRevision": 0, "confirmRemoteIdle": True, "token": "synthetic-secret"}):
            response = self.client.post("/api/cloud-drive/budget/resume", headers=self.headers, json=payload)
            self.assertEqual(422, response.status_code)
            self.assertNotIn("synthetic-secret", response.text)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TABLE cloud_drive_budget_pool")
            connection.commit()
        response = self.client.get("/api/cloud-drive/budget/status", headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertNotIn(str(self.database), response.text)

    def test_directory_manual_pause_release_has_separate_strict_confirmed_route(self):
        path = "/api/cloud-drive/budget/release-directory-manual-pause"
        body = {"expectedRevision": 1, "confirmRemoteIdle": True, "confirmClientsStopped": True}
        for payload in ({"expectedRevision": 1, "confirmRemoteIdle": True},
                        {**body, "confirmClientsStopped": 1}, {**body, "extra": "synthetic-secret"}):
            response = self.client.post(path, headers=self.headers, json=payload)
            self.assertEqual(422, response.status_code)
            self.assertNotIn("synthetic-secret", response.text)
        response = self.client.post(path, headers=self.headers, json={**body, "confirmClientsStopped": False})
        self.assertEqual(400, response.status_code)
        with patch("app.api.cloud_drive.releaseDirectoryManualPause", return_value={
                "state": "paused", "pauseReason": "request_incomplete", "directoryExecutionHeld": True}) as release:
            response = self.client.post(path, headers=self.headers, json=body)
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.json()["directoryExecutionHeld"])
            self.assertEqual(1, release.call_count)
            self.assertTrue(release.call_args.kwargs["confirmClientsStopped"])
            self.assertTrue(release.call_args.kwargs["confirmRemoteIdle"])
        paused = self.client.post("/api/cloud-drive/budget/pause", headers=self.headers).json()
        response = self.client.post(path, headers=self.headers, json={**body, "expectedRevision": paused["revision"]})
        self.assertEqual(409, response.status_code)

    def test_cli_default_inspection_is_read_only_and_never_initializes_database(self):
        config = self.root / "config.toml"
        config.write_text(f'[paths]\ndata_dir="{self.root.as_posix()}"\ndatabase_path="{self.database.as_posix()}"\n', encoding="utf-8")
        with closing(sqlite3.connect(self.database)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        result = subprocess.run([sys.executable, "-m", "app.recover_cloud_drive", "--config", str(config)],
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(json.loads(result.stdout)["inFlight"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(before, connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        absent = self.root / "absent.sqlite3"
        config.write_text(f'[paths]\ndata_dir="{self.root.as_posix()}"\ndatabase_path="{absent.as_posix()}"\n', encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "app.recover_cloud_drive", "--config", str(config)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(1, result.returncode)
        self.assertFalse(absent.exists())
