"""AI provider tests."""

import unittest
import json
from urllib.error import HTTPError
from unittest.mock import patch

from app.service.ai_provider import AiProviderConfig, DeepSeekProvider, MAX_AI_RESPONSE_BYTES
from app.service.ai_diagnostics import AiCallContext


class FakeHttpResponse:
    status = 200

    def __init__(self, body: bytes):
        self.body = body
        self.readSizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        self.readSizes.append(size)
        return self.body[:size] if size >= 0 else self.body


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

            def read(self, size=-1):
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
        self.assertAlmostEqual(5, captured["timeout"], delta=0.1)
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

    def test_invalid_encoding_json_and_structure_return_safe_failure(self):
        for body in (b"\xff", b"<html>synthetic-secret</html>", b"", b"[]", b"{}", b'{"choices":{}}',
                     b'{"choices":[{"message":{"content":42}}]}', b'{"choices":[{"message":{"content":""}}]}'):
            with self.subTest(body=body):
                provider = self.build_provider()
                with patch("app.service.ai_provider.url_request.urlopen", return_value=FakeHttpResponse(body)):
                    result = provider.complete_chat([{"role": "user", "content": "private prompt"}], 500, 0)
                self.assertEqual("failed", result["status"])
                self.assertEqual("invalid_response", result["error_type"])
                self.assertNotIn("synthetic-secret", str(result))

    def test_oversized_response_is_bounded_and_rejected(self):
        response = FakeHttpResponse(b"x" * (MAX_AI_RESPONSE_BYTES + 10))
        with patch("app.service.ai_provider.url_request.urlopen", return_value=response):
            result = self.build_provider().test_connection()
        self.assertEqual("invalid_response", result["error_type"])
        self.assertEqual([MAX_AI_RESPONSE_BYTES + 1], response.readSizes)

    def test_llm_summary_is_correlated_and_does_not_contain_prompt_response_or_key(self):
        provider = self.build_provider()
        from dataclasses import replace
        provider.config = replace(provider.config, callContext=AiCallContext("request-1", "rename_preview", 17))
        body = json.dumps({"choices": [{"message": {"content": "private response"}}], "usage": {
            "total_tokens": 12, "prompt_tokens": "synthetic-secret", "api_key": "synthetic-secret",
        }}).encode()
        with patch("app.service.ai_provider.url_request.urlopen", return_value=FakeHttpResponse(body)), \
             patch("app.service.ai_diagnostics.get_llm_logger") as logger:
            result = provider.complete_chat([{"role": "user", "content": "private prompt /private/path"}], 500, 0)
        self.assertEqual("success", result["status"])
        events = [json.loads(call.args[1]) for call in logger.return_value.info.call_args_list]
        self.assertEqual(["started", "success"], [event["status"] for event in events])
        self.assertTrue(all(event["request_id"] == "request-1" and event["source_record_id"] == 17 for event in events))
        self.assertEqual({"total_tokens": 12}, events[-1]["usage"])
        for secret in ("synthetic-secret", "private prompt", "private response", "/private/path", provider.config.api_key):
            self.assertNotIn(secret, str(events))

    def test_summary_failure_does_not_change_success_or_timeout_result(self):
        with patch("app.service.ai_diagnostics.get_llm_logger", side_effect=OSError("log unavailable")):
            with patch("app.service.ai_provider.url_request.urlopen", return_value=FakeHttpResponse(b'{"choices":[{"message":{"content":"ok"}}]}')):
                self.assertEqual("success", self.build_provider().test_connection()["status"])
            with patch("app.service.ai_provider.url_request.urlopen", side_effect=TimeoutError("private timeout value")):
                self.assertEqual("timeout", self.build_provider().test_connection()["error_type"])

    def test_program_error_keeps_safe_frames_and_propagates_original_exception(self):
        with patch("app.service.ai_provider.url_request.urlopen", side_effect=TypeError("synthetic-secret")), \
             patch("app.service.ai_diagnostics.get_llm_logger") as logger:
            with self.assertRaisesRegex(TypeError, "synthetic-secret"):
                self.build_provider().test_connection()
        event = json.loads(logger.return_value.info.call_args_list[-1].args[1])
        self.assertEqual("internal", event["error_type"])
        self.assertEqual("TypeError", event["exception_type"])
        self.assertTrue(event["frames"])
        self.assertNotIn("synthetic-secret", str(event))

    def test_retry_count_zero_one_and_n_only_replays_explicit_rate_limit(self):
        from dataclasses import replace
        for retries in (0, 1, 3):
            with self.subTest(retries=retries):
                provider = self.build_provider()
                provider.config = replace(provider.config, max_retries=retries)
                with patch("app.service.ai_provider.url_request.urlopen", side_effect=HTTPError("synthetic", 429, "limited", {"Retry-After": "0"}, None)) as transport, \
                        patch("app.service.ai_provider.time.sleep") as sleep:
                    result = provider.complete_chat([], 1, 0)
                self.assertEqual(retries + 1, transport.call_count)
                self.assertEqual(retries, sleep.call_count)
                self.assertEqual(retries + 1, result["attempts"])

    def test_timeout_network_auth_and_server_failure_are_not_replayed(self):
        from urllib.error import URLError
        for error in (TimeoutError(), URLError("network"), HTTPError("synthetic", 401, "auth", {}, None),
                      HTTPError("synthetic", 503, "server", {}, None)):
            with self.subTest(error=type(error).__name__):
                with patch("app.service.ai_provider.url_request.urlopen", side_effect=error) as transport, \
                        patch("app.service.ai_provider.time.sleep") as sleep:
                    result = self.build_provider().complete_chat([], 1, 0)
                self.assertEqual(1, transport.call_count)
                self.assertEqual(1, result["attempts"])
                sleep.assert_not_called()

    def test_retry_after_cannot_exceed_total_budget(self):
        with patch("app.service.ai_provider.url_request.urlopen", side_effect=HTTPError("synthetic", 429, "limited", {"Retry-After": "120"}, None)) as transport, \
                patch("app.service.ai_provider.time.sleep") as sleep:
            result = self.build_provider().test_connection()
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(1, transport.call_count)
        sleep.assert_not_called()

    def test_retry_wait_consumes_the_shared_request_budget(self):
        clock = [100.0]
        timeouts = []
        calls = []
        def transport(request, timeout):
            timeouts.append(timeout)
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError("synthetic", 429, "limited", {"Retry-After": "2"}, None)
            return FakeHttpResponse(b'{"choices":[{"message":{"content":"ok"}}]}')
        with patch("app.service.ai_provider.time.perf_counter", side_effect=lambda: clock[0]), \
                patch("app.service.ai_provider.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                patch("app.service.ai_provider.url_request.urlopen", side_effect=transport):
            result = self.build_provider().complete_chat([], 1, 0)
        self.assertEqual([5.0, 3.0], timeouts)
        self.assertEqual("success", result["status"])
        self.assertEqual(2000, result["response_ms"])

    def test_retry_after_http_date_and_invalid_header(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        from app.service.ai_provider import _retryAfterSeconds
        self.assertIsNone(_retryAfterSeconds("invalid-header"))
        self.assertIsNone(_retryAfterSeconds("-2"))
        self.assertEqual(0, _retryAfterSeconds("0"))
        wait = _retryAfterSeconds(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5)))
        self.assertGreater(wait, 3)
        self.assertLessEqual(wait, 5)
        self.assertEqual(float("inf"), _retryAfterSeconds("9" * 100))

    def test_late_success_is_not_accepted_after_budget_expires(self):
        clock = [100.0]
        class LateResponse(FakeHttpResponse):
            def read(self, size=-1):
                clock[0] += 6
                return super().read(size)
        with patch("app.service.ai_provider.time.perf_counter", side_effect=lambda: clock[0]), \
                patch("app.service.ai_provider.url_request.urlopen", return_value=LateResponse(b'{"choices":[{"message":{"content":"ok"}}]}')) as transport:
            result = self.build_provider().complete_chat([], 1, 0)
        self.assertEqual("timeout", result["error_type"])
        transport.assert_called_once()

    def test_retry_logs_share_request_id_and_record_actual_attempts(self):
        from dataclasses import replace
        provider = self.build_provider()
        provider.config = replace(provider.config, callContext=AiCallContext("retry-request", "rename_preview", 17))
        with patch("app.service.ai_provider.url_request.urlopen", side_effect=[
            HTTPError("synthetic", 429, "limited", {"Retry-After": "0"}, None),
            FakeHttpResponse(b'{"choices":[{"message":{"content":"ok"}}]}')]), \
                patch("app.service.ai_provider.time.sleep"), patch("app.service.ai_diagnostics.get_llm_logger") as logger:
            result = provider.complete_chat([], 1, 0)
        self.assertEqual("success", result["status"])
        events = [json.loads(call.args[1]) for call in logger.return_value.info.call_args_list]
        self.assertTrue(all(event["request_id"] == "retry-request" for event in events))
        self.assertEqual([1, 2], [event["attempt"] for event in events if event["status"] == "started"])


if __name__ == "__main__":
    unittest.main()
