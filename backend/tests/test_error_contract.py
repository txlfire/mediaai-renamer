"""统一 HTTP 错误响应与未预期异常诊断。"""

import tempfile
import unittest
from pathlib import Path

from api_test_support import authenticatedClient

from app.core.config import AppSettings, LoggingSettings
from app.core.logger import shutdown_logging
from app.main import create_app


class ErrorContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(shutdown_logging)
        self.root = Path(self.temp.name)
        self.settings = AppSettings(
            data_dir=self.root / "data",
            database_path=self.root / "data" / "test.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
        )
        self.app = create_app(self.settings)
        self.client = authenticatedClient(self.app, raise_server_exceptions=False)

    def test_validation_errors_use_same_string_detail_contract(self):
        for path in (
            "/api/scan-jobs/page?page_size=500",
            "/api/rename-previews/page?page_size=500",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(422, response.status_code)
                self.assertEqual({"detail"}, set(response.json()))
                self.assertIsInstance(response.json()["detail"], str)
                self.assertIn("page_size", response.json()["detail"])

    def test_framework_http_errors_keep_string_detail(self):
        response = self.client.get("/api/not-found")

        self.assertEqual(404, response.status_code)
        self.assertEqual({"detail"}, set(response.json()))
        self.assertIsInstance(response.json()["detail"], str)

    def test_unexpected_error_returns_correlation_id_and_writes_error_log(self):
        def explode():
            raise RuntimeError("synthetic unexpected failure")

        self.app.add_api_route("/api/test-unexpected", explode, methods=["GET"])
        response = self.client.get("/api/test-unexpected")

        self.assertEqual(500, response.status_code)
        correlationId = response.headers["x-correlation-id"]
        self.assertRegex(correlationId, r"^[0-9a-f]{32}$")
        self.assertEqual(f"服务器内部错误，关联ID：{correlationId}", response.json()["detail"])
        errorText = (self.root / "logs" / "error.log").read_text(encoding="utf-8")
        self.assertIn(f"correlation_id={correlationId}", errorText)
        self.assertIn("error_type=RuntimeError", errorText)
        self.assertNotIn("synthetic unexpected failure", response.text)


if __name__ == "__main__":
    unittest.main()
