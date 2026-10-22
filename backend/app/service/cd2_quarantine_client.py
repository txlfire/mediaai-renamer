"""固定隔离计划的原生传输；精确查询、最小权限、显式Skip，未知不重试。"""

from contextlib import contextmanager
import math
import os
import time

import grpc
from google.protobuf.message import DecodeError

from app.service.cd2_directory_client import Cd2DirectoryClient
from app.service import cd2_quarantine_protocol as wire
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_inventory_service import inventoryDb
from app.utils.cloud_drive_paths import canonicalCloudPath

QuarantineError = actions.QuarantineError
_SERVICE = "/clouddrive.CloudDriveFileSrv/"


class QuarantineRpcAdmission:
    def __init__(self, settings, runId, workerToken):
        self.settings, self.runId, self.token, self.pid = settings, runId, workerToken, os.getpid()
        with inventoryDb(settings) as connection:
            row = actions._worker(connection, runId, workerToken)
            actions._active(settings, connection, row, time.time())
            _, self.snapshot = actions._snapshot(connection, row)
            self.parentPath = self.snapshot["parentPath"]

    @contextmanager
    def call(self, method, deadline, itemOrdinal=None, requestPath=None):
        if self.pid != os.getpid() or type(deadline) not in {int, float} or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise QuarantineError("deadline_or_owner_invalid")
        rpcId = None
        while time.monotonic() < deadline:
            try:
                rpcId = actions.reserveQuarantineRpc(self.settings, self.runId, self.token, method,
                    itemOrdinal=itemOrdinal, requestPath=requestPath)
                break
            except actions.QuarantineSpacingWait as exc:
                time.sleep(min(exc.retryAfterSeconds, max(0, deadline-time.monotonic())))
        if rpcId is None:
            raise QuarantineError("deadline_exceeded")
        recorded = False

        def responded(code, size, identity=None):
            nonlocal recorded
            actions.recordQuarantineResponse(self.settings, self.runId, self.token, rpcId,
                responseCode=code, receivedBytes=size, identity=identity)
            recorded = True
        try:
            yield responded
        finally:
            if not recorded:
                actions.markQuarantineUnknown(self.settings, self.runId, self.token, rpcId)


class Cd2QuarantineClient(Cd2DirectoryClient):
    admissionType = QuarantineRpcAdmission
    allowedMethods = frozenset(actions.METHODS)
    tokenPermissions = frozenset({"allow_list", "allow_move", "allow_view_runtime_info"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 从创建或前次响应完成起等待完整间隔，首次TLS握手不能压缩服务端收到请求的间隔。
        self._lastSent = time.monotonic()

    def _beforeSend(self, deadline, method, itemOrdinal):
        with inventoryDb(self.admission.settings) as connection:
            interval = connection.execute("SELECT min_interval FROM cloud_drive_budget_pool WHERE id=1").fetchone()[0]
        wait = interval - (time.monotonic()-self._lastSent)
        if wait > 0:
            time.sleep(min(wait, max(0, deadline-time.monotonic())))
        with inventoryDb(self.admission.settings) as connection:
            row = actions._worker(connection, self.admission.runId, self.admission.token)
            actions._active(self.admission.settings, connection, row, time.time())
            pool = connection.execute("SELECT state,cooldown_until FROM cloud_drive_budget_pool WHERE id=1").fetchone()
            if pool["state"] != "ready" or time.time() < pool["cooldown_until"]:
                raise QuarantineError("pool_not_ready")
            rpc = connection.execute("SELECT * FROM cloud_quarantine_rpcs WHERE run_id=? AND phase='sending'", (row["id"],)).fetchone()
            if rpc is None or rpc["method"] != method or rpc["item_ordinal"] != itemOrdinal:
                raise QuarantineError("rpc_state_changed")
            if method == "MoveFile":
                from app.service.cloud_quarantine_item_service import requireSendingMoveProof
                _, snapshot = actions._snapshot(connection, row)
                requireSendingMoveProof(connection,row,snapshot,rpc["id"])
        if time.monotonic() >= deadline:
            raise QuarantineError("deadline_exceeded")

    def close(self):
        try:
            self._closed = True
            self._channel.close()
        finally:
            self._token, self._metadata, self._verified = "", (), False

    def _call(self, method, request, responseType, deadline, *, itemOrdinal=None, missingAllowed=False, validate=None):
        if self._closed or method not in self.allowedMethods:
            raise QuarantineError("rpc_not_allowed")
        path = request.path if method == "FindFileByPath" else None
        if method == "MoveFile":
            if not self._verified:
                raise QuarantineError("connection_not_verified")
            _, source, _, folder = actions.itemPaths(self.admission.snapshot, itemOrdinal)
            if (list(request.theFilePaths) != [source] or request.destPath != folder
                    or not request.HasField("conflictPolicy") or request.conflictPolicy != 2
                    or not request.HasField("moveAcrossClouds") or request.moveAcrossClouds
                    or not request.HasField("handleConflictRecursively") or request.handleConflictRecursively):
                raise QuarantineError("path_or_move_policy_not_in_plan")
            path = source
        if method == "FindFileByPath":
            if not self._verified or request.parentPath != "/" or path not in self._paths():
                raise QuarantineError("path_not_in_plan")
        with self.admission.call(method, deadline, itemOrdinal, path) as responded:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise QuarantineError("deadline_exceeded")
            rpc = self._channel.unary_unary(_SERVICE+method, request_serializer=type(request).SerializeToString,
                response_deserializer=responseType.FromString)
            self._beforeSend(deadline,method,itemOrdinal)
            remaining = deadline-time.monotonic()
            try:
                self._lastSent = time.monotonic()
                result = rpc(request, timeout=min(15, remaining),
                    metadata=() if method == "GetApiTokenInfo" else self._metadata, wait_for_ready=False)
            except grpc.RpcError as exc:
                if method == "FindFileByPath" and missingAllowed and exc.code() == grpc.StatusCode.NOT_FOUND:
                    responded("not_found", 0)
                    self._lastSent = time.monotonic()
                    return None
                code = exc.code()
                raise QuarantineError(code.name.lower() if isinstance(code, grpc.StatusCode) else "rpc_result_unknown") from None
            except DecodeError:
                raise QuarantineError("invalid_response") from None
            size = result.ByteSize()
            if method == "FindFileByPath":
                result.DiscardUnknownFields()
            elif method == "GetApiTokenInfo":
                result.ClearField("token")
            elif method == "MoveFile":
                result.ClearField("errorMessage")
            value = validate(result) if validate else result
            responded("rejected" if method == "MoveFile" and not result.success else "ok", size,
                value if method == "FindFileByPath" else None)
            self._lastSent = time.monotonic()
            return value

    def _paths(self):
        snapshot = self.admission.snapshot
        result = {"/", "/"+snapshot["targetName"]}
        for ordinal in range(1, len(snapshot["items"])+1):
            _, source, target, _ = actions.itemPaths(snapshot, ordinal)
            result.update((source, target))
        return result

    def findObject(self, virtualPath, deadline, *, itemOrdinal=None, missingAllowed=False):
        path = canonicalCloudPath(virtualPath)
        if path not in self._paths() or not self._verified:
            raise QuarantineError("path_not_in_plan")
        directory = path in {"/", "/"+self.admission.snapshot["targetName"]}

        def identity(raw):
            if (raw.fullPathName != path or bool(raw.isDirectory) != directory or raw.fileType != (0 if directory else 1)
                    or raw.isCloudRoot or raw.isForbidden or raw.isLocal or raw.isArchiveFolder or raw.isSearchResult
                    or raw.readOnly or raw.fileEncryptionType or raw.CloudAPI.readOnly or raw.CloudAPI.isLocked
                    or (path != "/" and raw.isRoot)):
                raise QuarantineError("object_identity_unsupported")
            return actions._identity({"objectId": raw.id, "virtualPath": path, "name": raw.name,
                "isDirectory": directory, "size": raw.size}, path)

        return self._call("FindFileByPath", wire.FindFileByPathRequest(parentPath="/", path=path), wire.CloudDriveFile,
            deadline, itemOrdinal=itemOrdinal, missingAllowed=missingAllowed, validate=identity)

    def moveItem(self, ordinal, deadline):
        _, source, _, folder = actions.itemPaths(self.admission.snapshot, ordinal)
        return self._call("MoveFile", wire.MoveFileRequest(theFilePaths=[source], destPath=folder, conflictPolicy=2,
            moveAcrossClouds=False, handleConflictRecursively=False), wire.FileOperationResult, deadline,
            itemOrdinal=ordinal, validate=lambda response: bool(response.success))
