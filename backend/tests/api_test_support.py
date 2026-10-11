"""业务 API 测试使用真实认证，安全边界测试仍使用原生 TestClient。"""

from fastapi.testclient import TestClient

from app.service.auth_service import bootstrap_admin, login, user_count


def authenticatedClient(app, **kwargs) -> TestClient:
    settings = app.state.settings
    if user_count(settings) != 0:
        raise AssertionError("业务测试客户端需要独立的空用户数据库")
    username = "api-review"
    password = "TemporaryApiTest123!"
    bootstrap_admin(settings, username, "API 测试管理员", password)
    session = login(settings, username, password)
    client = TestClient(app, **kwargs)
    client.headers["Authorization"] = f"Bearer {session.access_token}"
    return client
