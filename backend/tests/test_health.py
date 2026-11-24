"""健康检查接口测试。

验证前端和部署平台依赖的 /api/health 接口契约。
"""

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.logger import shutdown_logging
from app.main import create_app


class HealthEndpointTest(unittest.TestCase):
    """健康检查接口测试用例。"""

    def test_health_endpoint_reports_app_status(self):
        """健康检查接口应返回应用名称、版本和 ok 状态。"""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = replace(AppSettings(), data_dir=root / "data", database_path=root / "data" / "mediaai.sqlite3",
                               logging=LoggingSettings(log_dir=root / "logs"))
            try:
                response = TestClient(create_app(settings)).get("/api/health")
            finally:
                shutdown_logging()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "app": "MediaAI Renamer",
                "version": AppSettings.version,
                "status": "ok",
            },
            response.json(),
        )

    def test_importing_factory_leaves_default_instance_untouched(self):
        """直接导入工厂不创建数据库；部署入口仍可正常初始化并复用应用。"""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_text('[paths]\ndata_dir="data"\n[logging]\nlog_dir="logs"\n', encoding="utf-8")
            environment = {**os.environ, "MEDIAAI_CONFIG_PATH": str(config),
                           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            subprocess.run([sys.executable, "-c", """
from pathlib import Path
from app.main import create_app
from uvicorn.importer import import_from_string

assert callable(create_app)
assert not Path('data').exists()
assert not Path('logs').exists()
application = import_from_string('app.main:app')
assert application is import_from_string('app.main:app')
assert application.state.settings.database_path.resolve() == Path.cwd() / 'data' / 'mediaai.sqlite3'
assert application.state.settings.database_path.is_file()
"""], cwd=root, env=environment, timeout=30, check=True)


if __name__ == "__main__":
    unittest.main()
