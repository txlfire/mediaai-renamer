"""本机临时TLS合成服务验证隔离原生协议与真实隐藏子进程，不连接私人云端。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import os
import time
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc
import test_cloud_quarantine_plans as fixtures
from test_cd2_metadata_client import _certificate
from app.service import cd2_metadata_protocol as meta, cd2_quarantine_protocol as wire
from app.service import cloud_quarantine_execution_service as actions
from app.service import cloud_quarantine_worker_service as workers
from app.service.cd2_quarantine_client import Cd2QuarantineClient
from app.service.cloud_ad_review_service import _configHash
from app.utils.cloud_drive_paths import canonicalCd2Target


class Cd2QuarantineClientTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CloudQuarantinePlanTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.calls, self.moves, self.loseMove = [], 0, False
        self.version = ("1.1.2 Build 26-10-06 23:48:14", "1.1.2 Build 26-10-06 23:48:12")
        self.apiToken = "synthetic-quarantine-only-token"
        self.permissions = {"allow_list": True, "allow_move": True, "allow_view_runtime_info": True}
        key, cert = _certificate()
        self.server = grpc.server(ThreadPoolExecutor(max_workers=2))
        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("clouddrive.CloudDriveFileSrv", {
            "GetApiTokenInfo": grpc.unary_unary_rpc_method_handler(self.tokenInfo,
                request_deserializer=meta.StringValue.FromString, response_serializer=meta.TokenInfo.SerializeToString),
            "GetRuntimeInfo": grpc.unary_unary_rpc_method_handler(self.runtimeInfo,
                request_deserializer=meta.Empty.FromString, response_serializer=meta.RuntimeInfo.SerializeToString),
            "FindFileByPath": grpc.unary_unary_rpc_method_handler(self.find,
                request_deserializer=wire.FindFileByPathRequest.FromString, response_serializer=wire.CloudDriveFile.SerializeToString),
            "MoveFile": grpc.unary_unary_rpc_method_handler(self.move,
                request_deserializer=wire.MoveFileRequest.FromString, response_serializer=wire.FileOperationResult.SerializeToString),
        }),))
        port = self.server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials(((key, cert),)))
        self.server.start()
        self.addCleanup(lambda: self.server.stop(0).wait())
        caFile = self.fixture.settings.data_dir / "synthetic-public.crt"
        caFile.write_bytes(cert)
        self.settings = replace(self.fixture.settings, cloudDrive=replace(self.fixture.settings.cloudDrive,
            endpoint=f"https://localhost:{port}", caCertificateFile=str(caFile), quarantineEnabled=True))
        self.settings = bindCloudConfiguration(self.settings)
        self.fixture.settings = self.settings
        self.state = self.fixture.state
        self.state.sql("UPDATE cloud_drive_read_sessions SET endpoint_hash=?", (sha256(canonicalCd2Target(self.settings.cloudDrive.endpoint).encode()).hexdigest(),))
        self.state.sql("UPDATE cloud_ad_reviews SET config_hash=?", (_configHash(self.settings),))
        self.plan = self.fixture.create(entryIds=self.fixture.ids[:1])
        self.runId, self.token = actions.approveQuarantineRun(self.settings, self.plan["id"], requestId=str(uuid4()),
            expectedRevision=0, policyHash=self.plan["policyHash"], confirmMaintenance=True, confirmGlobalFence=True,
            auditContext=self.fixture.context)
        self.objects = {"/": ("parent-id", True, 0), "/"+self.plan["targetName"]: (self.plan["targetRemoteId"], True, 0)}
        for item in self.plan["items"]:
            self.objects["/"+item["sourceName"]] = (item["remoteId"], False, item["size"])

    def record(self, method, context):
        self.calls.append((method, time.monotonic(), dict(context.invocation_metadata()).get("authorization")))

    def tokenInfo(self, request, context):
        self.record("token", context)
        if request.value != self.apiToken:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "synthetic-private-detail")
        return meta.TokenInfo(token=self.apiToken, rootDir=self.plan["parentPath"],
            permissions=meta.TokenPermissions(**self.permissions), expires_in=300)

    def runtimeInfo(self, request, context):
        self.record("runtime", context)
        return meta.RuntimeInfo(productVersion=self.version[0], CloudAPIVersion=self.version[1])

    def find(self, request, context):
        self.record("find", context)
        if request.parentPath != "/":
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "synthetic-private-detail")
        value = self.objects.get(request.path)
        if value is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "synthetic-private-detail")
        objectId, directory, size = value
        return wire.CloudDriveFile(id=objectId, name=request.path.rpartition("/")[2] or "movies",
            fullPathName=request.path, isDirectory=directory, size=size, fileType=0 if directory else 1)

    def move(self, request, context):
        self.record("move", context)
        self.moves += 1
        self.assertEqual(1, len(request.theFilePaths))
        self.assertEqual(2, request.conflictPolicy)
        self.assertTrue(request.HasField("moveAcrossClouds"))
        self.assertFalse(request.moveAcrossClouds)
        self.assertTrue(request.HasField("handleConflictRecursively"))
        self.assertFalse(request.handleConflictRecursively)
        source = request.theFilePaths[0]
        target = request.destPath.rstrip("/") + "/" + source.rpartition("/")[2]
        if target not in self.objects:
            self.objects[target] = self.objects.pop(source)
        if self.loseMove:
            context.abort(grpc.StatusCode.UNAVAILABLE, "synthetic-private-detail")
        return wire.FileOperationResult(success=True, errorMessage="synthetic-private-detail")

    def bind(self):
        actions.bindQuarantineWorker(self.settings, self.runId, self.token, os.getpid())

    def test_native_move_records_exact_proof_actual_spacing_and_no_token_header_on_public_check(self):
        self.bind()
        workers.executeQuarantineBatch(self.settings, self.runId, self.token, self.apiToken)
        self.assertEqual(1, self.moves)
        self.assertIsNone(self.calls[0][2])
        self.assertTrue(all(value[2] == "Bearer " + self.apiToken for value in self.calls[1:]))
        self.assertGreaterEqual(min(b[1]-a[1] for a,b in zip(self.calls, self.calls[1:])), 1.9)
        self.assertEqual([("verified",)], self.state.sql("SELECT state FROM cloud_quarantine_outcomes"))
        self.assertEqual([("review", "awaiting_path_sync", 1, 12)], self.state.sql("SELECT status,reason,slot_held,used_rpc FROM cloud_quarantine_runs"))
        self.assertEqual([(0,)], self.state.sql("SELECT COUNT(*) FROM cloud_quarantine_rpcs WHERE phase!='responded'"))
        self.assertNotIn(self.apiToken, json.dumps(self.state.sql("SELECT * FROM cloud_quarantine_rpcs")))

    def test_home_exact_pair_keeps_native_move_proofs_and_spacing(self):
        self.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_native_move_records_exact_proof_actual_spacing_and_no_token_header_on_public_check()

    def test_home_lost_move_response_still_keeps_unknown_without_replay(self):
        self.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_lost_move_response_never_replays_or_runs_post_queries()

    def test_mixed_runtime_pair_stops_before_any_identity_or_move(self):
        self.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.2 Build 26-10-06 23:48:12")
        self.bind()
        with self.assertRaisesRegex(Exception, "runtime_not_verified"):
            workers.executeQuarantineBatch(self.settings, self.runId, self.token, self.apiToken)
        self.assertEqual(["token", "runtime"], [row[0] for row in self.calls])
        self.assertEqual(0, self.moves)
        self.assertEqual([("unknown",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs WHERE method='GetRuntimeInfo'"))

    def test_real_hidden_worker_does_not_release_global_fence_after_success(self):
        workers.superviseQuarantineWorker(self.settings, self.runId, self.token, self.apiToken)
        self.assertEqual(1, self.moves)
        self.assertEqual([("review", "awaiting_path_sync", 1, None)],
            self.state.sql("SELECT status,reason,slot_held,worker_pid FROM cloud_quarantine_runs"))

    def test_lost_move_response_never_replays_or_runs_post_queries(self):
        self.bind()
        self.loseMove = True
        with self.assertRaisesRegex(actions.QuarantineError, "unavailable"):
            workers.executeQuarantineBatch(self.settings, self.runId, self.token, self.apiToken)
        self.assertEqual(1, self.moves)
        self.assertEqual("move", self.calls[-1][0])
        self.assertEqual([("unknown",)], self.state.sql("SELECT phase FROM cloud_quarantine_rpcs WHERE method='MoveFile'"))
        self.assertEqual([("review", 1)], self.state.sql("SELECT status,slot_held FROM cloud_quarantine_runs"))

    def test_overprivileged_token_stops_before_runtime_or_any_file_query(self):
        self.bind()
        self.permissions["allow_delete"] = True
        with self.assertRaisesRegex(Exception, "token_scope_or_permissions_invalid"):
            workers.executeQuarantineBatch(self.settings, self.runId, self.token, self.apiToken)
        self.assertEqual(["token"], [row[0] for row in self.calls])
        self.assertEqual(0, self.moves)

    def test_stop_before_spawn_cancels_only_unstarted_run(self):
        import threading
        stop = threading.Event()
        stop.set()
        with patch.object(workers, "startTaskProcess") as start:
            workers.superviseQuarantineWorker(self.settings, self.runId, self.token, self.apiToken, stop=stop)
        start.assert_not_called()
        self.assertEqual([("cancelled", 0, 0)], self.state.sql("SELECT status,slot_held,used_rpc FROM cloud_quarantine_runs"))


class QuarantineCloseTest(unittest.TestCase):
    def test_close_exception_still_clears_in_memory_secret(self):
        client = object.__new__(Cd2QuarantineClient)
        class Channel:
            def close(self):
                raise RuntimeError("synthetic-close")
        client._channel, client._token, client._metadata, client._verified = Channel(), "synthetic", ("synthetic",), True
        with self.assertRaises(RuntimeError):
            client.close()
        self.assertEqual(("", (), False), (client._token, client._metadata, client._verified))


class QuarantineRecoveryTlsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Cd2QuarantineClientTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_hidden_read_only_worker_records_positions_without_move_or_original_rewrite(self):
        from app.service import cloud_quarantine_recovery_service as recovery
        f = self.fixture
        f.bind()
        rpcId = actions.reserveQuarantineRpc(f.settings,f.runId,f.token,"GetApiTokenInfo")
        actions.markQuarantineUnknown(f.settings,f.runId,f.token,rpcId)
        f.state.sql("UPDATE cloud_quarantine_runs SET worker_pid=NULL WHERE id=?", (f.runId,))
        f.state.sql("UPDATE cloud_drive_budget_pool SET cooldown_until=0")
        observedId,token = recovery.approveQuarantineObservation(f.settings,f.runId,requestId=str(uuid4()),
            expectedRevision=actions.getQuarantineRun(f.settings,f.runId,f.fixture.actor.id)["revision"],
            confirmMaintenance=True,confirmRemoteIdle=True,auditContext=f.fixture.context)
        f.permissions = {"allow_list":True,"allow_view_runtime_info":True}
        workers.superviseQuarantineWorker(f.settings,observedId,token,f.apiToken)
        self.assertEqual(0,f.moves)
        self.assertEqual([("review","positions_observed",0,None)],f.state.sql("SELECT status,reason,slot_held,worker_pid FROM cloud_quarantine_runs WHERE id=?", (observedId,)))
        self.assertEqual([("unknown",None)],f.state.sql("SELECT phase,reviewed_by_run FROM cloud_quarantine_rpcs WHERE run_id=?", (f.runId,)))

    def test_forward_commit_reverse_new_worker_preserves_id_and_file_name(self):
        from app.service import cloud_quarantine_recovery_service as recovery
        f = self.fixture
        workers.superviseQuarantineWorker(f.settings,f.runId,f.token,f.apiToken)
        value = actions.getQuarantineRun(f.settings,f.runId,f.fixture.actor.id)
        self.assertEqual(1,recovery.commitQuarantineLocal(f.settings,f.runId,expectedRevision=value["revision"],confirmLocalOnly=True,actorId=f.fixture.actor.id))
        reverse = recovery.createReverseQuarantinePlan(f.settings,f.plan["id"],requestId=str(uuid4()),expectedRevision=0,auditContext=f.fixture.context)
        runId,token = actions.approveQuarantineRun(f.settings,reverse["id"],requestId=str(uuid4()),expectedRevision=0,
            policyHash=reverse["policyHash"],confirmMaintenance=True,confirmGlobalFence=True,auditContext=f.fixture.context)
        workers.superviseQuarantineWorker(f.settings,runId,token,f.apiToken)
        self.assertEqual(2,f.moves)
        self.assertEqual("awaiting_path_sync",actions.getQuarantineRun(f.settings,runId,f.fixture.actor.id)["reason"])
        self.assertEqual(f.plan["items"][0]["remoteId"],f.objects["/"+f.plan["items"][0]["sourceName"]][0])
        value = actions.getQuarantineRun(f.settings,runId,f.fixture.actor.id)
        self.assertEqual(2,recovery.commitQuarantineLocal(f.settings,runId,expectedRevision=value["revision"],confirmLocalOnly=True,actorId=f.fixture.actor.id))

    def test_home_read_only_observation_preserves_original_unknown(self):
        self.fixture.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_hidden_read_only_worker_records_positions_without_move_or_original_rewrite()

    def test_home_reverse_requires_new_plan_and_preserves_file_identity(self):
        self.fixture.version = ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00")
        self.test_forward_commit_reverse_new_worker_preserves_id_and_file_name()
