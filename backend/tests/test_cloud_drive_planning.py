"""云盘离线范围、配置及接口测试，不连接真实云服务。"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings, load_settings, cloudDriveSettingsFromDict
from cloud_configuration_fixture import bindCloudConfiguration
from app.main import create_app
from app.service.cloud_drive_planning_service import buildCloudScanAdvice, cloudDrivePlanningStatus
from app.utils.cloud_drive_paths import approvedCloudRoot, canonicalCloudPath


class CloudDrivePlanningTest(unittest.TestCase):
    def setUp(self):
        self.settings = CloudDriveSettings("/cloud/movies")

    def test_defaults_disabled_and_config_requires_limited_root(self):
        self.assertFalse(cloudDrivePlanningStatus(CloudDriveSettings())["planningAvailable"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text('[cloud_drive]\napproved_root="/cloud/movies/"\n', encoding="utf-8")
            self.assertEqual(CloudDriveSettings(), load_settings(path).cloudDrive)
            for value in ('approved_root="/"', 'approved_root="/cloud"', 'approved_root=true',
                          'token="not-a-real-token"', 'approved_root=[]', 'min_interval_seconds=1',
                          'min_interval_seconds=true', 'failure_cooldown_seconds=0', 'failure_cooldown_seconds=60.5'):
                with self.subTest(value=value):
                    path.write_text("[cloud_drive]\n" + value, encoding="utf-8")
                    self.assertEqual(CloudDriveSettings(), load_settings(path).cloudDrive)
                    import tomllib
                    with self.assertRaises(ValueError):
                        cloudDriveSettingsFromDict(tomllib.loads(path.read_text(encoding="utf-8"))["cloud_drive"])

    def test_path_rejects_traversal_and_preserves_cloud_identity(self):
        for value in ("relative", "/a/../b", "/a/./b", "/a//b", "//a/b", "/a/\\b",
                      "/a/\x00b", "/a/%2fb", "/a/%252fb", "/a/%2eb", "/a/\ud800", "/a/" + "中" * 1400):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    canonicalCloudPath(value)
        self.assertEqual("/cloud/电影/欧美", approvedCloudRoot("/cloud/电影/欧美/"))
        self.assertNotEqual(canonicalCloudPath("/a/B"), canonicalCloudPath("/a/b"))
        self.assertNotEqual(canonicalCloudPath("/a/é"), canonicalCloudPath("/a/e\u0301"))

    def test_subtree_collapses_parent_child_and_keeps_parent_direct_files(self):
        advice = buildCloudScanAdvice(self.settings,
            ["/cloud/movies/欧美", "/cloud/movies", "/cloud/movies/欧美"],
            ["/cloud/movies/日韩", "/cloud/movies/日韩/2025"], "subtrees")
        self.assertEqual([{"path": "/cloud/movies", "recursive": True, "includeDirectFiles": True}], advice["scopes"])
        self.assertEqual(["/cloud/movies/日韩"], advice["excludedPaths"])
        self.assertEqual("coveredByParent", advice["omittedSelections"][0]["reason"])
        self.assertIsNone(advice["estimatedEntries"])
        self.assertIsNone(advice["estimatedDirectories"])
        self.assertFalse(advice["executionAvailable"])
        self.assertFalse(advice["networkAccess"])
        self.assertEqual(0, advice["suggestedBudget"]["contentBytes"])

    def test_shallow_parent_does_not_cover_childs_own_listing(self):
        advice = buildCloudScanAdvice(self.settings,
            ["/cloud/movies", "/cloud/movies/欧美"], [], "overview")
        self.assertEqual(2, len(advice["scopes"]))
        self.assertTrue(all(not scope["recursive"] for scope in advice["scopes"]))

    def test_exclusions_remove_whole_scope_without_escaping_selected_range(self):
        advice = buildCloudScanAdvice(self.settings,
            ["/cloud/movies/欧美", "/cloud/movies/大陆"], ["/cloud/movies/欧美"], "subtrees")
        self.assertEqual("/cloud/movies/大陆", advice["scopes"][0]["path"])
        self.assertEqual("excluded", advice["omittedSelections"][0]["reason"])
        for selected, excluded in ((["/cloud/movies2"], []), (["/cloud"], []),
                                   (["/cloud/movies"], ["/cloud/movies"]),
                                   (["/cloud/movies/欧美"], ["/cloud/movies/日韩"]),
                                   (["/cloud/movies"], ["/other"]), ([], [])):
            with self.subTest(selected=selected, excluded=excluded):
                with self.assertRaises(ValueError):
                    buildCloudScanAdvice(self.settings, selected, excluded, "subtrees")

    def test_large_selection_rejected_before_traversal(self):
        with self.assertRaises(ValueError):
            buildCloudScanAdvice(self.settings, [f"/cloud/movies/{i}" for i in range(101)], [], "subtrees")
        with self.assertRaises(ValueError):
            buildCloudScanAdvice(self.settings, ["/cloud/movies"], [], "invalid")


class CloudDrivePlanningApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.databasePath = root / "test.sqlite3"
        settings = AppSettings(data_dir=root, database_path=self.databasePath,
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            cloudDrive=CloudDriveSettings("/cloud/movies"))
        self.client = TestClient(create_app(settings))
        bindCloudConfiguration(settings)
        self.addCleanup(self.client.close)
        self.client.post("/api/auth/bootstrap-admin", json={"username": "admin", "displayName": "test", "password": "TestOnly123!"})
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "TestOnly123!"})
        self.headers = {"Authorization": f"Bearer {response.json()['accessToken']}"}

    def test_authentication_and_permission_guard(self):
        self.assertEqual(401, self.client.get("/api/cloud-drive/status").status_code)
        with closing(sqlite3.connect(self.databasePath)) as connection:
            connection.execute("UPDATE users SET permissions_json = ?", (json.dumps(["source:write"]),))
            connection.commit()
        for method, path, kwargs in ((self.client.get, "/api/cloud-drive/status", {}),
                                    (self.client.post, "/api/cloud-drive/scan-advice", {"json": {"selectedPaths": ["/cloud/movies"]}})):
            self.assertEqual(403, method(path, headers=self.headers, **kwargs).status_code)

    def test_api_is_offline_and_does_not_create_job(self):
        with closing(sqlite3.connect(self.databasePath)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0]
        with patch("socket.create_connection", side_effect=AssertionError("不应联网")):
            status = self.client.get("/api/cloud-drive/status", headers=self.headers)
            response = self.client.post("/api/cloud-drive/scan-advice", headers=self.headers,
                                       json={"selectedPaths": ["/cloud/movies/欧美"], "mode": "subtrees"})
        self.assertEqual(200, status.status_code)
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()["executionAvailable"])
        with closing(sqlite3.connect(self.databasePath)) as connection:
            self.assertEqual(before, connection.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0])

    def test_invalid_payload_and_disabled_config(self):
        for payload, code in (({"selectedPaths": ["/cloud/movies2"]}, 400),
                              ({"selectedPaths": ["/cloud/movies"], "token": "not-a-real-token"}, 422),
                              ({"selectedPaths": [1]}, 422),
                              ({"selectedPaths": ["/cloud/movies"] * 101}, 422)):
            self.assertEqual(code, self.client.post("/api/cloud-drive/scan-advice", headers=self.headers, json=payload).status_code)
        bindCloudConfiguration(AppSettings(cloudDrive=CloudDriveSettings(), database_path=self.databasePath))
        self.assertEqual(409, self.client.post("/api/cloud-drive/scan-advice", headers=self.headers,
                                            json={"selectedPaths": ["/cloud/movies"]}).status_code)
