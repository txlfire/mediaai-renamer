"""外部响应诊断使用合成数据，不请求真实账号或媒体目录。"""

import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.schema.media import ParsedMediaName
from app.service.tmdb_client import TmdbClient, InvalidTmdbResponseError
from app.service.shared_protocols.webdav import _propfind, _propfind_optional
from app.service.metadata_provider_registry import TmdbMetadataProvider
from app.core.config import AppSettings


class TransportDiagnosticsTest(unittest.TestCase):
    def test_auth_fallback_is_diagnosed_but_program_errors_are_not_silently_retried(self):
        effective = {"tmdb.v4_token": "synthetic-token", "tmdb.api_key": "synthetic-key"}
        parsed = ParsedMediaName("movie", "synthetic", 2024, None, None)
        with patch("app.service.metadata_provider_registry.get_effective_settings", return_value=effective), \
             patch("app.service.metadata_provider_registry.TmdbClient") as client, \
             patch("app.service.tmdb_client.get_logger") as logger:
            client.return_value.search.side_effect = [RuntimeError("synthetic-secret"), []]
            self.assertEqual([], TmdbMetadataProvider(AppSettings(), 1).search(parsed))
            self.assertEqual(2, client.call_count)
            self.assertIn("v4_to_v3", str(logger.mock_calls))
            self.assertNotIn("synthetic-secret", str(logger.mock_calls))
        with patch("app.service.metadata_provider_registry.get_effective_settings", return_value=effective), \
             patch("app.service.metadata_provider_registry.TmdbClient") as client:
            client.return_value.search.side_effect = TypeError("program failure")
            with self.assertRaises(TypeError):
                TmdbMetadataProvider(AppSettings(), 1).search(parsed)
            self.assertEqual(1, client.call_count)

    def test_tmdb_detail_degradation_preserves_candidate_and_safe_reason(self):
        parsed = ParsedMediaName("movie", "synthetic", 2024, None, None)
        for exception, errorType in ((RuntimeError("private error"), "request"),
                                     (HTTPError("https://example.invalid/?api_key=synthetic-secret", 429, "limit", {}, None), "rate_limit"),
                                     (TypeError("private programming error"), "internal")):
            with self.subTest(errorType=errorType):
                client = TmdbClient(api_key="synthetic-secret")
                with patch.object(client, "_get_json", side_effect=[{"results": [{"id": 7, "title": "synthetic", "release_date": "2024-01-01"}]}, exception]), \
                     patch("app.service.tmdb_client.get_logger") as logger:
                    candidates = client.search(parsed)
                self.assertEqual(1, len(candidates))
                diagnostic = candidates[0].raw_data["metadata_diagnostics"][0]
                self.assertEqual(errorType, diagnostic["error_type"])
                self.assertEqual("v3", diagnostic["channel"])
                self.assertEqual(7, diagnostic["provider_id"])
                self.assertNotIn("synthetic-secret", str(diagnostic) + str(logger.mock_calls))
                if errorType == "internal":
                    self.assertTrue(diagnostic["frames"])

    def test_tmdb_missing_id_does_not_report_false_detail_failure(self):
        client = TmdbClient()
        with patch("app.service.tmdb_client.get_logger") as logger:
            self.assertEqual({}, client._detail_for_item({"title": "no detail id"}, "movie"))
        logger.assert_not_called()

    def test_tmdb_invalid_bytes_and_json_are_classified_without_body(self):
        for body in (b"\xff", b"private invalid body", b"[]"):
            with self.subTest(body=body):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = body
                with patch("app.service.tmdb_client.urlopen", return_value=response):
                    with self.assertRaises(InvalidTmdbResponseError) as exc:
                        TmdbClient(api_key="synthetic-secret")._get_json("/configuration", {})
                self.assertNotIn("private invalid body", str(exc.exception))

    def test_webdav_invalid_xml_or_namespace_is_safe_value_error(self):
        for function in (_propfind, _propfind_optional):
            for body in (b"\xff", b"private invalid body", b"<html>synthetic-secret</html>", b"<multistatus/>", b""):
                with self.subTest(function=function.__name__, body=body):
                    response = MagicMock()
                    response.__enter__.return_value.read.return_value = body
                    with patch("app.service.shared_protocols.webdav._request", return_value=response), \
                         patch("app.service.shared_protocols.webdav.get_logger") as logger:
                        with self.assertRaisesRegex(ValueError, "服务响应格式无效") as exc:
                            function("https://example.invalid/dav/", None)
                    self.assertNotIn("synthetic-secret", str(exc.exception) + str(logger.mock_calls))

    def test_webdav_valid_empty_dav_response_and_logging_failure_are_safe(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'<d:multistatus xmlns:d="DAV:"/>'
        with patch("app.service.shared_protocols.webdav._request", return_value=response):
            self.assertEqual("{DAV:}multistatus", _propfind("https://example.invalid/dav/", None).tag)
        response.__enter__.return_value.read.return_value = b"invalid"
        with patch("app.service.shared_protocols.webdav._request", return_value=response), \
             patch("app.service.shared_protocols.webdav.get_logger", side_effect=OSError("log unavailable")):
            with self.assertRaisesRegex(ValueError, "服务响应格式无效"):
                _propfind("https://example.invalid/dav/", None)
