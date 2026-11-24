"""显式分层诊断接口；HTTPS交接秘密，查询/取消只访问本地原记录。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr, StrictBool, StrictInt, StrictStr, field_validator

from app.api.auth import require_permission, _extract_bearer_token
from app.api.cloud_connections import Payload, ConnectionRoute, _call, _failure
from app.service.cloud_task_context_service import cloudContextPolicy, _REVISION_KEYS
from app.service.cloud_connection_diagnostic_service import getCloudDiagnostic, cancelCloudDiagnostic

router = APIRouter(prefix='/api/cloud-drive', tags=['cloud-diagnostics'], route_class=ConnectionRoute)
operator = require_permission('scan:run')


class DiagnosticSource(Payload):
    requestId: StrictStr = Field(min_length=36, max_length=36)
    connectionId: StrictStr = Field(min_length=36, max_length=36)
    directoryProfileId: StrictStr = Field(min_length=36, max_length=36)
    selectedPath: StrictStr = Field(min_length=1, max_length=4096)
    policyHash: StrictStr = Field(pattern='^[a-f0-9]{64}$')
    expectedConnectionSecurityRevision: StrictInt = Field(ge=1, lt=2**63 - 1)
    expectedDirectorySecurityRevision: StrictInt = Field(ge=1, lt=2**63 - 1)
    expectedConnectionStateRevision: StrictInt = Field(ge=0, lt=2**63 - 1)
    expectedDirectoryStateRevision: StrictInt = Field(ge=0, lt=2**63 - 1)
    expectedGrantRevision: StrictInt = Field(ge=0, lt=2**63 - 1)
    expectedDirectoryGrantRevision: StrictInt = Field(ge=0, lt=2**63 - 1)
    confirmNetwork: StrictBool


class DiagnosticTls(DiagnosticSource):
    stage: Literal['tls']


class DiagnosticToken(DiagnosticSource):
    stage: Literal['token-runtime']
    apiToken: SecretStr = Field(min_length=1, max_length=8192)

    @field_validator('apiToken')
    @classmethod
    def tokenAscii(cls, value):
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError('受限Token格式无效')
        return value


def _authenticatedCall(request, user, function, *args, **kwargs):
    from app.service.cloud_drive_budget_service import CloudBudgetError, CloudBudgetUnavailable
    try:
        return _call(request, user, function, *args,
            accessToken=_extract_bearer_token(request.headers.get('authorization')), **kwargs)
    except CloudBudgetError as exc:
        unavailable = isinstance(exc, CloudBudgetUnavailable)
        return _failure('storage_unavailable' if unavailable else 'action_permission_denied' if exc.code == 'actor_permission_denied'
                        else 'global_protection_held', 503 if unavailable else 403 if exc.code == 'actor_permission_denied' else 409,
                        requestId=kwargs.get('requestId'))


@router.get('/connection-diagnostic-policy')
def policyApi(request: Request, connectionId: str, directoryProfileId: str,
              selectedPath: str = Query(min_length=1, max_length=4096), stage: Literal['tls', 'token-runtime'] = Query(...),
              user=Depends(operator)):
    return _authenticatedCall(request, user, cloudContextPolicy, connectionId=connectionId,
        directoryProfileId=directoryProfileId, selectedPath=selectedPath, stage=stage, action='diagnostic')


def _submit(settings, runner, **values):
    return runner.submit(**values)


@router.post('/connection-diagnostics')
def submitApi(payload: Annotated[DiagnosticTls | DiagnosticToken, Body(discriminator='stage')], request: Request,
              user=Depends(operator)):
    if request.url.scheme != 'https':
        return _failure('trusted_https_required')
    values = payload.model_dump()
    secret = values.pop('apiToken', None)
    expected = {key: values.pop('expected' + key[0].upper() + key[1:]) for key in _REVISION_KEYS}
    result = _authenticatedCall(request, user, _submit, request.app.state.cloudDiagnosticRunner,
        expected=expected, apiToken=secret.get_secret_value() if secret is not None else None, **values)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(result, status_code=200 if result.get('replayed') else 202)


@router.get('/connection-diagnostics/{diagnosticId}')
def resultApi(diagnosticId: str, request: Request, user=Depends(operator)):
    return _authenticatedCall(request, user, getCloudDiagnostic, diagnosticId)


@router.get('/diagnostic-requests/{requestId}')
def requestApi(requestId: str, request: Request, user=Depends(operator)):
    return _authenticatedCall(request, user, getCloudDiagnostic, requestId, byRequest=True)


@router.post('/connection-diagnostics/{diagnosticId}/cancel')
def cancelApi(diagnosticId: str, request: Request, user=Depends(operator)):
    return _authenticatedCall(request, user, cancelCloudDiagnostic, diagnosticId)
