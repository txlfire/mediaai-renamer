"""保护摘要只含本人状态；管理者可停止后续，不借此读取文件或重放请求。"""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictBool, StrictInt

from app.api.auth import require_authenticated_user, require_permission, _extract_bearer_token
from app.api.cloud_connections import ConnectionRoute, Payload, _call
from app.service.cloud_read_protection_service import (
    cloudReadProtection, stopCloudReadIntent, listCloudReadProtections, reviewCloudReadWorker,
)

router = APIRouter(prefix='/api', tags=['cloud-read-protections'], route_class=ConnectionRoute)
authenticated, manager = require_authenticated_user(), require_permission('settings:write')


class WorkerReview(Payload):
    expectedRevision: StrictInt = Field(ge=0, lt=2**63 - 1)
    confirmClientsStopped: StrictBool
    confirmRemoteIdle: StrictBool


def _handle(request, user, function, *args, **kwargs):
    return _call(request, user, function, *args,
        accessToken=_extract_bearer_token(request.headers.get('authorization')), **kwargs)


@router.get('/cloud-drive/intents/{requestId}/protection')
def protectionApi(requestId: str, request: Request, user=Depends(authenticated)):
    return _handle(request, user, cloudReadProtection, requestId)


@router.post('/cloud-drive/intents/{requestId}/stop')
def stopApi(requestId: str, request: Request, payload: Payload | None = None, user=Depends(authenticated)):
    return _handle(request, user, stopCloudReadIntent, requestId)


@router.get('/settings/cloud-drive-protections')
def managementListApi(request: Request, user=Depends(manager), page: int = Query(1, ge=1, le=1000),
                      pageSize: int = Query(20, ge=1, le=100)):
    return _handle(request, user, listCloudReadProtections, page=page, pageSize=pageSize)


@router.post('/settings/cloud-drive-protections/{requestId}/stop')
def managementStopApi(requestId: str, request: Request, payload: Payload | None = None, user=Depends(manager)):
    return _handle(request, user, stopCloudReadIntent, requestId, manage=True)


@router.post('/settings/cloud-drive-protections/{requestId}/review-worker')
def reviewWorkerApi(requestId: str, payload: WorkerReview, request: Request, user=Depends(manager)):
    return _handle(request, user, reviewCloudReadWorker, requestId, **payload.model_dump())
