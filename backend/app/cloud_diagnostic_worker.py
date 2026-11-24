"""显式诊断独立进程；只从同库原context恢复端点，秘密只读匿名stdin。"""

import json
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urlsplit

from app.core.config import AppSettings, LoggingSettings
from app.core.logger import setup_logging, shutdown_logging, get_logger
from app.service import cloud_connection_store as store
from app.service.cloud_connection_diagnostic_service import (
    loadDiagnosticWorker, requireDiagnosticSendTx, recordDiagnosticStage, recordDiagnosticBytes,
    finishDiagnosticObservation, safeDiagnosticReason,
)
from app.service.cloud_drive_budget_service import CloudDriveAdmission
from app.service.cd2_diagnostic_client import Cd2DiagnosticClient


def _tls(settings, sessionId, root, deadline):
    parsed = urlsplit(settings.cloudDrive.endpoint)
    context = ssl.create_default_context(cadata=settings.cloudDrive.caCertificatePem or None)
    admission = CloudDriveAdmission(settings, sessionId, endpoint=settings.cloudDrive.endpoint, rootPath=root)
    with admission(deadline) as permit:
        permit.markTransport()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise store.CloudConnectionError('deadline_exceeded')
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=remaining) as raw:
            raw.settimeout(max(0.001, deadline - time.monotonic()))
            with context.wrap_socket(raw, server_hostname=parsed.hostname):
                pass
        permit.markSucceeded()


def main():
    settings = None
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536 or not line.endswith(b'\n'):
        return 1
    phase = 'prepare'
    try:
        payload = json.loads(line)
        base = AppSettings(database_path=Path(payload['database_path']), data_dir=Path(payload['data_dir']),
            logging=LoggingSettings(log_dir=Path(payload['log_dir']), level=payload['log_level'],
                max_size_mb=payload['log_max_size_mb'], backup_count=payload['log_backup_count'], console_output=False))
        setup_logging(base.logging)
        settings = loadDiagnosticWorker(base, payload['context_id'], payload['diagnostic_id'], payload['worker_token'])
        with store.connectionDb(base) as connection:
            snapshot = requireDiagnosticSendTx(settings, connection, network=True)
            row = connection.execute('SELECT * FROM cloud_connection_diagnostics WHERE id=?', (settings.resourceId,)).fetchone()
        remaining = row['expires_at'] - time.time()
        if remaining <= 0:
            raise store.CloudConnectionError('deadline_exceeded')
        deadline = time.monotonic() + min(remaining, 30)
        root = snapshot.values['selected_path']
        if snapshot.resourceKind == 'diagnostic_tls' and 'api_token' in payload:
            raise store.CloudConnectionError('invalid_fields')
        if snapshot.resourceKind == 'diagnostic_token_runtime':
            token = payload.get('api_token')
            if (not isinstance(token, str) or not 1 <= len(token) <= 8192
                    or any(not 33 <= ord(char) <= 126 for char in token)):
                raise store.CloudConnectionError('invalid_fields')
        phase = 'tls'
        _tls(settings, row['session_id'], root, deadline)
        recordDiagnosticStage(settings, 'tls', 'passed')
        reason = None
        if snapshot.resourceKind == 'diagnostic_token_runtime':
            phase = 'token'
            def observed(stage, state, result):
                nonlocal phase
                recordDiagnosticStage(settings, stage, state, result)
                if stage == 'token' and state == 'passed':
                    phase = 'runtime'
            admission = CloudDriveAdmission(settings, row['session_id'], endpoint=settings.cloudDrive.endpoint, rootPath=root)
            with Cd2DiagnosticClient(settings.cloudDrive.endpoint, payload['api_token'], root, admission,
                    trustedCaPem=settings.cloudDrive.caCertificatePem.encode('ascii') or None,
                    onReceived=lambda size: recordDiagnosticBytes(settings, size), onStage=observed) as client:
                result = client.diagnose(timeoutSeconds=max(0, deadline - time.monotonic()))
            if result['runtimeState'] in {'unsupported', 'permission_missing'}:
                reason = 'runtime_unsupported' if result['runtimeState'] == 'unsupported' else 'permission_missing'
        finishDiagnosticObservation(settings, reason)
        return 0
    except Exception as exc:
        code = safeDiagnosticReason(getattr(exc, 'code', None))
        if isinstance(exc, ssl.SSLCertVerificationError):
            code = 'tls_expired' if exc.verify_code == 10 else 'tls_name_mismatch' if exc.verify_code in {62, 64} else 'tls_untrusted'
        elif isinstance(exc, (TimeoutError, socket.timeout)):
            code = 'deadline_exceeded'
        elif isinstance(exc, (ssl.SSLError, OSError)):
            code = 'tls_failed' if phase == 'tls' else 'unavailable'
        if settings is not None:
            try:
                if phase in {'tls', 'token', 'runtime'} and code not in {'context_stale', 'authentication_required',
                        'actor_permission_denied', 'resource_not_found', 'worker_ownership_lost', 'invalid_fields',
                        'pool_paused', 'session_expired', 'session_not_active', 'wait_timeout'}:
                    recordDiagnosticStage(settings, phase, 'failed')
                finishDiagnosticObservation(settings, code)
            except Exception as stateError:
                get_logger(__name__).error('CD2诊断原因保存失败 diagnostic_id=%s error_type=%s',
                                          settings.resourceId, type(stateError).__name__)
        get_logger(__name__).error('CD2诊断停止 diagnostic_id=%s error_type=%s code=%s',
                                  settings.resourceId if settings else 'unknown', type(exc).__name__, code)
        return 1
    finally:
        shutdown_logging()


if __name__ == '__main__':
    raise SystemExit(main())
