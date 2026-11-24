"""显式诊断监督；无后台领取循环，Token仅在本次内存和匿名管道交接。"""

import math
import os
import threading
import time

from app.core.logger import get_logger
from app.service import cloud_connection_store as store
from app.service.cloud_connection_diagnostic_service import (
    prepareCloudDiagnostic, claimCloudDiagnostic, persistDiagnosticPid, requireDiagnosticFactsTx,
    requireDiagnosticSendTx, safeDiagnosticReason,
)
from app.service.cloud_drive_budget_service import cloudDriveHostKey
from app.service.isolated_task_service import startTaskProcess, TaskProcessStartError
from app.service.scan_recovery_service import isProcessAlive


def _finish(settings, expectedPid, reason=None):
    if expectedPid is not None and isProcessAlive(expectedPid):
        raise store.CloudConnectionError('worker_ownership_lost')
    with store.connectionDb(settings.base, write=True) as connection:
        _, row = requireDiagnosticFactsTx(settings, connection)
        if settings.supervisorPid != os.getpid() or row['worker_pid'] not in {None, expectedPid}:
            raise store.CloudConnectionError('worker_ownership_lost')
        lease = connection.execute('SELECT * FROM cloud_drive_rpc_lease WHERE session_id=?', (row['session_id'],)).fetchone()
        # reserved先于应用sending；确认原子进程退出后只释放本地未发送握手槽。
        if (lease is not None and lease['phase'] == 'reserved' and lease['rpc_method'] is None
                and lease['owner_pid'] == expectedPid and lease['owner_host'] == cloudDriveHostKey()):
            connection.execute('DELETE FROM cloud_drive_rpc_lease WHERE id=?', (lease['id'],))
            lease = None
        unresolved = lease is not None
        status = 'review' if unresolved else row['status'] if row['status'] in {'completed', 'cancelled'} else 'interrupted'
        code = row['reason_code'] or (safeDiagnosticReason(reason) if reason else None)
        connection.execute('UPDATE cloud_connection_diagnostics SET status=?,reason_code=?,worker_token=NULL,worker_pid=NULL,'
            'revision=revision+1,updated_at=? WHERE id=?', (status, code, time.time(), row['id']))
        from app.service.audit_service import record_audit_event
        record_audit_event(settings, event_type='cloud_connections.diagnostic', action='diagnostic_worker_reaped',
            result='success', summary='诊断本机进程已回收；原未决及消费预算保留',
            target_type='cloud_diagnostic', target_id=row['id'], actor_name='cloud_diagnostic_supervisor',
            detail={'contextId': row['context_id'], 'processExited': True, 'unresolved': unresolved}, connection=connection)


def superviseCloudDiagnostic(settings, apiToken, stop):
    process, reason = None, None
    try:
        with store.connectionDb(settings.base) as connection:
            snapshot = requireDiagnosticSendTx(settings, connection)
            _, row = requireDiagnosticFactsTx(settings, connection)
        if stop.is_set():
            reason = 'cancel_requested'
            return
        remaining = row['expires_at'] - time.time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise store.CloudConnectionError('deadline_exceeded')
        deadline = time.monotonic() + min(remaining, 30)
        payload = {'context_id': settings.contextId, 'diagnostic_id': settings.resourceId,
                   'worker_token': settings.workerToken}
        if snapshot.resourceKind == 'diagnostic_token_runtime':
            payload['api_token'] = apiToken
        try:
            process = startTaskProcess(settings, 'app.cloud_diagnostic_worker', payload,
                                       lambda pid: persistDiagnosticPid(settings, pid))
        except TaskProcessStartError as exc:
            process = exc.process
            raise store.CloudConnectionError('worker_start_failed') from None
        finally:
            payload.pop('api_token', None)
        while process.poll() is None:
            if stop.is_set() or time.monotonic() >= deadline:
                reason = 'cancel_requested' if stop.is_set() else 'deadline_exceeded'
                break
            try:
                with store.connectionDb(settings.base) as connection:
                    requireDiagnosticSendTx(settings, connection)
            except Exception as exc:
                reason = safeDiagnosticReason(getattr(exc, 'code', None))
                break
            time.sleep(0.2)
    except Exception as exc:
        reason = safeDiagnosticReason(getattr(exc, 'code', None))
        get_logger(__name__).error('CD2诊断监督停止 diagnostic_id=%s error_type=%s code=%s',
                                  settings.resourceId, type(exc).__name__, reason)
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        _finish(settings, process.pid if process is not None else None, reason)


class CloudDiagnosticRunner:
    def __init__(self, settings):
        self.settings = settings
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread, self._open = None, False

    def open(self):
        with self._lock:
            if self._thread is not None:
                raise store.CloudConnectionError('worker_busy')
            self._open = True
            self._stop.clear()

    def submit(self, *, apiToken=None, **values):
        store.cloudId(values['requestId'])
        stage = values['stage']
        if stage not in {'tls', 'token-runtime'} or (stage == 'tls' and apiToken is not None):
            raise store.CloudConnectionError('invalid_fields')
        if stage == 'token-runtime' and (not isinstance(apiToken, str) or not 1 <= len(apiToken) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in apiToken)):
            raise store.CloudConnectionError('invalid_fields')
        with self._lock:
            if not self._open or self._stop.is_set():
                raise store.CloudConnectionError('worker_busy')
            if self._thread is not None:
                with store.connectionDb(self.settings) as connection:
                    actorId = store.requireActor(connection, values['auditContext'], 'scan:run')
                    previous = connection.execute('SELECT 1 FROM cloud_request_registry WHERE request_id=? AND actor_id=?',
                                                  (values['requestId'], actorId)).fetchone()
                if previous is None:
                    raise store.CloudConnectionError('worker_busy')
            receipt = prepareCloudDiagnostic(self.settings, **values)
            if receipt['replayed']:
                return receipt | {'queryUrl': '/api/cloud-drive/connection-diagnostics/' + receipt['id']}
            runtime = claimCloudDiagnostic(self.settings, receipt['id'])
            thread = threading.Thread(target=self._execute, args=(runtime, apiToken), name='cloud-diagnostic-supervisor', daemon=False)
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                _finish(runtime, None, 'worker_start_failed')
                raise store.CloudConnectionError('worker_start_failed') from None
            return receipt | {'operationState': 'accepted', 'networkAccess': None,
                              'queryUrl': '/api/cloud-drive/connection-diagnostics/' + receipt['id']}

    def _execute(self, runtime, apiToken):
        try:
            superviseCloudDiagnostic(runtime, apiToken, self._stop)
        except Exception as exc:
            get_logger(__name__).error('CD2诊断回收待核对 diagnostic_id=%s error_type=%s code=%s',
                runtime.resourceId, type(exc).__name__, safeDiagnosticReason(getattr(exc, 'code', None)))
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    def close(self):
        with self._lock:
            self._open = False
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join()
