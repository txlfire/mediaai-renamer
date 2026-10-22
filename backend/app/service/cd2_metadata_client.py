"""内部只读CD2适配器，供受预算约束的独立worker使用，不由网页直接调用。"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import math
import threading
import time
from typing import Protocol

import grpc
from google.protobuf.message import DecodeError

from app.service import cd2_metadata_protocol as wire
from app.utils.cloud_drive_paths import approvedCloudRoot, canonicalCloudPath, canonicalCd2Target, isWithinCloudRoot

_SERVICE = "/clouddrive.CloudDriveFileSrv/"
_ALLOWED_PERMISSIONS = {"allow_list", "allow_view_runtime_info"}


class Cd2RpcPermit(Protocol):
    def markSending(self, method: str) -> None: ...
    def markSucceeded(self) -> None: ...
    def markIncomplete(self, reason: str) -> None: ...


@dataclass(frozen=True)
class CloudEntry:
    id: str | None
    name: str
    path: str
    isDirectory: bool
    size: int


@dataclass(frozen=True)
class DirectoryObservation:
    path: str
    entries: tuple[CloudEntry, ...]
    complete: bool
    stopReason: str
    receivedEntries: int
    receivedBytes: int
    # 不把缓存结果解释为云盘最新状态，也不把应用接收量解释为115实际流量。
    freshness: str = "unknown"


class Cd2ReadError(RuntimeError):
    def __init__(self, code: str, partialObservation: DirectoryObservation | None = None):
        super().__init__(f"CD2只读请求停止：{code}；未自动重试")
        self.code = code
        self.partialObservation = partialObservation


def _positiveInteger(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name}超出允许范围")


class Cd2MetadataClient:
    """只暴露自检和单目录有界读取；调用方必须提供带截止时间的准入上下文。

    准入覆盖全实例跨进程排队、真实请求在途及失败暂停；通过内部预算工厂创建，
    网页启动还须经过受控服务及显式配置放行。构造函数不接受主账号密码。
    """

    def __init__(self, endpoint: str, token: str, rootPath: str,
                 admission: Callable[[float], AbstractContextManager[Cd2RpcPermit]], *,
                 timeoutSeconds: float = 15, maxMessageBytes: int = 1024 * 1024,
                 trustedCaPem: bytes | None = None):
        target = canonicalCd2Target(endpoint)
        self.rootPath = approvedCloudRoot(rootPath)
        if (not isinstance(token, str) or not 1 <= len(token) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in token)):
            raise ValueError("CD2受限令牌格式无效")
        if (isinstance(timeoutSeconds, bool) or not isinstance(timeoutSeconds, (float, int))
                or not math.isfinite(timeoutSeconds) or not 0.05 <= timeoutSeconds <= 60):
            raise ValueError("CD2请求总时间须为0.05至60秒的有限数值")
        if not callable(admission):
            raise ValueError("CD2请求必须提供账号准入控制")
        _positiveInteger(maxMessageBytes, 1024, 4 * 1024 * 1024, "单消息接收上限")
        self._metadata = (("authorization", f"Bearer {token}"),)
        self._token = token
        self._timeoutSeconds = timeoutSeconds
        self._admission = admission
        self._lock = threading.Lock()
        self._closed = False
        self._channel = grpc.secure_channel(target, grpc.ssl_channel_credentials(trustedCaPem), options=(
            ("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0),
            ("grpc.max_receive_message_length", maxMessageBytes), ("grpc.max_send_message_length", 16384),
        ))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self._closed = True
        self._channel.close()
        self._metadata = ()
        self._token = ""

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Cd2ReadError("deadline_exceeded")
        if self._closed:
            raise Cd2ReadError("client_closed")
        return remaining

    def _unary(self, method, request, responseType, deadline, validator=None):
        if method not in {"GetApiTokenInfo", "GetRuntimeInfo"}:
            raise Cd2ReadError("rpc_not_allowed")
        with self._admission(deadline) as permit:
            self._remaining(deadline)
            rpc = self._channel.unary_unary(_SERVICE + method,
                request_serializer=type(request).SerializeToString, response_deserializer=responseType.FromString)
            permit.markSending(method)
            try:
                # 官方令牌自检为公共方法；附加Bearer可能被受限令牌的权限中间件提前拒绝。
                metadata = () if method == "GetApiTokenInfo" else self._metadata
                result = rpc(request, timeout=self._remaining(deadline), metadata=metadata, wait_for_ready=False)
            except grpc.RpcError as exc:
                raise self._rpcError(exc) from None
            result = validator(result) if validator else result
            self._remaining(deadline)
            permit.markSucceeded()
            return result

    def _checkToken(self, deadline):
        return self._unary("GetApiTokenInfo", wire.StringValue(value=self._token), wire.TokenInfo, deadline,
                           self._validateToken)

    def _validateToken(self, info):
        # 不能因投影不认识新权限就静默放行。标准 protobuf 保留的未知字段必须为空。
        permissions = info.permissions
        known = wire.TokenPermissions()
        known.CopyFrom(permissions)
        known.DiscardUnknownFields()
        if known.SerializeToString(deterministic=True) != permissions.SerializeToString(deterministic=True):
            raise Cd2ReadError("unknown_token_permissions")
        if (not permissions.allow_list
                or any(getattr(permissions, name) for name in wire.PERMISSION_NAMES if name not in _ALLOWED_PERMISSIONS)):
            raise Cd2ReadError("overprivileged_or_missing_list_permission")
        try:
            tokenRoot = approvedCloudRoot(info.rootDir)
        except ValueError:
            raise Cd2ReadError("token_root_mismatch") from None
        if tokenRoot != self.rootPath:
            raise Cd2ReadError("token_root_mismatch")
        return bool(permissions.allow_view_runtime_info)

    @staticmethod
    def _runtimeVersion(runtime):
        value = runtime.productVersion
        # CD2版本展示包含Build日期与时间，不要求整串为语义化版本号。
        if (not value or len(value) > 64 or value != value.strip()
                or any(char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-+_ :" for char in value)):
            raise Cd2ReadError("invalid_runtime_version")
        return value

    @staticmethod
    def _rpcError(exc: grpc.RpcError, partial=None) -> Cd2ReadError:
        # 不使用details/debug_error_string，它们可能包含服务端返回的秘密或内网地址。
        code = exc.code().name.lower() if isinstance(exc.code(), grpc.StatusCode) else "rpc_error"
        return Cd2ReadError(code, partial)

    def inspectConnection(self) -> dict:
        if not self._lock.acquire(blocking=False):
            raise Cd2ReadError("client_busy")
        try:
            deadline = time.monotonic() + self._timeoutSeconds
            canViewRuntime = self._checkToken(deadline)
            version = None
            if canViewRuntime:
                version = self._unary("GetRuntimeInfo", wire.Empty(), wire.RuntimeInfo, deadline, self._runtimeVersion)
            return {"readOnlyTokenVerified": True, "runtimeVersion": version,
                    "runtimeVersionAvailable": canViewRuntime, "writeAvailable": False}
        except grpc.RpcError as exc:
            raise self._rpcError(exc) from None
        except DecodeError:
            raise Cd2ReadError("invalid_response") from None
        finally:
            self._lock.release()

    def _entry(self, raw, directory):
        try:
            # 受限令牌的响应路径以其虚拟根为基准，先校验，再还原到批准的全局根。
            tokenPath = canonicalCloudPath(raw.fullPathName)
            path = canonicalCloudPath(self.rootPath + (tokenPath if tokenPath != "/" else ""))
        except ValueError:
            raise Cd2ReadError("invalid_entry_path") from None
        if (not isWithinCloudRoot(path, self.rootPath) or path.rpartition("/")[0] != directory
                or raw.name != path.rpartition("/")[2]
                or len(raw.name.encode("utf-8")) > 1024 or len(raw.id.encode("utf-8")) > 1024
                or raw.size < 0 or raw.fileType not in {0, 1}
                or bool(raw.isDirectory) != (raw.fileType == 0)
                or any((raw.isRoot, raw.isCloudRoot, raw.isSearchResult, raw.isForbidden, raw.isLocal, raw.isArchiveFolder))):
            raise Cd2ReadError("out_of_scope_or_unsupported_entry")
        return CloudEntry(raw.id or None, raw.name, path, bool(raw.isDirectory), raw.size)

    def readDirectory(self, path: str, *, entryLimit: int = 1000,
                      byteLimit: int = 8 * 1024 * 1024, messageLimit: int = 128) -> DirectoryObservation:
        _positiveInteger(entryLimit, 1, 10000, "目录条目上限")
        _positiveInteger(byteLimit, 1024, 32 * 1024 * 1024, "目录元数据接收上限")
        _positiveInteger(messageLimit, 1, 1024, "目录消息上限")
        return self._readDirectory(path, entryLimit, byteLimit, messageLimit)

    def consumeDirectory(self, path: str, sink, *, entryLimit: int, byteLimit: int,
                         messageLimit: int = 100000, timeoutSeconds: float | None = None) -> DirectoryObservation:
        """每消息先记接收消费，每128条交给持久化sink；不保留整目录或内存去重集。"""
        _positiveInteger(entryLimit, 1, 1000000, "流式条目上限")
        _positiveInteger(byteLimit, 1024, 256 * 1024 * 1024, "流式元数据上限")
        _positiveInteger(messageLimit, 1, 100000, "流式消息上限")
        if timeoutSeconds is not None and (isinstance(timeoutSeconds, bool) or not isinstance(timeoutSeconds, (float, int))
                or not math.isfinite(timeoutSeconds) or not 0.05 <= timeoutSeconds <= 60):
            raise ValueError("流式读取时间须为0.05至60秒的有限数值")
        return self._readDirectory(path, entryLimit, byteLimit, messageLimit, sink, timeoutSeconds)

    def _readDirectory(self, path, entryLimit, byteLimit, messageLimit, sink=None, timeoutSeconds=None):
        path = canonicalCloudPath(path)
        if not isWithinCloudRoot(path, self.rootPath):
            raise ValueError("读取目录超出已批准范围")
        # 仅传输路径相对令牌根；准入、批准和本地观察继续绑定全局绝对路径。
        tokenPath = path[len(self.rootPath):] or "/"
        if not self._lock.acquire(blocking=False):
            raise Cd2ReadError("client_busy")
        entries = []
        seen = set()
        receivedEntries = 0
        receivedBytes = 0
        processedEntries = 0
        stream = None

        def observation(reason, complete=False):
            return DirectoryObservation(path, () if sink else tuple(entries), complete, reason, receivedEntries, receivedBytes)

        def flush():
            if sink and entries:
                sink.writeEntries(tuple(entries))
                entries.clear()

        try:
            deadline = time.monotonic() + min(self._timeoutSeconds, timeoutSeconds or self._timeoutSeconds)
            self._checkToken(deadline)
            with self._admission(deadline) as permit:
                self._remaining(deadline)
                rpc = self._channel.unary_stream(_SERVICE + "GetSubFiles",
                    request_serializer=wire.ListSubFileRequest.SerializeToString,
                    response_deserializer=wire.SubFilesReply.FromString)
                # checkExpires明确为false；不请求强制刷新，但CD2仍可能访问上游。
                permit.markSending("GetSubFiles")
                stream = rpc(wire.ListSubFileRequest(path=tokenPath, forceRefresh=False, checkExpires=False),
                             timeout=self._remaining(deadline), metadata=self._metadata, wait_for_ready=False)
                try:
                    for messageCount, reply in enumerate(stream, start=1):
                        self._remaining(deadline)
                        receivedBytes += reply.ByteSize()
                        receivedEntries += len(reply.subFiles)
                        if sink:
                            sink.messageReceived(len(reply.subFiles), reply.ByteSize())
                        if receivedBytes > byteLimit:
                            flush()
                            permit.markIncomplete("byte_limit")
                            return observation("byte_limit")
                        for raw in reply.subFiles:
                            self._remaining(deadline)
                            entry = self._entry(raw, path)
                            self._remaining(deadline)
                            if sink is None:
                                if entry.path in seen:
                                    raise Cd2ReadError("duplicate_entry")
                                seen.add(entry.path)
                            entries.append(entry)
                            processedEntries += 1
                            if sink and len(entries) >= 128:
                                flush()
                            if processedEntries >= entryLimit:
                                flush()
                                permit.markIncomplete("entry_limit")
                                return observation("entry_limit")
                        if messageCount >= messageLimit:
                            flush()
                            permit.markIncomplete("message_limit")
                            return observation("message_limit")
                    self._remaining(deadline)
                    flush()
                    self._remaining(deadline)
                    permit.markSucceeded()
                    return observation("stream_finished", True)
                except grpc.RpcError as exc:
                    raise self._rpcError(exc) from None
                finally:
                    # 先取消传输流再离开准入；取消不证明CD2或115的上游工作已停止。
                    stream.cancel()
        except grpc.RpcError as exc:
            raise self._rpcError(exc, observation("rpc_error")) from None
        except DecodeError:
            raise Cd2ReadError("invalid_response", observation("invalid_response")) from None
        except Cd2ReadError as exc:
            exc.partialObservation = observation(exc.code)
            raise
        finally:
            self._lock.release()
