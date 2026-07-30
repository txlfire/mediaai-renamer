"""前端静态资源托管测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.main import create_app
from app.web import resolve_frontend_dir


class FrontendHostingTest(unittest.TestCase):
    """验证单容器中的页面、API 与静态资源路由。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.frontend_dir = self.root / "frontend-dist"
        (self.frontend_dir / "assets").mkdir(parents=True)
        (self.frontend_dir / "index.html").write_text(
            "<html><title>MediaAI Renamer</title></html>",
            encoding="utf-8",
        )
        (self.frontend_dir / "assets" / "app.js").write_text(
            "console.log('ok')",
            encoding="utf-8",
        )
        settings = AppSettings(
            data_dir=self.root / "data",
            database_path=self.root / "data" / "mediaai.sqlite3",
            logging=LoggingSettings(
                log_dir=self.root / "logs",
                console_output=False,
            ),
        )
        self.client = TestClient(
            create_app(settings, frontend_dir=self.frontend_dir)
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_serves_frontend_index(self):
        """根路径应返回构建后的 Vue 首页。"""

        response = self.client.get("/")

        self.assertEqual(200, response.status_code)
        self.assertIn("MediaAI Renamer", response.text)

    def test_falls_back_to_index_for_spa_route(self):
        """不存在扩展名的前端路由应回退到首页。"""

        response = self.client.get("/settings/scraping")

        self.assertEqual(200, response.status_code)
        self.assertIn("MediaAI Renamer", response.text)

    def test_keeps_api_routes_ahead_of_frontend_mount(self):
        """API 路由优先级不能被根路径静态挂载覆盖。"""

        response = self.client.get("/api/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["status"])

    def test_serves_existing_static_asset(self):
        """存在的静态文件应按原内容返回。"""

        response = self.client.get("/assets/app.js")

        self.assertEqual(200, response.status_code)
        self.assertEqual("console.log('ok')", response.text)

    def test_returns_404_for_missing_static_asset(self):
        """缺失的脚本或样式不能错误返回 index.html。"""

        response = self.client.get("/assets/missing.js")

        self.assertEqual(404, response.status_code)

    def test_starts_without_frontend_build(self):
        """开发和后端测试环境缺少前端目录时仍应启动 API。"""

        missing_dir = self.root / "missing"
        settings = AppSettings(
            data_dir=self.root / "api-only-data",
            database_path=self.root / "api-only-data" / "mediaai.sqlite3",
            logging=LoggingSettings(
                log_dir=self.root / "api-only-logs",
                console_output=False,
            ),
        )
        client = TestClient(create_app(settings, frontend_dir=missing_dir))

        self.assertEqual(200, client.get("/api/health").status_code)
        self.assertEqual(404, client.get("/").status_code)

    def test_default_frontend_dir_is_relative_to_release_root(self):
        """统一 ZIP 未设置环境变量时应从发布包根目录加载页面。"""

        with patch.dict("os.environ", {}, clear=True):
            directory = resolve_frontend_dir()

        expected = Path(__file__).resolve().parents[2] / "frontend-dist"
        self.assertEqual(expected, directory)


if __name__ == "__main__":
    unittest.main()
