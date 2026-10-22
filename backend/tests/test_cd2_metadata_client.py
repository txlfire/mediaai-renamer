"""使用本机临时 TLS gRPC 服务验证投影；不使用真实CD2或账号。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import threading
import time
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import grpc

from app.service import cd2_metadata_protocol as wire
from app.service.cd2_metadata_client import Cd2MetadataClient, Cd2ReadError
from app.service.cloud_drive_error_codes import CD2_RPC_FAILURE_CODES

_ROOT = "/test-cloud/movies"
_TOKEN = "synthetic-test-only-api-token"


class _IsolatedPermit:
    """仅协议测试的替身，不接入任何生产入口。"""
    def markSending(self, method):
        pass
    def markSucceeded(self):
        pass
    def markIncomplete(self, reason):
        pass


def _certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()), certificate.public_bytes(serialization.Encoding.PEM))


class Cd2MetadataClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.privateKey, cls.ca = _certificate()

    def setUp(self):
        self.calls = []
        self.callAuthentication = []
        self.admissions = 0
        self.activeAdmissions = 0
        self.tokenInfo = wire.TokenInfo(token=_TOKEN, rootDir=_ROOT,
            permissions=wire.TokenPermissions(allow_list=True, allow_view_runtime_info=True))
        self.version = "1.0.0-test"
        self.listRequest = None
        self.expectedWirePath = "/"
        self.cancelled = threading.Event()
        self.replyFactory = lambda: iter([self.reply("one.mkv"), self.reply("two.txt")])
        self.runtimeHook = None
        self.streamFailure = None
        executor = ThreadPoolExecutor(max_workers=4)
        self.server = grpc.server(executor)
        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("clouddrive.CloudDriveFileSrv", {
            "GetApiTokenInfo": grpc.unary_unary_rpc_method_handler(self.tokenHandler,
                request_deserializer=wire.StringValue.FromString, response_serializer=wire.TokenInfo.SerializeToString),
            "GetRuntimeInfo": grpc.unary_unary_rpc_method_handler(self.runtimeHandler,
                request_deserializer=wire.Empty.FromString, response_serializer=wire.RuntimeInfo.SerializeToString),
            "GetSubFiles": grpc.unary_stream_rpc_method_handler(self.listHandler,
                request_deserializer=wire.ListSubFileRequest.FromString, response_serializer=wire.SubFilesReply.SerializeToString),
        }),))
        self.port = self.server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials(((self.privateKey, self.ca),)))
        self.server.start()
        self.addCleanup(executor.shutdown, wait=True)
        self.addCleanup(lambda: self.server.stop(0).wait(3))

    @contextmanager
    def admission(self, deadline):
        # 仅测试用准入，绝不能作为生产账号限流实现。
        self.assertGreater(deadline, time.monotonic())
        self.assertEqual(0, self.activeAdmissions)
        self.admissions += 1
        self.activeAdmissions += 1
        try:
            yield _IsolatedPermit()
        finally:
            self.activeAdmissions -= 1

    def client(self, **kwargs):
        options = {"endpoint": f"https://localhost:{self.port}", "token": _TOKEN, "rootPath": _ROOT,
                   "admission": self.admission, "trustedCaPem": self.ca}
        options.update(kwargs)
        value = Cd2MetadataClient(**options)
        self.addCleanup(value.close)
        return value

    def _call(self, name, context):
        self.calls.append(name)
        metadata = dict(context.invocation_metadata())
        self.callAuthentication.append((name, "authorization" in metadata))
        if name == "GetApiTokenInfo":
            self.assertNotIn("authorization", metadata)
        else:
            self.assertEqual("Bearer " + _TOKEN, metadata.get("authorization"))

    def tokenHandler(self, request, context):
        self._call("GetApiTokenInfo", context)
        self.assertEqual(_TOKEN, request.value)
        return self.tokenInfo

    def runtimeHandler(self, request, context):
        self._call("GetRuntimeInfo", context)
        if self.runtimeHook:
            self.runtimeHook(context)
        return wire.RuntimeInfo(productVersion=self.version, osInfo="private-os-info")

    def listHandler(self, request, context):
        self._call("GetSubFiles", context)
        self.listRequest = request
        self.assertEqual(self.expectedWirePath, request.path)
        context.add_callback(self.cancelled.set)
        yield from self.replyFactory()
        if self.streamFailure:
            context.abort(self.streamFailure, "synthetic-server-detail-" + _TOKEN)

    def reply(self, name, **kwargs):
        values = {"id": "id-" + name, "name": name, "fullPathName": "/" + name, "fileType": 1, "size": 10}
        values.update(kwargs)
        return wire.SubFilesReply(subFiles=[wire.CloudDriveFile(**values)])

    def test_connection_checks_bearer_root_permissions_and_version(self):
        result = self.client().inspectConnection()
        self.assertEqual("1.0.0-test", result["runtimeVersion"])
        self.assertTrue(result["readOnlyTokenVerified"])
        self.assertFalse(result["writeAvailable"])
        self.assertEqual(["GetApiTokenInfo", "GetRuntimeInfo"], self.calls)
        self.assertEqual(2, self.admissions)
        self.assertNotIn(_TOKEN, str(result))
        self.assertNotIn("private-os-info", str(result))

    def test_public_token_introspection_omits_bearer_without_weakening_authorized_calls(self):
        client = self.client()
        self.assertTrue(client.inspectConnection()["readOnlyTokenVerified"])
        self.assertTrue(client.readDirectory(_ROOT).complete)
        self.assertEqual([
            ("GetApiTokenInfo", False), ("GetRuntimeInfo", True),
            ("GetApiTokenInfo", False), ("GetSubFiles", True),
        ], self.callAuthentication)

    def test_minimum_list_only_token_does_not_request_runtime(self):
        self.tokenInfo.permissions.allow_view_runtime_info = False
        result = self.client().inspectConnection()
        self.assertIsNone(result["runtimeVersion"])
        self.assertFalse(result["runtimeVersionAvailable"])
        self.assertEqual(["GetApiTokenInfo"], self.calls)

    def test_runtime_version_accepts_bounded_cd2_build_display(self):
        self.version = "1.1.2 Build26-10-06 23:48:14"
        result = self.client().inspectConnection()
        self.assertEqual(self.version, result["runtimeVersion"])
        self.assertTrue(result["readOnlyTokenVerified"])

    def test_runtime_version_rejects_controls_markup_and_oversized_values(self):
        for version in ("", " ", "1.1.2\nBuild", "1.1.2\tBuild", "1.1.2\x00",
                        "<script>alert(1)</script>", "1.1.2\u202e", "x" * 65):
            with self.subTest(version=repr(version)):
                self.version = version
                with self.assertRaises(Cd2ReadError) as caught:
                    self.client().inspectConnection()
                self.assertEqual("invalid_runtime_version", caught.exception.code)
        self.assertNotIn("GetSubFiles", self.calls)

    def test_all_unneeded_permissions_rejected_before_list_or_runtime(self):
        client = self.client()
        for name in wire.PERMISSION_NAMES:
            if name in {"allow_list", "allow_view_runtime_info"}:
                continue
            with self.subTest(permission=name):
                setattr(self.tokenInfo.permissions, name, True)
                with self.assertRaises(Cd2ReadError) as caught:
                    client.readDirectory(_ROOT)
                self.assertEqual("overprivileged_or_missing_list_permission", caught.exception.code)
                setattr(self.tokenInfo.permissions, name, False)
        self.assertTrue(set(self.calls) <= {"GetApiTokenInfo"})

    def test_unknown_permission_missing_list_and_root_mismatch_fail_closed(self):
        self.tokenInfo.permissions.MergeFromString(b"\xa0\x06\x01")
        with self.assertRaises(Cd2ReadError) as caught:
            self.client().inspectConnection()
        self.assertEqual("unknown_token_permissions", caught.exception.code)
        self.tokenInfo.permissions.CopyFrom(wire.TokenPermissions())
        with self.assertRaises(Cd2ReadError):
            self.client().inspectConnection()
        self.tokenInfo.permissions.allow_list = True
        for root in ("/", "/test-cloud", "/test-cloud/movies2", _ROOT + "/nested"):
            self.tokenInfo.rootDir = root
            with self.subTest(root=root), self.assertRaises(Cd2ReadError) as caught:
                self.client().inspectConnection()
            self.assertEqual("token_root_mismatch", caught.exception.code)
        self.assertTrue(set(self.calls) <= {"GetApiTokenInfo"})

    def test_directory_stream_does_not_refresh_download_or_recurse(self):
        result = self.client().readDirectory(_ROOT)
        self.assertTrue(result.complete)
        self.assertEqual(2, len(result.entries))
        self.assertEqual("unknown", result.freshness)
        self.assertEqual(["GetApiTokenInfo", "GetSubFiles"], self.calls)
        self.assertFalse(self.listRequest.forceRefresh)
        self.assertTrue(self.listRequest.HasField("checkExpires"))
        self.assertFalse(self.listRequest.checkExpires)
        self.assertEqual(2, self.admissions)
        self.assertEqual(0, self.activeAdmissions)

    def test_entry_limit_cancels_stream_and_never_claims_complete(self):
        result = self.client().readDirectory(_ROOT, entryLimit=1)
        self.assertFalse(result.complete)
        self.assertEqual("entry_limit", result.stopReason)
        self.assertEqual(1, len(result.entries))
        self.assertTrue(self.cancelled.wait(2))
        self.assertEqual(1, self.calls.count("GetSubFiles"))

    def test_token_virtual_root_round_trip_keeps_approved_absolute_paths(self):
        result = self.client().readDirectory(_ROOT)
        self.assertEqual("/", self.listRequest.path)
        self.assertEqual(_ROOT, result.path)
        self.assertEqual([_ROOT + "/one.mkv", _ROOT + "/two.txt"],
                         [entry.path for entry in result.entries])

    def test_nested_token_path_is_mapped_once_and_must_remain_direct_child(self):
        self.expectedWirePath = "/Season 01"
        self.replyFactory = lambda: iter([self.reply("episode.mkv",fullPathName="/Season 01/episode.mkv")])
        result = self.client().readDirectory(_ROOT + "/Season 01")
        self.assertEqual("/Season 01", self.listRequest.path)
        self.assertEqual(_ROOT + "/Season 01/episode.mkv", result.entries[0].path)
        self.replyFactory = lambda: iter([self.reply("episode.mkv",fullPathName="/Season 02/episode.mkv")])
        with self.assertRaises(Cd2ReadError) as caught:
            self.client().readDirectory(_ROOT + "/Season 01")
        self.assertEqual("out_of_scope_or_unsupported_entry", caught.exception.code)

    def test_outside_approved_root_is_rejected_before_namespace_mapping_or_rpc(self):
        client = self.client()
        for path in ("/", "/test-cloud", _ROOT + "2", _ROOT + "/../other"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.readDirectory(path)
        self.assertEqual([], self.calls)

    def test_byte_and_empty_message_limits(self):
        self.replyFactory = lambda: iter([self.reply("a" * 900)])
        result = self.client().readDirectory(_ROOT, byteLimit=1024)
        self.assertEqual("byte_limit", result.stopReason)
        self.assertFalse(result.complete)
        self.assertEqual(0, len(result.entries))
        self.replyFactory = lambda: iter([wire.SubFilesReply()] * 100)
        result = self.client().readDirectory(_ROOT, messageLimit=3)
        self.assertEqual("message_limit", result.stopReason)
        self.assertFalse(result.complete)

    def test_oversized_single_message_is_bounded_by_grpc(self):
        self.replyFactory = lambda: iter([self.reply("a" * 900)])
        with self.assertRaises(Cd2ReadError) as caught:
            self.client(maxMessageBytes=1024).readDirectory(_ROOT)
        self.assertEqual("resource_exhausted", caught.exception.code)
        self.assertFalse(caught.exception.partialObservation.complete)
        self.assertEqual(1, self.calls.count("GetSubFiles"))

    def test_untrusted_entry_path_type_and_duplicate_fail_closed(self):
        client = self.client()
        for overrides in ({"fullPathName": "/test-cloud/movies2/one.mkv"},
                          {"fullPathName": _ROOT + "/one.mkv"},
                          {"fullPathName": "/sub/one.mkv"},
                          {"fullPathName": "/../one.mkv"},
                          {"fullPathName": "//one.mkv"},
                          {"fullPathName": "/%2e%2e/one.mkv"},
                          {"fullPathName": _ROOT + "/sub/one.mkv"},
                          {"fullPathName": _ROOT + "/../one.mkv"}, {"name": "different"},
                          {"fileType": 5}, {"size": -1}, {"isLocal": True}, {"isArchiveFolder": True},
                          {"isDirectory": True}):
            with self.subTest(overrides=overrides):
                self.replyFactory = lambda: iter([self.reply("one.mkv", **overrides)])
                with self.assertRaises(Cd2ReadError) as caught:
                    client.readDirectory(_ROOT)
                self.assertFalse(caught.exception.partialObservation.complete)
        self.replyFactory = lambda: iter([self.reply("same"), self.reply("same")])
        with self.assertRaises(Cd2ReadError) as caught:
            client.readDirectory(_ROOT)
        self.assertEqual("duplicate_entry", caught.exception.code)

    def test_partial_failure_is_redacted_and_not_retried(self):
        self.replyFactory = lambda: iter([self.reply("first.mkv")])
        self.streamFailure = grpc.StatusCode.UNAVAILABLE
        with self.assertRaises(Cd2ReadError) as caught:
            self.client().readDirectory(_ROOT)
        self.assertEqual("unavailable", caught.exception.code)
        self.assertNotIn(_TOKEN, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(1, len(caught.exception.partialObservation.entries))
        self.assertFalse(caught.exception.partialObservation.complete)
        self.assertEqual(1, self.calls.count("GetSubFiles"))

    def test_rejected_or_expired_token_does_not_retry_list(self):
        self.replyFactory = lambda: iter([])
        for status in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
            self.streamFailure = status
            before = self.calls.count("GetSubFiles")
            with self.subTest(status=status), self.assertRaises(Cd2ReadError) as caught:
                self.client().readDirectory(_ROOT)
            self.assertEqual(status.name.lower(), caught.exception.code)
            self.assertNotIn(_TOKEN, str(caught.exception))
            self.assertFalse(caught.exception.partialObservation.complete)
            self.assertEqual(before + 1, self.calls.count("GetSubFiles"))

    def test_admission_wait_counts_toward_deadline(self):
        @contextmanager
        def delayed(deadline):
            time.sleep(0.1)
            yield
        with self.assertRaises(Cd2ReadError) as caught:
            self.client(admission=delayed, timeoutSeconds=0.05).inspectConnection()
        self.assertEqual("deadline_exceeded", caught.exception.code)
        self.assertEqual([], self.calls)

    def test_local_processing_deadline_cannot_be_reported_complete(self):
        client = self.client()
        original = client._entry
        originalRemaining = client._remaining
        expired = False
        def expireDuringProcessing(raw, directory):
            nonlocal expired
            result = original(raw, directory)
            expired = True
            return result
        def remaining(deadline):
            if expired:
                raise Cd2ReadError("deadline_exceeded")
            return originalRemaining(deadline)
        with (patch.object(client, "_entry", side_effect=expireDuringProcessing),
              patch.object(client, "_remaining", side_effect=remaining)):
            with self.assertRaises(Cd2ReadError) as caught:
                client.readDirectory(_ROOT)
        self.assertEqual("deadline_exceeded", caught.exception.code)
        self.assertFalse(caught.exception.partialObservation.complete)
        self.assertEqual(1, self.calls.count("GetSubFiles"))

    def test_wire_contract_and_internal_rpc_allowlist(self):
        # 固定字节依据官方字段号独立给定，避免测试服务与客户端共用错定义而同时通过。
        permissions = wire.TokenPermissions.FromString(bytes.fromhex("0801b00101"))
        self.assertTrue(permissions.allow_list)
        self.assertTrue(permissions.allow_view_runtime_info)
        request = wire.ListSubFileRequest(path="/cloud/movies", checkExpires=False)
        self.assertEqual(bytes.fromhex("0a0d2f636c6f75642f6d6f766965731800"), request.SerializeToString())
        with self.assertRaises(Cd2ReadError) as caught:
            self.client()._unary("DeleteFile", wire.Empty(), wire.Empty, time.monotonic() + 1)
        self.assertEqual("rpc_not_allowed", caught.exception.code)
        self.assertEqual([], self.calls)

    def test_safe_rpc_failure_codes_match_standard_statuses_without_success(self):
        self.assertEqual({status.name.lower() for status in grpc.StatusCode if status != grpc.StatusCode.OK},
                         CD2_RPC_FAILURE_CODES)

    def test_rpc_deadline_and_concurrent_client_guard(self):
        entered = threading.Event()
        def blocked(context):
            entered.set()
            while context.is_active():
                time.sleep(0.01)
        self.runtimeHook = blocked
        client = self.client(timeoutSeconds=0.3)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(client.inspectConnection)
            self.assertTrue(entered.wait(2))
            with self.assertRaises(Cd2ReadError) as caught:
                client.inspectConnection()
            self.assertEqual("client_busy", caught.exception.code)
            with self.assertRaises(Cd2ReadError) as caught:
                result.result(timeout=2)
            self.assertEqual("deadline_exceeded", caught.exception.code)
        self.assertEqual(1, self.calls.count("GetRuntimeInfo"))

    def test_tls_is_verified_and_insecure_or_credential_urls_are_rejected(self):
        with self.assertRaises(Cd2ReadError):
            self.client(trustedCaPem=None, timeoutSeconds=0.5).inspectConnection()
        self.assertEqual([], self.calls)
        for endpoint in ("http://localhost", "https://user:pass@localhost", "https://localhost/path",
                         "https://localhost?token=test", "https://localhost#fragment", "https://localhost:99999",
                         "https://localhost:0"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.client(endpoint=endpoint)

    def test_bad_budgets_paths_and_closed_client_never_list(self):
        for kwargs in ({"timeoutSeconds": float("nan")}, {"timeoutSeconds": True}, {"maxMessageBytes": True},
                       {"admission": None}, {"token": "bad\nheader"}, {"rootPath": "/"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.client(**kwargs)
        client = self.client()
        for kwargs in ({"entryLimit": True}, {"entryLimit": 10001}, {"byteLimit": -1}, {"messageLimit": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                client.readDirectory(_ROOT, **kwargs)
        with self.assertRaises(ValueError):
            client.readDirectory(_ROOT + "2")
        client.close()
        with self.assertRaises(Cd2ReadError) as caught:
            client.inspectConnection()
        self.assertEqual("client_closed", caught.exception.code)
        self.assertEqual([], self.calls)
