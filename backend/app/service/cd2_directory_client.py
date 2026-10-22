"""目录原生RPC传输层；只由受独立预算约束的进程调用，不自行重试或改名。"""

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import math
import os
import time
import unicodedata

import grpc
from google.protobuf.message import DecodeError

from app.service import cd2_metadata_protocol as metadataWire, cd2_directory_protocol as wire
from app.service.cloud_directory_admission_service import (
    reserveDirectoryRpc, recordDirectoryRpcResponse, markDirectoryRpcUnknown, _worker, _active, DirectorySpacingWait,
)
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_inventory_service import inventoryDb
from app.service.cloud_inventory_web_service import _trustedCa
from app.utils.cloud_drive_paths import canonicalCd2Target, canonicalCloudPath

_SERVICE = "/clouddrive.CloudDriveFileSrv/"
_PERMISSIONS = {"allow_list", "allow_rename", "allow_view_runtime_info"}
_SUPPORTED_RUNTIME = ("1.1.2 Build 26-10-06 23:48:14", "1.1.2 Build 26-10-06 23:48:12")
# CD2与CLOUDAPI必须整组命中已验构建，不能按主版本或分别匹配。
_SUPPORTED_RUNTIMES = frozenset({
    _SUPPORTED_RUNTIME,
    ("1.1.3 Build 26-10-17 17:13:11", "1.1.3 Build 26-10-17 17:13:00"),
})


class Cd2DirectoryError(CloudDirectoryError):
    pass


@dataclass(frozen=True)
class RemoteDirectoryIdentity:
    objectId: str
    virtualPath: str
    name: str


class DirectoryRpcAdmission:
    def __init__(self, settings, planId, token):
        self.settings, self.planId, self.token = settings, planId, token
        self.pid = os.getpid()
        with inventoryDb(settings) as connection:
            row = _worker(connection, planId, token)
            _active(settings, connection, row, time.time())
            plan = connection.execute("SELECT parent_path FROM cloud_directory_plans WHERE id=?", (planId,)).fetchone()
            self.parentPath = plan["parent_path"]

    @contextmanager
    def call(self, method, deadline, itemOrdinal=None, requestPath=None):
        if (self.pid != os.getpid() or type(deadline) not in {int, float}
                or not math.isfinite(deadline) or deadline <= time.monotonic()):
            raise Cd2DirectoryError("deadline_or_owner_invalid")
        rpcId = None
        while time.monotonic() < deadline:
            try:
                rpcId = reserveDirectoryRpc(self.settings, self.planId, self.token, method, itemOrdinal=itemOrdinal, requestPath=requestPath)
                break
            except DirectorySpacingWait as exc:
                # 未产生发送意图时按剩余间隔等待，避免每50ms抢写SQLite；取消由监督进程处理。
                time.sleep(min(exc.retryAfterSeconds, max(0, deadline - time.monotonic())))
        if rpcId is None:
            raise Cd2DirectoryError("deadline_exceeded")
        responseRecorded = False

        def responded(code, size, identity=None):
            nonlocal responseRecorded
            recordDirectoryRpcResponse(self.settings, self.planId, self.token, rpcId, responseCode=code, receivedBytes=size, identity=identity)
            responseRecorded = True

        try:
            yield responded
        finally:
            if not responseRecorded:
                # 即使没有来得及实际调用网络，也保守保留发送意图，禁止按过期/PID释放。
                markDirectoryRpcUnknown(self.settings, self.planId, self.token, rpcId)


class Cd2DirectoryClient:
    """自检、精确身份查询及逐项改名；写入必须有当前持久化前检证明。"""

    admissionType = DirectoryRpcAdmission
    allowedMethods = frozenset({"GetApiTokenInfo", "GetRuntimeInfo", "FindFileByPath", "RenameFile"})
    tokenPermissions = frozenset(_PERMISSIONS)

    def __init__(self, settings, planId, workerToken, apiToken):
        if (not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in apiToken)):
            raise ValueError("CD2临时令牌格式无效")
        self.admission = self.admissionType(settings, planId, workerToken)
        self._token = apiToken
        self._metadata = (("authorization", "Bearer " + apiToken),)
        self._verified = False
        self._closed = False
        self._channel = grpc.secure_channel(canonicalCd2Target(settings.cloudDrive.endpoint),
            grpc.ssl_channel_credentials(_trustedCa(settings)), options=(
                ("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0),
                ("grpc.max_receive_message_length", 128 * 1024), ("grpc.max_send_message_length", 16384)))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self._closed = True
        self._channel.close()
        self._token, self._metadata, self._verified = "", (), False

    def _call(self, method, request, responseType, deadline, *, itemOrdinal=None, missingAllowed=False, validate=None):
        if self._closed:
            raise Cd2DirectoryError("client_closed")
        if method not in self.allowedMethods:
            raise Cd2DirectoryError("rpc_not_allowed")
        requestPath = request.path if method == "FindFileByPath" else None
        if method == "RenameFile":
            if not self._verified or type(itemOrdinal) is not int:
                raise Cd2DirectoryError("rpc_not_allowed")
            with inventoryDb(self.admission.settings) as connection:
                item = connection.execute("SELECT source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=? AND ordinal=?", (self.admission.planId, itemOrdinal)).fetchone()
                if item is None or request.theFilePath != "/" + item["source_name"] or request.newName != item["target_name"]:
                    raise Cd2DirectoryError("path_not_in_plan")
            requestPath = request.theFilePath
        with self.admission.call(method, deadline, itemOrdinal, requestPath) as responded:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Cd2DirectoryError("deadline_exceeded")
            rpc = self._channel.unary_unary(_SERVICE + method,
                request_serializer=type(request).SerializeToString, response_deserializer=responseType.FromString)
            try:
                result = rpc(request, timeout=min(15, remaining),
                    metadata=() if method == "GetApiTokenInfo" else self._metadata, wait_for_ready=False)
            except grpc.RpcError as exc:
                if missingAllowed and exc.code() == grpc.StatusCode.NOT_FOUND:
                    responded("not_found", 0)
                    return None
                # 不记录服务端details/debug_error_string，错误也不触发低层重放。
                code = exc.code()
                raise Cd2DirectoryError(code.name.lower() if isinstance(code, grpc.StatusCode) else "rpc_result_unknown") from None
            except DecodeError:
                raise Cd2DirectoryError("invalid_response") from None
            receivedBytes = result.ByteSize()
            if method == "FindFileByPath":
                # 官方对象还有缩略图/链接/账号等扩展字段，不使用或持久化它们；按原始接收量计费。
                result.DiscardUnknownFields()
            elif method == "GetApiTokenInfo":
                result.ClearField("token")
            elif method == "RenameFile":
                result.ClearField("errorMessage")
            value = validate(result) if validate else result
            responded("rejected" if method == "RenameFile" and not result.success else "ok", receivedBytes,
                      asdict(value) if isinstance(value, RemoteDirectoryIdentity) else None)
            return value

    def verifyConnection(self, deadline):
        self._verified = False
        def token(info):
            known = metadataWire.TokenPermissions()
            known.CopyFrom(info.permissions)
            known.DiscardUnknownFields()
            if known.SerializeToString(deterministic=True) != info.permissions.SerializeToString(deterministic=True):
                raise Cd2DirectoryError("unknown_token_permissions")
            if (any(bool(getattr(info.permissions, name)) != (name in self.tokenPermissions) for name in metadataWire.PERMISSION_NAMES)
                    or info.rootDir != self.admission.parentPath or not 1 <= info.expires_in <= 600):
                raise Cd2DirectoryError("token_scope_or_permissions_invalid")

        self._call("GetApiTokenInfo", metadataWire.StringValue(value=self._token), metadataWire.TokenInfo, deadline, validate=token)

        def runtime(info):
            if (info.productVersion, info.CloudAPIVersion) not in _SUPPORTED_RUNTIMES:
                raise Cd2DirectoryError("runtime_not_verified")
            return {"productVersion": info.productVersion, "cloudApiVersion": info.CloudAPIVersion}

        result = self._call("GetRuntimeInfo", metadataWire.Empty(), metadataWire.RuntimeInfo, deadline, validate=runtime)
        self._verified = True
        return result

    def findDirectory(self, virtualPath, deadline, *, itemOrdinal=None, missingAllowed=False):
        if not self._verified:
            raise Cd2DirectoryError("connection_not_verified")
        path = canonicalCloudPath(virtualPath)
        # 首版只允许令牌父根及精确的选定源/目标，不列目录、不访问正文。
        with inventoryDb(self.admission.settings) as connection:
            approved = {"/"}
            for item in connection.execute("SELECT source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=?", (self.admission.planId,)):
                approved.update(("/" + item["source_name"], "/" + item["target_name"]))
        if path not in approved:
            raise Cd2DirectoryError("path_not_in_plan")

        def identity(raw):
            if (not raw.id or any(unicodedata.category(char).startswith("C") for char in raw.id) or len(raw.id.encode()) > 1024
                    or raw.fullPathName != path or not raw.isDirectory or raw.fileType != 0
                    or raw.isCloudRoot or raw.isForbidden or raw.isLocal or raw.isArchiveFolder or raw.isSearchResult
                    or raw.readOnly or raw.fileEncryptionType != 0 or raw.CloudAPI.readOnly or raw.CloudAPI.isLocked
                    or (path != "/" and (raw.isRoot or raw.name != path[1:]))):
                raise Cd2DirectoryError("directory_identity_unsupported")
            return RemoteDirectoryIdentity(raw.id, path, raw.name)

        return self._call("FindFileByPath", wire.FindFileByPathRequest(parentPath="/", path=path),
            wire.CloudDriveFile, deadline, itemOrdinal=itemOrdinal, missingAllowed=missingAllowed, validate=identity)

    def renameItem(self, ordinal, deadline):
        with inventoryDb(self.admission.settings) as connection:
            item = connection.execute("SELECT source_name,target_name FROM cloud_directory_plan_items WHERE plan_id=? AND ordinal=?", (self.admission.planId, ordinal)).fetchone()
        if item is None:
            raise Cd2DirectoryError("item_not_found")
        return self._call("RenameFile", wire.RenameFileRequest(theFilePath="/" + item["source_name"], newName=item["target_name"]),
            wire.FileOperationResult, deadline, itemOrdinal=ordinal, validate=lambda result: bool(result.success))
