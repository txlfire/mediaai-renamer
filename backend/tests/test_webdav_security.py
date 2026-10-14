"""WebDAV 认证与路径边界回归，仅使用合成凭据。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from dataclasses import replace
from xml.etree import ElementTree as ET
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from app.service.shared_protocols.base import SharedPathContext
from app.service.shared_protocols.base import ConnectionTestResult, RemoteFileEntry
from app.service.shared_protocols.webdav import (
    WebDavProtocol, _confined_url, _normalize_webdav_url, _request,
    _url_from_href, _webdav_error_message, urlopen,
)


class WebDavSecurityTest(unittest.TestCase):
    def setUp(self):
        self.rootUrl = "https://nas.invalid/dav/"
        self.context = SharedPathContext(
            path_type="webdav", username="synthetic-user", secret="synthetic-password",
            root_url=self.rootUrl, conditional_move_status="verified",
        )

    def test_invalid_urls_are_rejected_without_echoing_secrets(self):
        cases = (
            "https://user:synthetic-password@nas.invalid/dav/",
            "https://nas.invalid/dav/?token=synthetic-password",
            "https://nas.invalid/dav/#fragment",
            "https://nas.invalid:bad/dav/",
            "https://nas.invalid/dav/../outside",
            "https://nas.invalid/dav/%2e%2e/outside",
            "https://nas.invalid/dav/%252e%252e/outside",
            "https://nas.invalid/dav/name%2ffile",
            "https://nas.invalid/dav/name%5cfile",
            "https://nas.invalid/dav/name\\file",
            "https://nas.invalid/dav/name\nfile",
            "https://nas.invalid/dav/%00file",
        )
        for value in cases:
            with self.subTest(value=value):
                result = WebDavProtocol().validate_config(value)
                self.assertFalse(result.success)
                self.assertNotIn("synthetic-password", result.message + (result.suggestion or ""))

    def test_destination_variants_are_confined_and_conflicts_never_sent(self):
        for key in ("destination", "DESTINATION", "Destination"):
            with self.subTest(key=key), patch("app.service.shared_protocols.webdav.urlopen") as send:
                with self.assertRaises(ValueError):
                    _request(self.rootUrl + "source", "MOVE", self.context,
                             extra_headers={key: "https://other.invalid/target"})
                send.assert_not_called()
        with patch("app.service.shared_protocols.webdav.urlopen") as send:
            with self.assertRaisesRegex(ValueError, "重复"):
                _request(self.rootUrl + "source", "MOVE", self.context,
                         extra_headers={"destination": self.rootUrl + "a", "Destination": self.rootUrl + "b"})
            send.assert_not_called()
            _request(self.rootUrl + "source", "MOVE", self.context,
                     extra_headers={"destination": self.rootUrl + "a", "Destination": self.rootUrl + "a"})
            request = send.call_args.args[0]
            self.assertEqual(self.rootUrl + "a", request.get_header("Destination"))

    def test_https_and_chinese_paths_remain_supported(self):
        value = _normalize_webdav_url(self.rootUrl + "电影/我的电影.mkv")
        self.assertIn("%E7%94%B5", value)
        self.assertEqual(value, _normalize_webdav_url(value))
        self.assertFalse(WebDavProtocol().validate_config("http://nas.invalid/dav/").success)

    def test_mixed_destination_values_fail_before_request_construction(self):
        headers = {"Destination": self.rootUrl + "safe", "destination": "https://other.invalid/unsafe"}
        for entries in (headers, dict(reversed(list(headers.items())))):
            with self.subTest(headers=list(entries)), patch(
                "app.service.shared_protocols.webdav.Request"
            ) as construct, patch("app.service.shared_protocols.webdav.urlopen") as send:
                with self.assertRaises(ValueError):
                    _request(self.rootUrl + "source", "MOVE", self.context, extra_headers=entries)
                construct.assert_not_called()
                send.assert_not_called()

    def _runCapabilityVerification(self, *, ignoreWrongEtag=False, cleanupStatus=204):
        protocol = WebDavProtocol()
        files: dict[str, RemoteFileEntry] = {}
        collections: set[str] = set()

        def info(path, _context):
            if path not in files:
                raise ValueError("WebDAV 源文件不存在")
            return files[path]

        def optional(path, _context):
            return object() if path in files or path in collections else None

        def status(path, method, _context, *, body=None, headers=None):
            if method == "MKCOL":
                collections.add(path)
                return 201
            if method == "PUT":
                version = f'"etag-{len(files) + 1}"'
                files[path] = RemoteFileEntry(path, path.rsplit("/", 1)[-1], ".bin", len(body or b""), "", version)
                return 201
            if method == "MOVE":
                source = files[path]
                destination = headers["Destination"]
                if "invalid" in headers["If-Match"]:
                    if ignoreWrongEtag:
                        files[destination] = files.pop(path)
                        return 201
                    return 412
                if destination in files:
                    return 412
                files[destination] = files.pop(path)
                return 201
            if method == "DELETE":
                if cleanupStatus in {200, 204}:
                    prefix = path.rstrip("/") + "/"
                    files.clear()
                    collections.difference_update({item for item in collections if item == path or item.startswith(prefix)})
                return cleanupStatus
            raise AssertionError(method)

        with (
            patch.object(protocol, "test_connection", return_value=ConnectionTestResult(True, "ok")),
            patch.object(protocol, "get_file_info", side_effect=info),
            patch("app.service.shared_protocols.webdav._propfind_optional", side_effect=optional),
            patch("app.service.shared_protocols.webdav._requestStatus", side_effect=status),
        ):
            return protocol.verify_conditional_move(self.rootUrl, self.context)

    def test_capability_verification_requires_all_three_safe_move_semantics(self):
        result = self._runCapabilityVerification()
        self.assertEqual("verified", result.status)
        self.assertTrue(result.cleanup_succeeded)
        self.assertEqual(
            {"wrong_etag_status": 412, "correct_etag_status": 201, "overwrite_status": 412},
            result.evidence,
        )

    def test_capability_verification_rejects_server_that_ignores_wrong_etag(self):
        result = self._runCapabilityVerification(ignoreWrongEtag=True)
        self.assertEqual("unsupported", result.status)
        self.assertTrue(result.cleanup_succeeded)
        self.assertIn("错误 ETag", result.message)

    def test_capability_verification_cleanup_failure_never_unlocks_move(self):
        result = self._runCapabilityVerification(cleanupStatus=500)
        self.assertEqual("unknown", result.status)
        self.assertFalse(result.cleanup_succeeded)
        self.assertIn("清理失败", result.message)

    def test_connection_requires_readable_collection_not_options_success(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'<d:multistatus xmlns:d="DAV:"/>'
        with patch("app.service.shared_protocols.webdav.urlopen", return_value=response) as transport:
            result = WebDavProtocol().test_connection(self.rootUrl, self.context)
        self.assertFalse(result.success)
        self.assertFalse(result.readable)
        self.assertEqual("PROPFIND", transport.call_args.args[0].method)

    def test_propfind_response_is_byte_bounded(self):
        from unittest.mock import MagicMock
        from app.service.shared_protocols.webdav import _propfind, MAX_DAV_RESPONSE_BYTES
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * (MAX_DAV_RESPONSE_BYTES + 1)
        with patch("app.service.shared_protocols.webdav.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "8MB"):
                _propfind(self.rootUrl, self.context)
        response.__enter__.return_value.read.assert_called_once_with(MAX_DAV_RESPONSE_BYTES + 1)

    def test_http_error_resources_are_closed_even_for_optional_not_found(self):
        import io
        from urllib.error import HTTPError
        from app.service.shared_protocols.webdav import _propfind_optional
        for code in (404, 401):
            stream = io.BytesIO(b"synthetic error body")
            error = HTTPError(self.rootUrl, code, "synthetic", None, stream)
            if code == 404:
                with patch("app.service.shared_protocols.webdav.urlopen", side_effect=error):
                    self.assertIsNone(_propfind_optional(self.rootUrl, self.context))
            else:
                self.assertFalse(_webdav_error_message(error).success)
            self.assertTrue(stream.closed)

    def test_failed_property_status_is_not_a_readable_directory(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'''<d:multistatus xmlns:d="DAV:">
        <d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>
        </d:resourcetype></d:prop><d:status>HTTP/1.1 403 Forbidden</d:status></d:propstat>
        </d:response></d:multistatus>'''
        with patch("app.service.shared_protocols.webdav.urlopen", return_value=response):
            result = WebDavProtocol().test_connection(self.rootUrl, self.context)
        self.assertFalse(result.success)
        self.assertFalse(result.readable)

    def test_dtd_is_rejected_before_entity_expansion_for_utf8_and_utf16(self):
        from app.service.shared_protocols.webdav import _parseDavPayload
        xml = '<!DOCTYPE root [<!ENTITY private "synthetic-private">]><d:multistatus xmlns:d="DAV:">&private;</d:multistatus>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding), self.assertRaisesRegex(ValueError, "响应格式无效"):
                _parseDavPayload(xml.encode(encoding))

    def test_href_rejects_foreign_origin_port_and_root_escape(self):
        for href in (
            "https://other.invalid/dav/", "//other.invalid/dav/",
            "https://nas.invalid:444/dav/", "/outside/", "/dav-other/",
            "../outside/", "/dav/%2e%2e/outside/", "/dav/%252e%252e/outside/",
            "/dav/a%2fb/", "https://user:synthetic-password@nas.invalid/dav/",
        ):
            with self.subTest(href=href), self.assertRaises(ValueError):
                _url_from_href(self.rootUrl, href)

    def test_href_resolves_relative_and_absolute_same_origin(self):
        for href in ("Movies/", "/dav/Movies/", "https://NAS.invalid:443/dav/Movies/"):
            with self.subTest(href=href):
                self.assertTrue(_url_from_href(self.rootUrl, href).endswith("/dav/Movies/"))

    def test_request_checks_root_before_transport(self):
        for path in ("https://other.invalid/dav/file", "https://nas.invalid/outside/file"):
            with patch("app.service.shared_protocols.webdav.urlopen") as transport:
                with self.assertRaises(ValueError):
                    _request(path, "PROPFIND", self.context)
                transport.assert_not_called()

    def test_move_target_checks_boundary_before_transport(self):
        for target in ("https://other.invalid/dav/file", "https://nas.invalid/outside/file"):
            with patch("app.service.shared_protocols.webdav.urlopen") as transport:
                result = WebDavProtocol().move_file(self.rootUrl + "file", target, self.context)
                self.assertFalse(result.success)
                transport.assert_not_called()

    def test_dry_run_rejects_foreign_target_before_reading_source(self):
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            result = WebDavProtocol().check_rename_ready(
                self.rootUrl + "file", "https://other.invalid/dav/file", self.context
            )
            self.assertFalse(result.success)
            transport.assert_not_called()

    def test_contextless_move_cannot_escape_source_directory(self):
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            result = WebDavProtocol().move_file(self.rootUrl + "file", "https://nas.invalid/other/file")
            self.assertFalse(result.success)
            transport.assert_not_called()

    def test_authentication_is_preserved_for_allowed_request(self):
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            _request(self.rootUrl + "Movies/", "PROPFIND", self.context)
        request = transport.call_args.args[0]
        self.assertTrue(request.get_header("Authorization").startswith("Basic "))
        self.assertEqual("PROPFIND", request.get_method())

    def test_root_listing_does_not_offer_escape_parent(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size=-1):
                return b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>'
        with patch("app.service.shared_protocols.webdav.urlopen", return_value=Response()):
            listing = WebDavProtocol().list_directories(self.rootUrl, self.context)
        self.assertIsNone(listing.parent_path)

    def test_directory_listing_requires_self_record_and_resource_addresses(self):
        payloads = (
            '<d:multistatus xmlns:d="DAV:"/>',
            '<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat></d:response></d:multistatus>',
            '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>',
        )
        for payload in payloads:
            for operation in (WebDavProtocol().list_files, WebDavProtocol().list_directories):
                with self.subTest(payload=payload, operation=operation.__name__):
                    with patch("app.service.shared_protocols.webdav._propfind", return_value=ET.fromstring(payload)):
                        with self.assertRaises(ValueError):
                            operation(self.rootUrl, self.context)

    def test_verified_empty_directory_is_supported(self):
        root = ET.fromstring('<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>')
        with patch("app.service.shared_protocols.webdav._propfind", return_value=root):
            self.assertEqual([], WebDavProtocol().list_files(self.rootUrl, self.context))
            self.assertEqual([], WebDavProtocol().list_directories(self.rootUrl, self.context).entries)

    def test_recursive_scan_rejects_incomplete_child_directory(self):
        root = ET.fromstring('<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response><d:response><d:href>/dav/Movies/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>')
        child = ET.fromstring('<d:multistatus xmlns:d="DAV:"/>')
        with patch("app.service.shared_protocols.webdav._propfind", side_effect=[root, child]) as transport:
            with self.assertRaisesRegex(ValueError, "响应不完整"):
                WebDavProtocol().list_files(self.rootUrl, self.context)
        self.assertEqual(2, transport.call_count)

    def test_arbitrary_transport_error_is_not_echoed(self):
        result = _webdav_error_message(RuntimeError("synthetic-password"))
        self.assertNotIn("synthetic-password", result.message + (result.suggestion or ""))

    def test_file_info_reads_exact_resource_and_etag(self):
        resource = ET.fromstring('''<d:multistatus xmlns:d="DAV:"><d:response>
          <d:href>/dav/Movie.mkv</d:href><d:propstat><d:prop>
          <d:resourcetype/><d:getcontentlength>42</d:getcontentlength><d:getetag>"version-a"</d:getetag>
          </d:prop></d:propstat></d:response></d:multistatus>''')
        with patch("app.service.shared_protocols.webdav._propfind_optional", return_value=resource):
            info = WebDavProtocol().get_file_info(self.rootUrl + "Movie.mkv", self.context)
        self.assertEqual(42, info.file_size)
        self.assertEqual('"version-a"', info.version)
        self.assertEqual("", info.modified_at)

    def test_conditional_move_sends_if_match_and_rejects_unsafe_etags(self):
        context = replace(self.context, expected_source_version='"version-a"')
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            transport.return_value.__enter__.return_value.status = 201
            result = WebDavProtocol().move_file(self.rootUrl + "Movie.mkv", self.rootUrl + "New.mkv", context)
        self.assertTrue(result.success)
        request = transport.call_args.args[0]
        self.assertEqual('"version-a"', request.get_header("If-match"))
        self.assertEqual("F", request.get_header("Overwrite"))
        for invalid in ('W/"weak"', '"line\nfeed"', "*", "unquoted"):
            with self.subTest(invalid=invalid), patch("app.service.shared_protocols.webdav.urlopen") as transport:
                result = WebDavProtocol().move_file(
                    self.rootUrl + "Movie.mkv", self.rootUrl + "New.mkv",
                    replace(self.context, expected_source_version=invalid),
                )
                self.assertFalse(result.success)
                transport.assert_not_called()

    def test_move_without_etag_is_rejected_before_transport(self):
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            result = WebDavProtocol().move_file(
                self.rootUrl + "Movie.mkv",
                self.rootUrl + "New.mkv",
                self.context,
            )

        self.assertFalse(result.success)
        self.assertFalse(result.indeterminate)
        self.assertIn("拒绝真实移动", result.message)
        transport.assert_not_called()

    def test_unverified_endpoint_rejects_dry_run_and_move_before_transport(self):
        context = replace(self.context, conditional_move_status="unknown", expected_source_version='"version-a"')
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            readiness = WebDavProtocol().check_rename_ready(
                self.rootUrl + "Movie.mkv",
                self.rootUrl + "New.mkv",
                context,
            )
            result = WebDavProtocol().move_file(
                self.rootUrl + "Movie.mkv",
                self.rootUrl + "New.mkv",
                context,
            )

        self.assertFalse(readiness.success)
        self.assertFalse(result.success)
        self.assertIn("尚未验证", readiness.message)
        transport.assert_not_called()

    def test_move_204_is_indeterminate_and_not_success(self):
        context = replace(self.context, expected_source_version='"version-a"')
        with patch("app.service.shared_protocols.webdav.urlopen") as transport:
            transport.return_value.__enter__.return_value.status = 204
            result = WebDavProtocol().move_file(
                self.rootUrl + "Movie.mkv",
                self.rootUrl + "New.mkv",
                context,
            )

        self.assertFalse(result.success)
        self.assertTrue(result.indeterminate)
        self.assertIn("人工核查", result.message)

    def test_move_412_is_known_conflict(self):
        context = replace(self.context, expected_source_version='"version-a"')
        error = HTTPError(self.rootUrl, 412, "Precondition Failed", {}, None)
        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=error):
            result = WebDavProtocol().move_file(
                self.rootUrl + "Movie.mkv",
                self.rootUrl + "New.mkv",
                context,
            )

        self.assertFalse(result.success)
        self.assertFalse(result.indeterminate)
        self.assertIn("412", result.message)

    def test_nested_encoded_boundaries_are_rejected(self):
        with self.assertRaises(ValueError):
            _confined_url("https://nas.invalid/dav/%252foutside", self.rootUrl)

    def test_real_transport_never_follows_redirect_with_authentication(self):
        # 本机 HTTP 仅用于验证 urllib 跳转处理器，生产协议仍只允许 HTTPS。
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/start",
                headers={"Authorization": "Bearer synthetic-token"},
            )
            with self.assertRaisesRegex(ValueError, "重定向"):
                urlopen(request, timeout=3)
            self.assertEqual([("/start", "Bearer synthetic-token")], captured)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
