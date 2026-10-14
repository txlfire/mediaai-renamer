"""真实WebDAV测试入口必须隔离、可信连接并确认清理，不访问NAS。"""

from contextlib import closing
import sqlite3
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

import integration.test_webdav_e2e as integration


class WebDavIntegrationSafetyTest(unittest.TestCase):
    def setUp(self):
        self.case = integration.WebDavEndToEndTest("test_https_basic_connection_succeeds")
        self.case.base_url = "https://review.invalid/isolated"
        self.case.root_url = self.case.base_url + "/case-synthetic/"
        self.case.username = "synthetic"
        self.case.password = "synthetic"
        self.case.ssl_context = ssl.create_default_context()
        self.case.protocol = Mock()
        self.case.protocol.verify_conditional_move.return_value = Mock(status="verified", cleanup_succeeded=True, message="synthetic")
        self.case.remoteCreated = False
        self.addCleanup(self.case.doCleanups)

    def missing(self):
        return HTTPError(self.case.root_url, 404, "Not found", {}, None)

    def test_existing_collection_is_never_created_or_deleted(self):
        with patch.object(self.case, "_request", return_value=207) as request:
            with self.assertRaisesRegex(AssertionError, "已经存在"):
                self.case.setUp()
            self.case.doCleanups()
        self.assertEqual(["PROPFIND"], [call.args[0] for call in request.call_args_list])

    def test_setup_registers_cleanup_and_independently_verifies_missing(self):
        with patch.object(self.case, "_request", side_effect=[self.missing(), 201, 204, self.missing()]) as request:
            self.case.setUp()
            self.assertTrue(self.case.remoteCreated)
            self.case.doCleanups()
            self.assertFalse(self.case.remoteCreated)
        self.assertEqual(["PROPFIND", "MKCOL", "DELETE", "PROPFIND"],
                         [call.args[0] for call in request.call_args_list])

    def test_cleanup_failure_is_visible_and_keeps_cleanup_needed(self):
        self.case.remoteCreated = True
        failure = HTTPError(self.case.root_url, 403, "Forbidden", {}, None)
        with patch.object(self.case, "_request", side_effect=failure):
            with self.assertRaisesRegex(AssertionError, "清理失败"):
                self.case._cleanupRemote()
        self.assertTrue(self.case.remoteCreated)

    def test_successful_delete_with_remaining_collection_is_failure(self):
        self.case.remoteCreated = True
        with patch.object(self.case, "_request", side_effect=[204, 207]):
            with self.assertRaisesRegex(AssertionError, "仍然存在"):
                self.case._cleanupRemote()
        self.assertTrue(self.case.remoteCreated)

    def test_unconfirmed_creation_is_not_deleted(self):
        with patch.object(self.case, "_request", side_effect=[self.missing(), TimeoutError("synthetic")]) as request:
            with self.assertRaisesRegex(AssertionError, "结果未确认"):
                self.case.setUp()
            self.case.doCleanups()
        self.assertEqual(["PROPFIND", "MKCOL"], [call.args[0] for call in request.call_args_list])

    def test_requests_reject_escape_before_transport(self):
        urls = ["http://review.invalid/isolated/case-synthetic/file",
                "https://other.invalid/file", self.case.base_url + "/existing/",
                self.case.root_url + "../existing", self.case.root_url + "%252e%252e/existing"]
        with patch.object(integration, "build_opener") as transport:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.case._request("DELETE", url)
        transport.assert_not_called()

    def test_disabled_tls_validation_is_rejected(self):
        self.case.ssl_context = ssl._create_unverified_context()
        with patch.object(integration, "build_opener") as transport:
            with self.assertRaisesRegex(ValueError, "校验证书"):
                self.case._request("PUT", self.case.root_url + "file", body=b"synthetic")
        transport.assert_not_called()

    def test_move_destination_escape_is_rejected_case_insensitively(self):
        with patch.object(integration, "build_opener") as transport:
            for header in ("Destination", "destination"):
                with self.subTest(header=header), self.assertRaises(ValueError):
                    self.case._request("MOVE", self.case.root_url + "file",
                                       headers={header: "https://other.invalid/file"})
        transport.assert_not_called()

    def test_transport_includes_redirect_rejection_and_closes_response(self):
        response = Mock(status=201)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(integration, "build_opener", return_value=opener) as build:
            self.assertEqual(201, self.case._request("MKCOL", self.case.root_url))
        redirect = next(handler for handler in build.call_args.args
                        if isinstance(handler, integration._RejectRedirect))
        with self.assertRaisesRegex(ValueError, "重定向"):
            redirect.redirect_request(Request(self.case.root_url), None, 302, "Found", {}, "https://other.invalid/")
        response.__exit__.assert_called_once()

    def test_recovery_fixture_keeps_observed_versions(self):
        settings, itemId = self.case._build_failed_recovery(self.case.root_url + "source.mkv",
            self.case.root_url + "target.mkv", '"source-identity"', '"target-identity"')
        with closing(sqlite3.connect(settings.database_path)) as connection:
            versions = connection.execute("SELECT source_version,target_version FROM remote_operation_items WHERE id=?",
                                          (itemId,)).fetchone()
        self.assertEqual(('"source-identity"', '"target-identity"'), versions)
