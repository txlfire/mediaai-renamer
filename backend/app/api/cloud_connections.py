"""多连接本地维护接口；只有显式迁移完成的库可用，无远端执行入口。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app.api.auth import require_authenticated_user, require_permission
from app.service.audit_service import AuditWriteContext
from app.service import cloud_connection_service as configurations
from app.service import cloud_connection_grant_service as grants
from app.service.cloud_connection_store import CloudConnectionError


def _failure(code, status=400, *, requestId=None):
    try:
        requestId = requestId if isinstance(requestId, str) and str(UUID(requestId)) == requestId else None
    except (ValueError, TypeError, AttributeError):
        requestId = None
    uncertain = code in {'storage_unavailable', 'audit_write_failed'}
    return JSONResponse(status_code=status, content={'detail': {'reasonCode': code, 'requestId': requestId,
        'networkAccess': False, 'operationState': 'unknown' if uncertain else 'not_started',
        'retryAutomatically': False}})


class ConnectionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # 只返回稳定错误码，不回显包含敏感字段的输入和Pydantic原始错误。
                return _failure('invalid_fields')
        return validated


router = APIRouter(prefix='/api', tags=['cloud-connections'], route_class=ConnectionRoute)
manager = require_permission('settings:write')
authenticated = require_authenticated_user()


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ConnectionCreate(Payload):
    requestId: StrictStr
    name: StrictStr = Field(min_length=1, max_length=80)
    endpoint: StrictStr = Field(min_length=1, max_length=4096)
    caCertificatePem: StrictStr = Field(default='', max_length=16384)


class MetadataUpdate(Payload):
    name: StrictStr = Field(min_length=1, max_length=80)
    expectedMetadataRevision: StrictInt = Field(ge=0, lt=2**63 - 1)


class ConnectionSecurity(Payload):
    endpoint: StrictStr = Field(min_length=1, max_length=4096)
    caCertificatePem: StrictStr = Field(default='', max_length=16384)
    expectedSecurityRevision: StrictInt = Field(ge=1, lt=2**63 - 1)


class Lifecycle(Payload):
    state: Literal['enabled', 'disabled', 'archived']
    expectedStateRevision: StrictInt = Field(ge=0, lt=2**63 - 1)


class ProfileCreate(Payload):
    requestId: StrictStr
    name: StrictStr = Field(min_length=1, max_length=80)
    approvedRoot: StrictStr = Field(min_length=1, max_length=4096)
    mappingKey: StrictStr = Field(min_length=1, max_length=64)
    quarantineFolderName: StrictStr = Field(default='_MediaAI_Quarantine', min_length=1, max_length=240)


class ProfilePolicy(Payload):
    webExecutionEnabled: StrictBool
    directoryRenameEnabled: StrictBool
    quarantineEnabled: StrictBool
    quarantineFolderName: StrictStr = Field(min_length=1, max_length=240)
    expectedSecurityRevision: StrictInt = Field(ge=1, lt=2**63 - 1)


class UserGrant(Payload):
    allowed: StrictBool
    directoryProfileIds: list[StrictStr] = Field(max_length=128)
    expectedGrantRevision: StrictInt = Field(ge=0, lt=2**63 - 1)


class Preference(Payload):
    connectionId: StrictStr | None
    directoryProfileId: StrictStr | None
    expectedRevision: StrictInt = Field(ge=0, lt=2**63 - 1)


class GlobalPolicy(Payload):
    minIntervalSeconds: StrictFloat | StrictInt = Field(ge=2, le=300)
    failureCooldownSeconds: StrictInt = Field(ge=60, le=86400)
    expectedRevision: StrictInt = Field(ge=0, lt=2**63 - 1)


def _call(request, user, function, *args, create=False, **kwargs):
    context = AuditWriteContext(actor=user, ipAddress=request.client.host if request.client else None,
                                userAgent=request.headers.get('user-agent'))
    try:
        value = function(request.app.state.settings, *args, auditContext=context, **kwargs)
    except CloudConnectionError as exc:
        status = 401 if exc.code == 'authentication_required' else 404 if exc.code == 'resource_not_found' else 403 if exc.code == 'action_permission_denied' else 429 if exc.code == 'capacity_reached' else 503 if exc.code in {
            'storage_unavailable', 'storage_not_migrated', 'storage_version_unsupported', 'worker_start_failed'} else 409 if exc.code in {
            'revision_conflict', 'request_context_conflict', 'root_overlap', 'endpoint_duplicate', 'name_duplicate',
            'connection_endpoint_immutable',
            'source_disabled', 'global_protection_held', 'context_stale', 'legacy_unbound',
            'multi_connection_required', 'web_execution_disabled', 'directory_execution_disabled', 'quarantine_execution_disabled',
            'worker_ownership_lost', 'worker_busy', 'worker_exit_unconfirmed'} else 400
        return _failure(exc.code, status, requestId=kwargs.get('requestId'))
    if create:
        return JSONResponse(status_code=200 if value.get('replayed') else 201, content=value)
    return value


@router.get('/settings/cloud-connections')
def connectionList(request: Request, user=Depends(manager), page: int = Query(1, ge=1, le=1000),
                   pageSize: int = Query(20, ge=1, le=100), search: str = Query('', max_length=80), state: str | None = None):
    return _call(request, user, configurations.listCloudConnections, page=page, pageSize=pageSize, search=search, state=state)


@router.post('/settings/cloud-connections')
def connectionCreate(payload: ConnectionCreate, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.createCloudConnection, create=True, **payload.model_dump())


@router.get('/settings/cloud-connections/{connectionId}')
def connectionGet(connectionId: str, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.getCloudConnection, connectionId)


@router.put('/settings/cloud-connections/{connectionId}/metadata')
def connectionMetadata(connectionId: str, payload: MetadataUpdate, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.updateCloudConnectionMetadata, connectionId, **payload.model_dump())


@router.put('/settings/cloud-connections/{connectionId}/security')
def connectionSecurity(connectionId: str, payload: ConnectionSecurity, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.publishCloudConnectionVersion, connectionId, **payload.model_dump())


@router.put('/settings/cloud-connections/{connectionId}/state')
def connectionState(connectionId: str, payload: Lifecycle, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.setCloudLifecycle, connectionId, **payload.model_dump())


@router.get('/settings/cloud-connections/{connectionId}/directories')
def profileList(connectionId: str, request: Request, user=Depends(manager), page: int = Query(1, ge=1, le=1000),
                pageSize: int = Query(20, ge=1, le=100), search: str = Query('', max_length=80)):
    return _call(request, user, configurations.listCloudDirectoryProfiles, connectionId, page=page, pageSize=pageSize, search=search)


@router.post('/settings/cloud-connections/{connectionId}/directories')
def profileCreate(connectionId: str, payload: ProfileCreate, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.createCloudDirectoryProfile, connectionId, create=True, **payload.model_dump())


@router.put('/settings/cloud-connections/{connectionId}/directories/{profileId}/metadata')
def profileMetadata(connectionId: str, profileId: str, payload: MetadataUpdate, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.updateCloudDirectoryMetadata, connectionId, profileId, **payload.model_dump())


@router.put('/settings/cloud-connections/{connectionId}/directories/{profileId}/policy')
def profilePolicy(connectionId: str, profileId: str, payload: ProfilePolicy, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.publishCloudDirectoryPolicy, connectionId, profileId, **payload.model_dump())


@router.put('/settings/cloud-connections/{connectionId}/directories/{profileId}/state')
def profileState(connectionId: str, profileId: str, payload: Lifecycle, request: Request, user=Depends(manager)):
    return _call(request, user, configurations.setCloudLifecycle, connectionId, profileId=profileId, **payload.model_dump())


@router.get('/settings/cloud-connections/{connectionId}/user-grants/{userId}')
def userGrantGet(connectionId: str, userId: int, request: Request, user=Depends(manager)):
    return _call(request, user, grants.getCloudUserGrant, connectionId, userId)


@router.put('/settings/cloud-connections/{connectionId}/user-grants/{userId}')
def userGrantPut(connectionId: str, userId: int, payload: UserGrant, request: Request, user=Depends(manager)):
    return _call(request, user, grants.replaceCloudUserGrant, connectionId, userId, **payload.model_dump())


@router.get('/cloud-drive/connection-choices')
def connectionChoices(request: Request, user=Depends(authenticated), connectionId: str | None = None,
                      page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=100), search: str = Query('', max_length=80)):
    return _call(request, user, grants.cloudConnectionChoices, connectionId=connectionId, page=page, pageSize=pageSize, search=search)


@router.get('/cloud-drive/preferences')
def preferenceGet(request: Request, user=Depends(authenticated)):
    return _call(request, user, grants.getCloudPreference)


@router.put('/cloud-drive/preferences')
def preferencePut(payload: Preference, request: Request, user=Depends(authenticated)):
    return _call(request, user, grants.saveCloudPreference, **payload.model_dump())


@router.get('/settings/cloud-drive-policy')
def globalPolicyGet(request: Request, user=Depends(manager)):
    return _call(request, user, grants.getCloudGlobalPolicy)


@router.put('/settings/cloud-drive-policy')
def globalPolicyPut(payload: GlobalPolicy, request: Request, user=Depends(manager)):
    return _call(request, user, grants.updateCloudGlobalPolicy, **payload.model_dump())
