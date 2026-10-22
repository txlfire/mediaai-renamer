"""目录原生传输只连本机合成TLS服务器，不连NAS/CD2/115。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
import os
import sqlite3
import time
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc

import test_cloud_directory_plans as fixtures
from test_cd2_metadata_client import _certificate
from app.service import cd2_metadata_protocol as meta, cd2_directory_protocol as wire
from app.service.cd2_directory_client import Cd2DirectoryClient, Cd2DirectoryError, DirectoryRpcAdmission, _SUPPORTED_RUNTIME
from app.service.cloud_directory_admission_service import DirectorySpacingWait
from app.service.cloud_directory_admission_service import approveDirectoryExecution, bindDirectoryWorker
from app.service.cloud_directory_item_service import prepareDirectoryItem, verifyDirectoryItem
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.utils.cloud_drive_paths import canonicalCd2Target


class DirectorySpacingWaitTest(unittest.TestCase):
    def test_waits_remaining_spacing_without_repeated_database_writes(self):
        admission = object.__new__(DirectoryRpcAdmission)
        admission.settings, admission.planId, admission.token, admission.pid = None, "synthetic", "synthetic", os.getpid()
        clock = [0.0]
        with patch("app.service.cd2_directory_client.time.monotonic", side_effect=lambda: clock[0]), \
                patch("app.service.cd2_directory_client.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)) as sleep, \
                patch("app.service.cd2_directory_client.reserveDirectoryRpc", side_effect=[DirectorySpacingWait(1.75), "rpc"]) as reserve, \
                patch("app.service.cd2_directory_client.recordDirectoryRpcResponse"), \
                patch("app.service.cd2_directory_client.markDirectoryRpcUnknown") as unknown:
            with admission.call("GetRuntimeInfo", 10) as responded:
                responded("ok", 0)
            self.assertEqual(2, reserve.call_count)
            sleep.assert_called_once_with(1.75)
            unknown.assert_not_called()

    def test_local_wait_cannot_exceed_deadline_or_send_when_budget_expires(self):
        admission = object.__new__(DirectoryRpcAdmission)
        admission.settings, admission.planId, admission.token, admission.pid = None, "synthetic", "synthetic", os.getpid()
        clock = [0.0]
        with patch("app.service.cd2_directory_client.time.monotonic", side_effect=lambda: clock[0]), \
                patch("app.service.cd2_directory_client.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)) as sleep, \
                patch("app.service.cd2_directory_client.reserveDirectoryRpc", side_effect=DirectorySpacingWait(2)) as reserve, \
                patch("app.service.cd2_directory_client.markDirectoryRpcUnknown") as unknown:
            with self.assertRaisesRegex(Cd2DirectoryError, "deadline_exceeded"):
                with admission.call("GetRuntimeInfo", 0.5):
                    self.fail("截止后不能进入网络调用")
            self.assertEqual(1, reserve.call_count)
            sleep.assert_called_once_with(0.5)
            unknown.assert_not_called()


class Cd2DirectoryClientTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudDirectoryPlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.calls = []
        self.token = "synthetic-directory-token"
        self.permissions = {"allow_list": True, "allow_rename": True, "allow_view_runtime_info": True}
        self.version = _SUPPORTED_RUNTIME
        self.badRoot = False
        self.protected = ""
        self.tokenDelay = 0
        self.unknownPermission = False
        self.renamed = False
        self.replaceSource = False
        self.replaceTarget = False
        self.conflictOnRename = False
        self.loseRenameResponse = False
        self.rejectRename = False
        key, cert = _certificate()
        self.server = grpc.server(ThreadPoolExecutor(max_workers=2))
        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("clouddrive.CloudDriveFileSrv", {
            "GetApiTokenInfo": grpc.unary_unary_rpc_method_handler(self.tokenInfo,
                request_deserializer=meta.StringValue.FromString, response_serializer=meta.TokenInfo.SerializeToString),
            "GetRuntimeInfo": grpc.unary_unary_rpc_method_handler(self.runtimeInfo,
                request_deserializer=meta.Empty.FromString, response_serializer=meta.RuntimeInfo.SerializeToString),
            "FindFileByPath": grpc.unary_unary_rpc_method_handler(self.find,
                request_deserializer=wire.FindFileByPathRequest.FromString, response_serializer=wire.CloudDriveFile.SerializeToString),
            "RenameFile": grpc.unary_unary_rpc_method_handler(self.rename,
                request_deserializer=wire.RenameFileRequest.FromString, response_serializer=wire.FileOperationResult.SerializeToString),
        }),))
        port = self.server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials(((key, cert),)))
        self.server.start()
        self.addCleanup(lambda: self.server.stop(0).wait())
        caFile = self.fixture.settings.data_dir / "synthetic-public.crt"
        caFile.write_bytes(cert)
        self.settings = replace(self.fixture.settings, cloudDrive=replace(self.fixture.settings.cloudDrive,
            endpoint=f"https://localhost:{port}", caCertificateFile="" if self._testMethodName == "test_untrusted_tls_is_not_bypassed" else str(caFile), directoryRenameEnabled=True,
            directoryMappingRevision="synthetic-v1"))
        self.settings = bindCloudConfiguration(self.settings)
        self.fixture.settings = self.settings
        self.fixture.state.sql("UPDATE users SET permissions_json=?", (json.dumps(["scan:run", "cloud-directory:plan", "cloud-directory:execute"]),))
        jobId = self.fixture.observed()
        self.fixture.state.sql("UPDATE cloud_drive_read_sessions SET endpoint_hash=?", (sha256(canonicalCd2Target(self.settings.cloudDrive.endpoint).encode()).hexdigest(),))
        self.plan = self.fixture.plan(jobId)
        self.workerToken = approveDirectoryExecution(self.settings, self.plan["id"], requestId=str(uuid4()), expectedRevision=0,
            confirmMaintenance=True, confirmGlobalFence=True, auditContext=self.fixture.context)
        bindDirectoryWorker(self.settings, self.plan["id"], self.workerToken, os.getpid())

    def tokenInfo(self, request, context):
        self.calls.append(("token", dict(context.invocation_metadata()).get("authorization")))
        if request.value != self.token:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "synthetic-private-text")
        if self.tokenDelay:
            time.sleep(self.tokenDelay)
        permissions = meta.TokenPermissions(**self.permissions)
        if self.unknownPermission:
            permissions.MergeFromString(b"\xf8\x07\x01")
        return meta.TokenInfo(rootDir="/other/root" if self.badRoot else self.plan["parentPath"], permissions=permissions, expires_in=300)

    def runtimeInfo(self, _request, context):
        self.calls.append(("runtime", dict(context.invocation_metadata()).get("authorization")))
        return meta.RuntimeInfo(productVersion=self.version[0], CloudAPIVersion=self.version[1])

    def find(self, request, context):
        self.calls.append(("find", dict(context.invocation_metadata()).get("authorization")))
        source = "/" + self.plan["items"][0]["sourceName"]
        target = "/" + self.plan["items"][0]["targetName"]
        if (request.path == target and not self.renamed and not self.conflictOnRename
                or request.path == source and self.renamed):
            context.abort(grpc.StatusCode.NOT_FOUND, "synthetic-private-target")
        objectId = "synthetic-parent" if request.path == "/" else "replaced-source" if self.replaceSource and request.path == source else "external-target" if self.replaceTarget and request.path == target or self.conflictOnRename and request.path == target else self.plan["items"][0]["remoteId"]
        result = wire.CloudDriveFile(id=objectId, name=request.path[1:],
            fullPathName=request.path, isDirectory=True, fileType=0)
        if self.protected == "readonly":
            result.readOnly = True
        elif self.protected == "encrypted":
            result.fileEncryptionType = 2
        elif self.protected == "cloudLocked":
            result.CloudAPI.isLocked = True
        return result

    def rename(self, request, context):
        self.calls.append(("rename", dict(context.invocation_metadata()).get("authorization")))
        if self.conflictOnRename or self.rejectRename:
            return wire.FileOperationResult(success=False, errorMessage="synthetic-private-error")
        self.renamed = True
        if hasattr(self, "renameEntered"):
            self.renameEntered.set()
            self.renameBlock.wait(10)
        if self.loseRenameResponse:
            context.abort(grpc.StatusCode.UNAVAILABLE, "synthetic-lost-response")
        return wire.FileOperationResult(success=True)

    def client(self, settings=None):
        return Cd2DirectoryClient(settings or self.settings, self.plan["id"], self.workerToken, self.token)

    def test_secure_metadata_and_virtual_identity_are_budgeted_and_never_persist_token(self):
        before = self.fixture.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions")
        with self.client() as client:
            client.verifyConnection(time.monotonic() + 20)
            value = client.findDirectory("/" + self.plan["items"][0]["sourceName"], time.monotonic() + 20, itemOrdinal=1)
        self.assertEqual(self.plan["items"][0]["remoteId"], value.objectId)
        self.assertEqual([("token", None), ("runtime", "Bearer " + self.token), ("find", "Bearer " + self.token)], self.calls)
        self.assertEqual([(3, 1)], self.fixture.state.sql("SELECT used_rpc,slot_held FROM cloud_directory_executions"))
        self.assertEqual(before, self.fixture.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertNotIn(self.token, "\n".join(connection.iterdump()))

    def test_extra_or_missing_token_permissions_stop_before_runtime_without_retry(self):
        self.permissions["allow_delete"] = True
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "token_scope_or_permissions_invalid"):
                client.verifyConnection(time.monotonic() + 10)
        self.assertEqual([("token", None)], self.calls)
        self.assertEqual([("review", 1)], self.fixture.state.sql("SELECT status,used_rpc FROM cloud_directory_executions"))

    def test_unknown_permission_fields_fail_closed(self):
        self.unknownPermission = True
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "unknown_token_permissions"):
                client.verifyConnection(time.monotonic() + 10)
        self.assertEqual(1, len(self.calls))

    def test_token_root_and_unverified_runtime_are_not_silently_accepted(self):
        self.badRoot = True
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "token_scope_or_permissions_invalid"):
                client.verifyConnection(time.monotonic() + 10)

    def test_runtime_change_prevents_identity_lookup(self):
        self.version = ("1.1.3", "1.1.3")
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "runtime_not_verified"):
                client.verifyConnection(time.monotonic() + 10)
        self.assertEqual(2, len(self.calls))

    def assertRuntimeRejected(self, version):
        self.version = version
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "runtime_not_verified"):
                client.verifyConnection(time.monotonic() + 10)
            with self.assertRaisesRegex(Cd2DirectoryError, "connection_not_verified"):
                client.findDirectory("/", time.monotonic() + 10)
        self.assertEqual(["token", "runtime"], [method for method, _ in self.calls])
        self.assertEqual([("unknown",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE method='GetRuntimeInfo'"))

    def test_home_product_with_company_cloudapi_is_rejected(self):
        self.assertRuntimeRejected(("1.1.3 Build 26-10-17 17:13:11", _SUPPORTED_RUNTIME[1]))

    def test_company_product_with_home_cloudapi_is_rejected(self):
        self.assertRuntimeRejected((_SUPPORTED_RUNTIME[0], "1.1.3 Build 26-10-17 17:13:00"))

    def test_unverified_home_build_is_rejected(self):
        self.assertRuntimeRejected(("1.1.3 Build 26-10-17 17:13:12", "1.1.3 Build 26-10-17 17:13:00"))

    def test_home_exact_pair_can_rename_with_existing_durable_identity_proofs(self):
        self.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        with self.client() as client:
            self.precheck(client)
            self.assertTrue(client.renameItem(1, time.monotonic() + 10))
            self.assertEqual("verified", self.postcheck(client))
        self.assertEqual(1, sum(method == "rename" for method, _ in self.calls))

    def test_home_lost_rename_response_still_never_replays(self):
        self.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_lost_rename_response_keeps_unknown_and_never_repeats_write()

    def test_only_plan_paths_and_known_missing_target_are_supported(self):
        with self.client() as client:
            client.verifyConnection(time.monotonic() + 20)
            with self.assertRaisesRegex(Cd2DirectoryError, "path_not_in_plan"):
                client.findDirectory("/unapproved", time.monotonic() + 10)
            value = client.findDirectory("/" + self.plan["items"][0]["targetName"], time.monotonic() + 10,
                itemOrdinal=1, missingAllowed=True)
            self.assertIsNone(value)
            self.assertEqual([("responded", "not_found")], self.fixture.state.sql("SELECT phase,response_code FROM cloud_directory_rpc_intents WHERE method='FindFileByPath'"))

    def test_encrypted_directory_is_refused_and_freeze_is_preserved(self):
        self.protected = "encrypted"
        with self.client() as client:
            client.verifyConnection(time.monotonic() + 20)
            with self.assertRaisesRegex(Cd2DirectoryError, "directory_identity_unsupported"):
                client.findDirectory("/" + self.plan["items"][0]["sourceName"], time.monotonic() + 10, itemOrdinal=1)
        self.assertEqual([("review", 1)], self.fixture.state.sql("SELECT status,slot_held FROM cloud_directory_executions"))

    def test_deadline_unknown_never_replays_and_keeps_sending_evidence(self):
        self.tokenDelay = 0.3
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "deadline_exceeded"):
                client.verifyConnection(time.monotonic() + 0.15)
        self.assertLessEqual(len(self.calls), 1)
        self.assertEqual([("unknown",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents"))

    def test_write_projection_is_not_a_write_entry_and_invalid_deadlines_do_not_charge(self):
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "rpc_not_allowed"):
                client._call("RenameFile", wire.RenameFileRequest(theFilePath="/source", newName="target"),
                    wire.FileOperationResult, time.monotonic() + 10)
            with self.assertRaisesRegex(Cd2DirectoryError, "deadline_or_owner_invalid"):
                client.verifyConnection(float("inf"))
        self.assertEqual([], self.calls)
        self.assertEqual([(0,)], self.fixture.state.sql("SELECT used_rpc FROM cloud_directory_executions"))

    def test_untrusted_tls_is_not_bypassed(self):
        with self.client() as client:
            with self.assertRaisesRegex(Cd2DirectoryError, "unavailable"):
                client.verifyConnection(time.monotonic() + 5)
        self.assertEqual([], self.calls)
        self.assertEqual([("unknown",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents"))

    def precheck(self, client):
        deadline = time.monotonic() + 40
        client.verifyConnection(deadline)
        client.findDirectory("/", deadline)
        item = self.plan["items"][0]
        client.findDirectory("/", deadline, itemOrdinal=1)
        client.findDirectory("/" + item["sourceName"], deadline, itemOrdinal=1)
        client.findDirectory("/" + item["targetName"], deadline, itemOrdinal=1, missingAllowed=True)
        prepareDirectoryItem(self.settings, self.plan["id"], self.workerToken, 1)

    def postcheck(self, client):
        deadline = time.monotonic() + 40
        item = self.plan["items"][0]
        client.findDirectory("/" + item["targetName"], deadline, itemOrdinal=1, missingAllowed=True)
        client.findDirectory("/" + item["sourceName"], deadline, itemOrdinal=1, missingAllowed=True)
        client.findDirectory("/", deadline, itemOrdinal=1)
        return verifyDirectoryItem(self.settings, self.plan["id"], self.workerToken, 1)

    def test_native_rename_requires_durable_prechecks_and_records_post_identity(self):
        with self.client() as client:
            self.precheck(client)
            self.assertTrue(client.renameItem(1, time.monotonic() + 10))
            self.assertEqual("verified", self.postcheck(client))
            with self.assertRaisesRegex(CloudDirectoryError, "identity_precheck_required|execution_budget_exhausted"):
                client.renameItem(1, time.monotonic() + 10)
        self.assertEqual(1, sum(method == "rename" for method, _ in self.calls))
        self.assertEqual([("verified",)], self.fixture.state.sql("SELECT state FROM cloud_directory_item_outcomes"))
        self.assertEqual([(1,)], self.fixture.state.sql("SELECT slot_held FROM cloud_directory_executions"))

    def test_replaced_source_is_refused_before_any_rename(self):
        self.replaceSource = True
        with self.client() as client:
            with self.assertRaisesRegex(CloudDirectoryError, "identity_or_conflict_changed"):
                self.precheck(client)
        self.assertEqual(0, sum(method == "rename" for method, _ in self.calls))

    def test_lost_rename_response_keeps_unknown_and_never_repeats_write(self):
        with self.client() as client:
            self.precheck(client)
            self.loseRenameResponse = True
            with self.assertRaisesRegex(Cd2DirectoryError, "unavailable"):
                client.renameItem(1, time.monotonic() + 10)
            with self.assertRaises(CloudDirectoryError):
                client.renameItem(1, time.monotonic() + 10)
        self.assertTrue(self.renamed)
        self.assertEqual(1, sum(method == "rename" for method, _ in self.calls))
        self.assertEqual([("review",)], self.fixture.state.sql("SELECT state FROM cloud_directory_item_outcomes"))
        self.assertEqual([("unknown",)], self.fixture.state.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE method='RenameFile'"))

    def test_post_identity_change_is_manual_review_without_reverse_compensation(self):
        with self.client() as client:
            self.precheck(client)
            client.renameItem(1, time.monotonic() + 10)
            self.replaceTarget = True
            self.assertEqual("review", self.postcheck(client))
        self.assertEqual(1, sum(method == "rename" for method, _ in self.calls))

    def test_known_rejection_is_not_retried_or_reported_as_success(self):
        with self.client() as client:
            self.precheck(client)
            self.rejectRename = True
            self.assertFalse(client.renameItem(1, time.monotonic() + 10))
            self.assertEqual("not_applied", self.postcheck(client))
        self.assertEqual(1, sum(method == "rename" for method, _ in self.calls))
