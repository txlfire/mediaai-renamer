"""健康检查接口测试。

验证前端和部署平台依赖的 /api/health 接口契约。
"""

import unittest

from fastapi.testclient import TestClient

from app.main import create_app


class HealthEndpointTest(unittest.TestCase):
    """健康检查接口测试用例。"""

    def test_health_endpoint_reports_app_status(self):
        """健康检查接口应返回应用名称、版本和 ok 状态。"""

        app = create_app()
        client = TestClient(app)

        response = client.get("/api/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "app": "MediaAI Renamer",
                "version": "0.5.2",
                "status": "ok",
            },
            response.json(),
        )


if __name__ == "__main__":
    unittest.main()
