"""只诊断受限Token与精确双Build；不列目录、不重试、不管理Token。"""

import re
import math
import time

import grpc
from google.protobuf.message import DecodeError

from app.service.cd2_metadata_client import Cd2MetadataClient, Cd2ReadError
from app.service import cd2_metadata_protocol as wire
from app.service.cd2_runtime_versions import SUPPORTED_RUNTIMES

_BUILD = re.compile(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3} Build [0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}')


class Cd2DiagnosticClient(Cd2MetadataClient):
    def __init__(self, endpoint, token, rootPath, admission, *, trustedCaPem=None, onReceived, onStage,
                 timeoutSeconds=30):
        super().__init__(endpoint, token, rootPath, admission, trustedCaPem=trustedCaPem,
                         timeoutSeconds=timeoutSeconds, maxMessageBytes=32768)
        self.receivedBytes = 0
        self.onReceived, self.onStage = onReceived, onStage
        self._diagnosed = False

    def consumeDirectory(self, *args, **kwargs):
        raise Cd2ReadError('rpc_not_allowed')

    def inspectConnection(self):
        raise Cd2ReadError('rpc_not_allowed')

    def _diagnosticCall(self, method, request, responseType, deadline, validator):
        if method not in {'GetApiTokenInfo', 'GetRuntimeInfo'}:
            raise Cd2ReadError('rpc_not_allowed')
        failure, result = None, None
        with self._admission(deadline) as permit:
            self._remaining(deadline)
            rpc = self._channel.unary_unary('/clouddrive.CloudDriveFileSrv/' + method,
                request_serializer=type(request).SerializeToString, response_deserializer=bytes)
            permit.markSending(method)
            try:
                raw = rpc(request, timeout=self._remaining(deadline),
                    metadata=() if method == 'GetApiTokenInfo' else self._metadata, wait_for_ready=False)
            except grpc.RpcError as exc:
                failure = self._rpcError(exc)
                if exc.code() not in {grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED}:
                    raise failure from None
                # 明确收到鉴权拒绝，不把它误记成结果未知；异常在准入上下文退出后再抛。
            else:
                size = len(raw)
                self.receivedBytes += size
                self.onReceived(size)
                try:
                    if size > 32768 or self.receivedBytes > 65536:
                        raise Cd2ReadError('byte_limit')
                    result = validator(responseType.FromString(raw))
                except Cd2ReadError as exc:
                    failure = exc
                except DecodeError:
                    failure = Cd2ReadError('invalid_response')
            permit.markSucceeded()
        if failure is not None:
            raise failure
        return result

    def diagnose(self, *, timeoutSeconds=None):
        if not self._lock.acquire(blocking=False):
            raise Cd2ReadError('client_busy')
        try:
            if self._diagnosed:
                raise Cd2ReadError('rpc_not_allowed')
            self._diagnosed = True
            return self._diagnose(timeoutSeconds=timeoutSeconds)
        finally:
            self._lock.release()

    def _diagnose(self, *, timeoutSeconds=None):
        if timeoutSeconds is not None and (isinstance(timeoutSeconds, bool)
                or not isinstance(timeoutSeconds, (int, float)) or not math.isfinite(timeoutSeconds)):
            raise Cd2ReadError('deadline_exceeded')
        duration = self._timeoutSeconds if timeoutSeconds is None else min(timeoutSeconds, self._timeoutSeconds)
        if not 0 < duration <= 30:
            raise Cd2ReadError('deadline_exceeded')
        deadline = time.monotonic() + duration
        canViewRuntime = self._diagnosticCall('GetApiTokenInfo', wire.StringValue(value=self._token), wire.TokenInfo,
                                    deadline, self._validateToken)
        self.onStage('token', 'passed', None)
        if not canViewRuntime:
            self.onStage('runtime', 'permission_missing', None)
            return {'tokenState': 'passed', 'runtimeState': 'permission_missing', 'coreBuild': None,
                    'cloudApiBuild': None, 'writeAvailable': False}
        def validateRuntime(response):
            builds = (response.productVersion, response.CloudAPIVersion)
            safe = all(_BUILD.fullmatch(value) for value in builds)
            supported = builds in SUPPORTED_RUNTIMES
            return {'tokenState': 'passed', 'runtimeState': 'passed' if supported else 'unsupported',
                    'coreBuild': builds[0] if safe else None, 'cloudApiBuild': builds[1] if safe else None,
                    'writeAvailable': False}
        result = self._diagnosticCall('GetRuntimeInfo', wire.Empty(), wire.RuntimeInfo, deadline, validateRuntime)
        self.onStage('runtime', result['runtimeState'], result)
        return result
