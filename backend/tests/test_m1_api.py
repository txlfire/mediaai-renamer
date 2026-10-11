"""M1 媒体扫描 API 测试。"""

import tempfile
import unittest
from pathlib import Path

from api_test_support import authenticatedClient as TestClient

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.logger import shutdown_logging
from app.main import create_app


class M1ApiTest(unittest.TestCase):
    """M1 API 契约测试。"""

    def test_async_scan_accepts_without_network_and_status_is_queryable(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient as RawClient
        from app.service.scan_queue_service import claimScan, executeAcceptedScan
        with tempfile.TemporaryDirectory() as tempDir:
            root = Path(tempDir)
            media = root / "media"
            media.mkdir()
            try:
                app = create_app(self.build_settings(root))
                client = TestClient(app)
                sourceId = client.post("/api/media-sources", json={"name": "async", "path": str(media), "enabled": True}).json()["id"]
                self.assertEqual(401, RawClient(app).post("/api/scan-jobs/async", json={"media_source_id": sourceId}).status_code)
                with patch("app.service.scan_service.get_protocol", side_effect=AssertionError("must not probe")):
                    response = client.post("/api/scan-jobs/async", json={"media_source_id": sourceId})
                self.assertEqual(202, response.status_code)
                self.assertEqual("queued", response.json()["status"])
                jobId = response.json()["id"]
                self.assertEqual("queued", client.get(f"/api/scan-jobs/{jobId}").json()["status"])
                self.assertEqual(400, client.post("/api/scan-jobs", json={"media_source_id": sourceId}).status_code)
                self.assertEqual(400, client.post("/api/scan-jobs/async", json={"media_source_id": sourceId}).status_code)
                claimedId, token = claimScan(app.state.settings)
                self.assertEqual(jobId, claimedId)
                executeAcceptedScan(app.state.settings, jobId, token)
                self.assertEqual("completed", client.get(f"/api/scan-jobs/{jobId}").json()["status"])
                self.assertEqual(404, client.get("/api/scan-jobs/99999").status_code)
                self.assertNotIn("execution_token", response.json())
            finally:
                shutdown_logging()

    def build_settings(self, root: Path) -> AppSettings:
        """构建隔离的测试配置。"""

        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=2, batch_interval_seconds=0),
        )

    def test_media_source_scan_files_and_logs_api(self):
        """API 应支持媒体源、扫描任务、结果和日志读取。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "movie.mkv").write_text("movie", encoding="utf-8")
            (media_dir / "poster.jpg").write_text("poster", encoding="utf-8")

            try:
                app = create_app(self.build_settings(root))
                client = TestClient(app)

                create_response = client.post(
                    "/api/media-sources",
                    json={"name": "电影", "path": str(media_dir), "enabled": True},
                )
                self.assertEqual(200, create_response.status_code)
                source_id = create_response.json()["id"]

                list_response = client.get("/api/media-sources")
                self.assertEqual(200, list_response.status_code)
                self.assertEqual(1, len(list_response.json()))

                directories_response = client.get(
                    "/api/media-sources/local-directories",
                    params={"path": str(root)},
                )
                self.assertEqual(200, directories_response.status_code)
                directory_names = [
                    item["name"] for item in directories_response.json()["entries"]
                ]
                self.assertIn("media", directory_names)

                scan_response = client.post(
                    "/api/scan-jobs", json={"media_source_id": source_id}
                )
                self.assertEqual(202, scan_response.status_code)
                self.assertEqual("queued", scan_response.json()["status"])
                from app.service.scan_queue_service import claimScan, executeAcceptedScan
                jobId, token = claimScan(app.state.settings)
                self.assertEqual(scan_response.json()["id"], jobId)
                executeAcceptedScan(app.state.settings, jobId, token)
                self.assertEqual("completed", client.get(f"/api/scan-jobs/{jobId}").json()["status"])

                auditResponse = client.get("/api/audit-events", params={"event_type": "scan.execute"})
                self.assertEqual(200, auditResponse.status_code)
                scanEvents = auditResponse.json()["items"]
                self.assertEqual(4, len(scanEvents))
                self.assertTrue(all(event["actorName"] == "api-review" for event in scanEvents))
                self.assertTrue(all(event["targetId"] == str(scan_response.json()["id"]) for event in scanEvents))
                self.assertEqual({"pending", "success"}, {event["result"] for event in scanEvents})
                self.assertEqual(
                    {"accept_scan", "claim_scan", "start_scan", "complete_scan"},
                    {event["action"] for event in scanEvents},
                )

                files_response = client.get("/api/media-files")
                self.assertEqual(200, files_response.status_code)
                self.assertEqual(1, len(files_response.json()))
                self.assertEqual("movie.mkv", files_response.json()[0]["file_name"])

                filtered_jobs_response = client.get(
                    "/api/scan-jobs", params={"media_source_id": source_id}
                )
                self.assertEqual(200, filtered_jobs_response.status_code)
                self.assertEqual(1, len(filtered_jobs_response.json()))

                filtered_files_response = client.get(
                    "/api/media-files", params={"scan_job_id": scan_response.json()["id"]}
                )
                self.assertEqual(200, filtered_files_response.status_code)
                self.assertEqual(1, len(filtered_files_response.json()))

                logs_response = client.get("/api/logs")
                self.assertEqual(200, logs_response.status_code)
                self.assertIn("items", logs_response.json())

                export_response = client.get("/api/logs/export")
                self.assertEqual(200, export_response.status_code)
                self.assertIn("text/plain", export_response.headers["content-type"])
            finally:
                shutdown_logging()

    def test_create_unc_media_source_api_masks_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            try:
                app = create_app(self.build_settings(root))
                client = TestClient(app)

                response = client.post(
                    "/api/media-sources",
                    json={
                        "name": "NAS",
                        "path": r"\\nas\media",
                        "path_type": "unc",
                        "username": "admin",
                        "secret": "password",
                    },
                )

                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual("unc", payload["path_type"])
                self.assertEqual("smb", payload["protocol"])
                self.assertEqual("admin", payload["username"])
                self.assertTrue(payload["has_secret"])
                self.assertNotIn("password", str(payload))
            finally:
                shutdown_logging()

    def test_shared_protocol_capabilities_and_connection_test_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            try:
                app = create_app(self.build_settings(root))
                client = TestClient(app)

                capabilities_response = client.get("/api/shared-protocols")
                self.assertEqual(200, capabilities_response.status_code)
                protocols = {item["protocol"] for item in capabilities_response.json()}
                self.assertIn("local", protocols)
                self.assertIn("unc", protocols)
                self.assertIn("mounted_nfs", protocols)
                self.assertIn("webdav", protocols)

                create_response = client.post(
                    "/api/media-sources",
                    json={
                        "name": "电影",
                        "path": str(media_dir),
                        "path_type": "local",
                    },
                )
                self.assertEqual(200, create_response.status_code)
                source_id = create_response.json()["id"]

                test_response = client.post(f"/api/media-sources/{source_id}/test-connection")
                self.assertEqual(200, test_response.status_code)
                self.assertTrue(test_response.json()["success"])
                self.assertEqual("连接成功", test_response.json()["message"])
            finally:
                shutdown_logging()

    def test_webdav_conditional_move_verification_api_returns_explicit_status(self):
        from unittest.mock import patch
        from app.service.shared_protocols.webdav import WebDavCapabilityVerification

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            try:
                app = create_app(self.build_settings(root))
                client = TestClient(app)
                created = client.post(
                    "/api/media-sources",
                    json={
                        "name": "WebDAV",
                        "path": "https://nas.invalid/dav/",
                        "path_type": "webdav",
                    },
                )
                sourceId = created.json()["id"]
                with patch(
                    "app.api.media_sources.verifyWebDavConditionalMove",
                    return_value=WebDavCapabilityVerification(
                        status="unsupported",
                        message="服务端忽略错误 ETag",
                        suggestion="保持只读",
                        cleanup_succeeded=True,
                        evidence={"wrong_etag_status": 201},
                    ),
                ):
                    response = client.post(f"/api/media-sources/{sourceId}/verify-conditional-move")
                self.assertEqual(200, response.status_code)
                self.assertEqual("unsupported", response.json()["status"])
                self.assertTrue(response.json()["cleanup_succeeded"])
                self.assertEqual(201, response.json()["evidence"]["wrong_etag_status"])
            finally:
                shutdown_logging()


if __name__ == "__main__":
    unittest.main()
