"""AI provider tests."""

import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from app.service.ai_provider import AiProviderConfig, DeepSeekProvider


class DeepSeekProviderTest(unittest.TestCase):
    def build_provider(self) -> DeepSeekProvider:
        return DeepSeekProvider(
            AiProviderConfig(
                provider="deepseek",
                model="deepseek-chat",
                api_key="sk-secret123456",
                base_url="https://api.deepseek.com/v1",
                timeout_ms=5000,
                max_retries=1,
            )
        )

    def test_connection_posts_minimal_chat_completion_payload(self):
        provider = self.build_provider()

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["content_type"] = request.headers.get("Content-type")
            captured["timeout"] = timeout
            captured["body"] = request.data.decode("utf-8")
            return FakeResponse()

        with patch("app.service.ai_provider.url_request.urlopen", fake_urlopen):
            result = provider.test_connection()

        self.assertEqual("success", result["status"])
        self.assertEqual("https://api.deepseek.com/v1/chat/completions", captured["url"])
        self.assertEqual("Bearer sk-secret123456", captured["authorization"])
        self.assertEqual("application/json", captured["content_type"])
        self.assertEqual(5, captured["timeout"])
        self.assertIn('"model": "deepseek-chat"', captured["body"])
        self.assertIn('"content": "ping"', captured["body"])

    def test_connection_reports_auth_failure(self):
        provider = self.build_provider()

        def fake_urlopen(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with patch("app.service.ai_provider.url_request.urlopen", fake_urlopen):
            result = provider.test_connection()

        self.assertEqual("failed", result["status"])
        self.assertEqual("client", result["error_type"])
        self.assertEqual(401, result["http_status"])
        self.assertEqual("AI 鉴权失败，请检查 API Key 是否正确", result["message"])


if __name__ == "__main__":
    unittest.main()
