"""云盘本地规划、观察维护及默认关闭的受控单目录盘点入口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, StrictStr, field_validator

from app.api.auth import require_permission, _extract_bearer_token
from app.api.cloud_connections import _call as _connectionCall
from app.service.cloud_connection_store import cloudMultiMode, CloudConnectionError
from app.service.cloud_task_context_service import cloudContextPolicy
from app.service.cloud_bound_inventory_service import (
    prepareBoundCloudInventory, boundCloudInventorySummary, boundCloudInventoryObservations,
    cancelBoundCloudInventory, listBoundCloudInventories,
)
from app.service.cloud_global_budget_service import (
    globalCloudBudgetStatus, pauseGlobalCloudBudget, resumeGlobalCloudBudget, reviewGlobalCloudReadLease,
)
from app.service.cloud_drive_planning_service import buildCloudScanAdvice, cloudDrivePlanningStatus
from app.service.audit_service import AuditWriteContext
from app.service.cloud_drive_budget_service import (
    CloudBudgetError, CloudBudgetUnavailable, cloudBudgetStatus, pauseCloudBudget, resumeCloudBudget,
    releaseDirectoryManualPause,
)
from app.service.cloud_inventory_service import (
    CloudInventoryError, cloudInventorySummary, cloudInventoryEntries, cloudInventoryJobs, cancelCloudInventoryJob, cloudInventoryDirectories,
)
from app.service.cloud_inventory_advice_service import buildCloudInventoryAdvice
from app.service.cloud_inventory_lifecycle_service import clearCloudInventoryCache, setCloudInventoryKeep
from app.service.cloud_ad_review_service import createCloudAdReview, getCloudAdReview, listCloudAdReviews
from app.service.cloud_inventory_web_service import (
    cloudInventoryWebPolicy, prepareCloudInventoryWebJob, cloudInventoryWebRequest, startCloudInventoryWebJob,
    startBoundCloudInventoryWebJob,
)
from app.service.cloud_directory_plan_service import (
    CloudDirectoryError, createDirectoryPlan, getDirectoryPlan, directoryPlanByRequest, cancelDirectoryPlan, listDirectoryPlans,
)
from app.service.cloud_directory_web_service import directoryWebPolicy, getDirectoryExecution, findDirectoryExecutionRequest, requestDirectoryCancel
from app.service.cloud_directory_path_service import commitDirectoryPathRevision
from app.service.cloud_directory_recovery_service import getDirectoryRecovery, findDirectoryRecoveryRequest, cancelDirectoryRecovery, commitRecoveryPositions
from app.service.cloud_directory_recovery_worker_service import reviewDirectoryRecoveryWorker
from app.service.cloud_directory_reverse_service import createReverseDirectoryPlan
from app.service.cloud_quarantine_plan_service import (
    createQuarantinePlan, getQuarantinePlan, listQuarantinePlans, cancelQuarantinePlan, quarantinePlanPolicy, quarantineTargets,
)
from app.service.cloud_quarantine_execution_service import getQuarantineRun, requestQuarantineCancel
from app.service.cloud_quarantine_recovery_service import (
    commitQuarantineLocal, commitQuarantineObservation, createReverseQuarantinePlan, reviewQuarantineWorker,
)

def _cloudSettings(request):
    from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings, CloudConfigurationError
    if not hasattr(request.state, "cloudSettings"):
        try:
            request.state.cloudSettings = resolveCloudDriveSettings(request.app.state.settings)
        except CloudConfigurationError as exc:
            raise HTTPException(status_code=409 if exc.code == "multi_connection_required" else 503, detail=exc.code) from None
    return request.state.cloudSettings


router = APIRouter(prefix="/api/cloud-drive", tags=["cloud-drive"])
CloudPath = Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class CloudScanAdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selectedPaths: list[CloudPath] = Field(min_length=1, max_length=100)
    excludedPaths: list[CloudPath] = Field(default_factory=list, max_length=100)
    mode: Literal["overview", "subtrees"] = "overview"


class CloudBudgetResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    confirmRemoteIdle: StrictBool


class CloudDirectoryPauseReleaseRequest(CloudBudgetResumeRequest):
    confirmClientsStopped: StrictBool


class CloudReadLeaseReviewRequest(CloudDirectoryPauseReleaseRequest):
    leaseId: StrictStr = Field(min_length=36, max_length=36)


InventoryEntryId = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]


class CloudInventoryAdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    afterId: StrictInt = Field(default=0, ge=0, le=2**63 - 1)
    page: StrictInt | None = Field(default=None, ge=1, le=1000000)
    limit: StrictInt = Field(default=100, ge=1, le=200)
    folderEntryIds: list[InventoryEntryId] = Field(default_factory=list, max_length=200)
    keepEntryIds: list[InventoryEntryId] = Field(default_factory=list, max_length=200)
    folderTerms: list[Annotated[StrictStr, Field(min_length=2, max_length=80)]] = Field(default_factory=list, max_length=50)


class CloudInventoryKeepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kept: StrictBool
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)


class CloudInventoryClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmClear: StrictBool
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)


class CloudAdReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entryId: InventoryEntryId
    decision: Literal["keep", "review", "suggest_quarantine", "suggest_delete"]
    reason: StrictStr = Field(default="", max_length=500)


class CloudAdReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    jobId: StrictStr = Field(min_length=36, max_length=36)
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    items: list[CloudAdReviewItem] = Field(min_length=1, max_length=100)
    confirmLocalOnly: StrictBool


class DirectoryPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    jobId: StrictStr = Field(min_length=36, max_length=36)
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    entryIds: list[InventoryEntryId] = Field(min_length=1, max_length=5)
    folderTerms: list[Annotated[StrictStr, Field(min_length=2, max_length=80)]] = Field(max_length=50)


class DirectoryPlanCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)


class QuarantinePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    reviewId: StrictStr = Field(min_length=36, max_length=36)
    entryIds: list[InventoryEntryId] = Field(min_length=1, max_length=5)
    targetEntryId: InventoryEntryId


class DirectoryReverseRequest(DirectoryPlanCancelRequest):
    requestId: StrictStr = Field(min_length=36, max_length=36)


class DirectoryExecutionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    policyHash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    confirmMaintenance: StrictBool
    confirmGlobalFence: StrictBool
    apiToken: SecretStr = Field(min_length=1, max_length=8192)

    @field_validator("apiToken")
    @classmethod
    def validateToken(cls, value):
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("CD2临时令牌格式无效")
        return value


class DirectoryLocalCommitRequest(DirectoryPlanCancelRequest):
    confirmLocalOnly: StrictBool


class DirectoryRecoveryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    confirmMaintenance: StrictBool
    confirmRemoteIdle: StrictBool
    apiToken: SecretStr = Field(min_length=1, max_length=8192)

    @field_validator("apiToken")
    @classmethod
    def validateToken(cls, value):
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("CD2只读临时令牌格式无效")
        return value


class DirectoryRecoveryCommitRequest(DirectoryPlanCancelRequest):
    confirmCurrentPositions: StrictBool


PolicyHash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


class CloudInventoryPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: StrictStr = Field(min_length=36, max_length=36)
    selectedPath: CloudPath
    policyHash: PolicyHash
    confirmRead: StrictBool
    connectionId: StrictStr | None = None
    directoryProfileId: StrictStr | None = None
    expectedConnectionSecurityRevision: StrictInt | None = Field(default=None, ge=1, lt=2**63 - 1)
    expectedDirectorySecurityRevision: StrictInt | None = Field(default=None, ge=1, lt=2**63 - 1)
    expectedConnectionStateRevision: StrictInt | None = Field(default=None, ge=0, lt=2**63 - 1)
    expectedDirectoryStateRevision: StrictInt | None = Field(default=None, ge=0, lt=2**63 - 1)
    expectedGrantRevision: StrictInt | None = Field(default=None, ge=0, lt=2**63 - 1)
    expectedDirectoryGrantRevision: StrictInt | None = Field(default=None, ge=0, lt=2**63 - 1)


class CloudInventoryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: StrictInt = Field(ge=0, le=2**63 - 1)
    policyHash: PolicyHash
    confirmRead: StrictBool
    apiToken: SecretStr = Field(min_length=1, max_length=8192)

    @field_validator("apiToken")
    @classmethod
    def validateToken(cls, value):
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("CD2受限令牌格式无效")
        return value


def _budgetResponse(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except CloudBudgetUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except CloudBudgetError as exc:
        code = 403 if exc.code == "actor_permission_denied" else 409
        raise HTTPException(status_code=code, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _auditContext(request, actor):
    return AuditWriteContext(actor, request.client.host if request.client else None, request.headers.get("user-agent"))


def _inventoryResponse(function, *args, **kwargs):
    try:
        return _budgetResponse(function, *args, **kwargs)
    except CloudInventoryError as exc:
        status = 404 if exc.code in {"job_not_found", "review_not_found"} else (503 if exc.code == "storage_unavailable" else 409)
        raise HTTPException(status_code=status, detail=str(exc)) from None


def _directoryResponse(function, *args, **kwargs):
    try:
        return _inventoryResponse(function, *args, **kwargs)
    except CloudDirectoryError as exc:
        status = 404 if exc.code in {"not_found", "entry_not_found", "execution_not_found"} else (403 if exc.code == "permission_denied" else 409)
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from None


@router.get("/quarantine/policy")
def quarantinePolicyApi(request: Request, connectionId: str | None = None, directoryProfileId: str | None = None,
                         selectedPath: str | None = Query(None, max_length=4096),
                         currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_policy_service import boundCloudActionPolicy
        return _directoryResponse(_boundCall, request, currentUser, boundCloudActionPolicy, connectionId=connectionId,
            directoryProfileId=directoryProfileId, selectedPath=selectedPath, action='quarantine')
    if any(value is not None for value in (connectionId, directoryProfileId, selectedPath)):
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _directoryResponse(quarantinePlanPolicy, _cloudSettings(request), currentUser.id)


@router.get("/quarantine-targets/{reviewId}")
def quarantineTargetsApi(reviewId: str, request: Request, entryId: int = Query(ge=1,le=2**63-1),
                          currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import boundQuarantineTargets
        return _directoryResponse(_boundCall, request, currentUser, boundQuarantineTargets, reviewId, entryId)
    return _directoryResponse(quarantineTargets,_cloudSettings(request),reviewId,entryId,currentUser.id)


@router.post("/quarantine-plans", status_code=201)
def quarantineCreateApi(payload: QuarantinePlanRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import createBoundQuarantinePlan
        result = _directoryResponse(_boundCall, request, currentUser, createBoundQuarantinePlan, **payload.model_dump())
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)
    return _directoryResponse(createQuarantinePlan, _cloudSettings(request), requestId=payload.requestId,
        reviewId=payload.reviewId, entryIds=payload.entryIds, targetEntryId=payload.targetEntryId,
        auditContext=_auditContext(request, currentUser))


@router.get("/quarantine-plan-requests/{requestId}")
def quarantineRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import getBoundQuarantinePlan
        return _directoryResponse(_boundCall, request, currentUser, getBoundQuarantinePlan, requestId, byRequest=True)
    return _directoryResponse(getQuarantinePlan, _cloudSettings(request), requestId, currentUser.id, byRequest=True)


@router.get("/quarantine-plans")
def quarantineListApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                      connectionId: str | None = None, directoryProfileId: str | None = None,
                      currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import listBoundQuarantinePlans
        return _directoryResponse(_boundCall, request, currentUser, listBoundQuarantinePlans,
            page=page, pageSize=pageSize, connectionId=connectionId, directoryProfileId=directoryProfileId)
    if connectionId is not None or directoryProfileId is not None:
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _directoryResponse(listQuarantinePlans, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/quarantine-plans/{planId}")
def quarantineGetApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import getBoundQuarantinePlan
        return _directoryResponse(_boundCall, request, currentUser, getBoundQuarantinePlan, planId)
    return _directoryResponse(getQuarantinePlan, _cloudSettings(request), planId, currentUser.id)


@router.post("/quarantine-plans/{planId}/cancel")
def quarantineCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import cancelBoundQuarantinePlan
        return _directoryResponse(_boundCall, request, currentUser, cancelBoundQuarantinePlan, planId,
            expectedRevision=payload.expectedRevision)
    return _directoryResponse(cancelQuarantinePlan, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/quarantine-plans/{planId}/execute", status_code=202)
def quarantineExecuteApi(planId: str, payload: DirectoryExecutionStartRequest, request: Request,
                         currentUser=Depends(require_permission("cloud-quarantine:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400,detail="临时Token仅允许通过可信HTTPS提交")
    if _multiMode(request):
        result = _directoryResponse(_connectionCall, request, currentUser,
            lambda _settings, **kwargs: request.app.state.cloudQuarantineRunner.startBound(planId, **kwargs),
            apiToken=payload.apiToken.get_secret_value(), accessToken=_extract_bearer_token(request.headers.get('authorization')),
            **payload.model_dump(exclude={'apiToken'}))
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 202)
    runId = _directoryResponse(request.app.state.cloudQuarantineRunner.start,planId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,policyHash=payload.policyHash,
        confirmMaintenance=payload.confirmMaintenance,confirmGlobalFence=payload.confirmGlobalFence,
        apiToken=payload.apiToken.get_secret_value(),auditContext=_auditContext(request,currentUser))
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)


@router.get("/quarantine-runs/{runId}")
def quarantineRunApi(runId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_service import getBoundQuarantineRun
        return _directoryResponse(_boundCall, request, currentUser, getBoundQuarantineRun, runId)
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)


@router.get("/quarantine-run-requests/{requestId}")
def quarantineRunRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_service import getBoundQuarantineRun
        return _directoryResponse(_boundCall, request, currentUser, getBoundQuarantineRun, requestId, byRequest=True)
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),requestId,currentUser.id,byRequest=True)


@router.post("/quarantine-runs/{runId}/cancel")
def quarantineRunCancelApi(runId: str, payload: DirectoryPlanCancelRequest, request: Request,
                           currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_service import cancelBoundQuarantineRun
        return _directoryResponse(_boundCall, request, currentUser, cancelBoundQuarantineRun, runId, **payload.model_dump())
    return _directoryResponse(requestQuarantineCancel,_cloudSettings(request),runId,
        expectedRevision=payload.expectedRevision,actorId=currentUser.id,auditContext=_auditContext(request,currentUser))


@router.post("/quarantine-runs/{runId}/commit-local")
def quarantineCommitApi(runId: str, payload: DirectoryLocalCommitRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:execute"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_service import commitBoundQuarantineRun
        return _directoryResponse(_boundCall, request, currentUser, commitBoundQuarantineRun, runId, **payload.model_dump())
    value = _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)
    commit = commitQuarantineObservation if value["kind"] == "observe" else commitQuarantineLocal
    _directoryResponse(commit,_cloudSettings(request),runId,expectedRevision=payload.expectedRevision,
        confirmLocalOnly=payload.confirmLocalOnly,actorId=currentUser.id)
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)


@router.post("/quarantine-runs/{runId}/observe", status_code=202)
def quarantineObserveApi(runId: str, payload: DirectoryRecoveryStartRequest, request: Request,
                         currentUser=Depends(require_permission("cloud-quarantine:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400,detail="只读临时Token仅允许通过可信HTTPS提交")
    if _multiMode(request):
        result = _directoryResponse(_connectionCall, request, currentUser,
            lambda _settings, **kwargs: request.app.state.cloudQuarantineRunner.startBoundObservation(runId, **kwargs),
            apiToken=payload.apiToken.get_secret_value(), accessToken=_extract_bearer_token(request.headers.get('authorization')),
            **payload.model_dump(exclude={'apiToken'}))
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 202)
    observedId = _directoryResponse(request.app.state.cloudQuarantineRunner.startObservation,runId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,confirmMaintenance=payload.confirmMaintenance,
        confirmRemoteIdle=payload.confirmRemoteIdle,apiToken=payload.apiToken.get_secret_value(),auditContext=_auditContext(request,currentUser))
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),observedId,currentUser.id)


@router.post("/quarantine-runs/{runId}/review-worker")
def quarantineWorkerReviewApi(runId: str, payload: CloudDirectoryPauseReleaseRequest, request: Request,
                              currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_service import reviewBoundQuarantineWorker
        return _directoryResponse(_boundCall, request, currentUser, reviewBoundQuarantineWorker, runId, **payload.model_dump())
    return _directoryResponse(reviewQuarantineWorker,_cloudSettings(request),runId,
        expectedRevision=payload.expectedRevision,confirmClientsStopped=payload.confirmClientsStopped,
        confirmRemoteIdle=payload.confirmRemoteIdle,auditContext=_auditContext(request,currentUser))


@router.post("/quarantine-plans/{planId}/reverse", status_code=201)
def quarantineReverseApi(planId: str, payload: DirectoryReverseRequest, request: Request,
                         currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_quarantine_plan_service import createBoundReverseQuarantinePlan
        result = _directoryResponse(_boundCall, request, currentUser, createBoundReverseQuarantinePlan, planId, **payload.model_dump())
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)
    return _directoryResponse(createReverseQuarantinePlan,_cloudSettings(request),planId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,auditContext=_auditContext(request,currentUser))


@router.get("/directory-policy")
def directoryPolicyApi(request: Request, connectionId: str | None = None, directoryProfileId: str | None = None,
                        selectedPath: str | None = Query(None, max_length=4096),
                        currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_policy_service import boundCloudActionPolicy
        return _directoryResponse(_boundCall, request, currentUser, boundCloudActionPolicy, connectionId=connectionId,
            directoryProfileId=directoryProfileId, selectedPath=selectedPath, action='directory')
    if any(value is not None for value in (connectionId, directoryProfileId, selectedPath)):
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _directoryResponse(directoryWebPolicy, _cloudSettings(request))


@router.post("/ad-reviews", status_code=201)
def adReviewCreateApi(payload: CloudAdReviewRequest, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_review_service import createBoundCloudAdReview
        result = _inventoryResponse(_boundCall, request, currentUser, createBoundCloudAdReview, requestId=payload.requestId,
            jobId=payload.jobId, expectedRevision=payload.expectedRevision, items=[item.model_dump() for item in payload.items],
            confirmLocalOnly=payload.confirmLocalOnly)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)
    return _inventoryResponse(createCloudAdReview, _cloudSettings(request), requestId=payload.requestId,
        jobId=payload.jobId, expectedRevision=payload.expectedRevision, items=[item.model_dump() for item in payload.items],
        confirmLocalOnly=payload.confirmLocalOnly, auditContext=_auditContext(request, currentUser))


@router.get("/ad-reviews")
def adReviewListApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                    connectionId: str | None = None, directoryProfileId: str | None = None,
                    currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_review_service import listBoundCloudAdReviews
        return _inventoryResponse(_boundCall, request, currentUser, listBoundCloudAdReviews,
            page=page, pageSize=pageSize, connectionId=connectionId, directoryProfileId=directoryProfileId)
    if connectionId is not None or directoryProfileId is not None:
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _inventoryResponse(listCloudAdReviews, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/ad-review-requests/{requestId}")
def adReviewRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_review_service import getBoundCloudAdReview
        return _inventoryResponse(_boundCall, request, currentUser, getBoundCloudAdReview, requestId, byRequest=True)
    return _inventoryResponse(getCloudAdReview, _cloudSettings(request), requestId, currentUser.id, byRequest=True)


@router.get("/ad-reviews/{reviewId}")
def adReviewGetApi(reviewId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_review_service import getBoundCloudAdReview
        return _inventoryResponse(_boundCall, request, currentUser, getBoundCloudAdReview, reviewId)
    return _inventoryResponse(getCloudAdReview, _cloudSettings(request), reviewId, currentUser.id)


@router.post("/directory-plans/{planId}/recoveries", status_code=202)
def directoryRecoveryStartApi(planId: str, payload: DirectoryRecoveryStartRequest, request: Request,
                              currentUser=Depends(require_permission("cloud-directory:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="只读临时Token仅允许通过可信HTTPS提交")
    if _multiMode(request):
        result = _directoryResponse(_connectionCall, request, currentUser,
            lambda _settings, **kwargs: request.app.state.cloudDirectoryRunner.startBoundRecovery(planId, **kwargs),
            apiToken=payload.apiToken.get_secret_value(), accessToken=_extract_bearer_token(request.headers.get('authorization')),
            **payload.model_dump(exclude={'apiToken'}))
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 202)
    recoveryId = _directoryResponse(request.app.state.cloudDirectoryRunner.startRecovery, planId,
        requestId=payload.requestId, expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        confirmMaintenance=payload.confirmMaintenance, confirmRemoteIdle=payload.confirmRemoteIdle,
        auditContext=_auditContext(request, currentUser))
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.get("/directory-recovery-requests/{requestId}")
def directoryRecoveryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_recovery_service import getBoundDirectoryRecovery
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryRecovery, requestId, byRequest=True)
    return _directoryResponse(findDirectoryRecoveryRequest, _cloudSettings(request), requestId, currentUser.id)


@router.get("/directory-recoveries/{recoveryId}")
def directoryRecoveryApi(recoveryId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_recovery_service import getBoundDirectoryRecovery
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryRecovery, recoveryId)
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/cancel")
def directoryRecoveryCancelApi(recoveryId: str, payload: DirectoryPlanCancelRequest, request: Request,
                               currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_recovery_service import cancelBoundDirectoryRecovery
        return _directoryResponse(_boundCall, request, currentUser, cancelBoundDirectoryRecovery, recoveryId,
            expectedRevision=payload.expectedRevision)
    return _directoryResponse(cancelDirectoryRecovery, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, actorId=currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/commit-local")
def directoryRecoveryCommitApi(recoveryId: str, payload: DirectoryRecoveryCommitRequest, request: Request,
                               currentUser=Depends(require_permission("cloud-directory:execute"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_recovery_service import commitBoundDirectoryRecovery
        return _directoryResponse(_boundCall, request, currentUser, commitBoundDirectoryRecovery, recoveryId, **payload.model_dump())
    _directoryResponse(commitRecoveryPositions, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, confirmCurrentPositions=payload.confirmCurrentPositions, actorId=currentUser.id)
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/review-worker")
def directoryRecoveryWorkerReviewApi(recoveryId: str, payload: CloudDirectoryPauseReleaseRequest, request: Request,
                                     currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_recovery_service import reviewBoundDirectoryRecoveryWorker
        return _directoryResponse(_boundCall, request, currentUser, reviewBoundDirectoryRecoveryWorker, recoveryId,
            **payload.model_dump())
    return _directoryResponse(reviewDirectoryRecoveryWorker, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, confirmClientsStopped=payload.confirmClientsStopped,
        confirmRemoteIdle=payload.confirmRemoteIdle, auditContext=_auditContext(request, currentUser))


@router.get("/directory-execution-requests/{requestId}")
def directoryExecutionRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_service import getBoundDirectoryExecution
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryExecution, requestId, byRequest=True)
    return _directoryResponse(findDirectoryExecutionRequest, _cloudSettings(request), requestId, currentUser.id)


@router.post("/directory-plans/{planId}/execute", status_code=202)
def directoryExecuteApi(planId: str, payload: DirectoryExecutionStartRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-directory:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="临时Token仅允许通过可信HTTPS入口提交")
    if _multiMode(request):
        values = payload.model_dump(exclude={'apiToken'})
        result = _directoryResponse(_connectionCall, request, currentUser,
            lambda _settings, **kwargs: request.app.state.cloudDirectoryRunner.startBound(planId, **kwargs),
            apiToken=payload.apiToken.get_secret_value(), accessToken=_extract_bearer_token(request.headers.get('authorization')),
            **values)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 202)
    _directoryResponse(request.app.state.cloudDirectoryRunner.start, planId,
        requestId=payload.requestId, expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        policyHash=payload.policyHash, confirmMaintenance=payload.confirmMaintenance, confirmGlobalFence=payload.confirmGlobalFence,
        auditContext=_auditContext(request, currentUser))
    return _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)


@router.get("/directory-plans/{planId}/execution")
def directoryExecutionApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_service import getBoundDirectoryExecution
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryExecution, planId)
    return _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)


@router.post("/directory-plans/{planId}/execution/cancel")
def directoryExecutionCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                                currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_service import cancelBoundDirectoryExecution
        return _directoryResponse(_boundCall, request, currentUser, cancelBoundDirectoryExecution, planId,
            expectedRevision=payload.expectedRevision)
    return _directoryResponse(requestDirectoryCancel, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/directory-plans/{planId}/commit-local")
def directoryCommitLocalApi(planId: str, payload: DirectoryLocalCommitRequest, request: Request,
                           currentUser=Depends(require_permission("cloud-directory:execute"))):
    if _multiMode(request):
        from app.service.cloud_bound_directory_service import commitBoundDirectoryLocal
        return _directoryResponse(_boundCall, request, currentUser, commitBoundDirectoryLocal, planId, **payload.model_dump())
    if payload.confirmLocalOnly is not True:
        raise HTTPException(status_code=400, detail="须确认仅提交本地已核验的路径修订，不重发远端动作")
    current = _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)
    if current["revision"] != payload.expectedRevision:
        raise HTTPException(status_code=409, detail="状态已变化，请先刷新原任务")
    _directoryResponse(commitDirectoryPathRevision, _cloudSettings(request), planId, actorId=currentUser.id)
    return _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)


@router.post("/directory-plans", status_code=201)
def directoryPrepareApi(payload: DirectoryPlanRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import createBoundDirectoryPlan
        result = _directoryResponse(_boundCall, request, currentUser, createBoundDirectoryPlan, **payload.model_dump())
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)
    return _directoryResponse(createDirectoryPlan, _cloudSettings(request), **payload.model_dump(),
                              auditContext=_auditContext(request, currentUser))


@router.get("/directory-plans")
def directoryPlansApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                      connectionId: str | None = None, directoryProfileId: str | None = None,
                      currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import listBoundDirectoryPlans
        return _directoryResponse(_boundCall, request, currentUser, listBoundDirectoryPlans,
            page=page, pageSize=pageSize, connectionId=connectionId, directoryProfileId=directoryProfileId)
    if connectionId is not None or directoryProfileId is not None:
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _directoryResponse(listDirectoryPlans, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.post("/directory-plans/{planId}/reverse", status_code=201)
def directoryReverseApi(planId: str, payload: DirectoryReverseRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import createBoundReverseDirectoryPlan
        result = _directoryResponse(_boundCall, request, currentUser, createBoundReverseDirectoryPlan, planId, **payload.model_dump())
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)
    return _directoryResponse(createReverseDirectoryPlan, _cloudSettings(request), planId,
        **payload.model_dump(), auditContext=_auditContext(request, currentUser))


@router.get("/directory-plan-requests/{requestId}")
def directoryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import getBoundDirectoryPlan
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryPlan, requestId, byRequest=True)
    return _directoryResponse(directoryPlanByRequest, _cloudSettings(request), requestId, currentUser.id)


@router.get("/directory-plans/{planId}")
def directoryPlanApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import getBoundDirectoryPlan
        return _directoryResponse(_boundCall, request, currentUser, getBoundDirectoryPlan, planId)
    return _directoryResponse(getDirectoryPlan, _cloudSettings(request), planId, currentUser.id)


@router.post("/directory-plans/{planId}/cancel")
def directoryCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                       currentUser=Depends(require_permission("cloud-directory:plan"))):
    if _multiMode(request):
        from app.service.cloud_bound_plan_service import cancelBoundDirectoryPlan
        return _directoryResponse(_boundCall, request, currentUser, cancelBoundDirectoryPlan,
            planId, expectedRevision=payload.expectedRevision)
    return _directoryResponse(cancelDirectoryPlan, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


def _multiMode(request):
    try:
        return cloudMultiMode(request.app.state.settings)
    except CloudConnectionError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from None


def _boundCall(request, user, function, *args, **kwargs):
    return _inventoryResponse(_connectionCall, request, user, function, *args,
        accessToken=_extract_bearer_token(request.headers.get('authorization')), **kwargs)


@router.get("/inventory-policy")
def inventoryPolicyApi(request: Request, connectionId: str | None = None, directoryProfileId: str | None = None,
                       selectedPath: str | None = Query(None, min_length=1, max_length=4096),
                       _currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        if any(value is None for value in (connectionId, directoryProfileId, selectedPath)):
            return _connectionCall(request, _currentUser, _missingSource)
        return _boundCall(request, _currentUser, cloudContextPolicy, connectionId=connectionId,
                          directoryProfileId=directoryProfileId, selectedPath=selectedPath)
    if connectionId is not None or directoryProfileId is not None:
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return cloudInventoryWebPolicy(_cloudSettings(request))


def _missingSource(*args, **kwargs):
    raise CloudConnectionError('multi_connection_required')


@router.post("/inventory-jobs")
def inventoryPrepareApi(payload: CloudInventoryPrepareRequest, request: Request,
                        currentUser=Depends(require_permission("scan:run"))):
    values = payload.model_dump()
    sourceKeys = ('connectionId', 'directoryProfileId')
    revisionKeys = ('ConnectionSecurityRevision', 'DirectorySecurityRevision', 'ConnectionStateRevision',
                    'DirectoryStateRevision', 'GrantRevision', 'DirectoryGrantRevision')
    if _multiMode(request):
        if any(values[key] is None for key in (*sourceKeys, *('expected' + key for key in revisionKeys))):
            return _connectionCall(request, currentUser, _missingSource)
        expected = {key[0].lower() + key[1:]: values.pop('expected' + key) for key in revisionKeys}
        return _boundCall(request, currentUser, prepareBoundCloudInventory, expected=expected, create=True, **values)
    if any(values.pop(key) is not None for key in sourceKeys):
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    for key in revisionKeys:
        if values.pop('expected' + key) is not None:
            raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _inventoryResponse(prepareCloudInventoryWebJob, _cloudSettings(request), **values,
                              auditContext=_auditContext(request, currentUser))


@router.get("/inventory-requests/{requestId}")
def inventoryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        return _boundCall(request, currentUser, boundCloudInventorySummary, requestId, byRequest=True)
    return _inventoryResponse(cloudInventoryWebRequest, _cloudSettings(request), requestId, currentUser.id)


@router.post("/inventory-jobs/{jobId}/start", status_code=202)
def inventoryStartApi(jobId: str, payload: CloudInventoryStartRequest, request: Request,
                      currentUser=Depends(require_permission("scan:run"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="受限令牌仅允许通过HTTPS提交；请配置可信TLS入口")
    if _multiMode(request):
        result = _boundCall(request, currentUser, startBoundCloudInventoryWebJob,
            request.app.state.cloudInventoryRunner, jobId, expectedRevision=payload.expectedRevision,
            apiToken=payload.apiToken.get_secret_value(), policyHash=payload.policyHash, confirmRead=payload.confirmRead)
        return result
    return _inventoryResponse(startCloudInventoryWebJob, _cloudSettings(request),
        request.app.state.cloudInventoryRunner, jobId, actorId=currentUser.id,
        expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        policyHash=payload.policyHash, confirmRead=payload.confirmRead)


@router.get("/inventory-jobs/{jobId}")
def inventorySummaryApi(jobId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        return _boundCall(request, currentUser, boundCloudInventorySummary, jobId)
    return _inventoryResponse(cloudInventorySummary, _cloudSettings(request), jobId, currentUser.id)


@router.get("/inventory-jobs")
def inventoryJobsApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                     connectionId: str | None = None, directoryProfileId: str | None = None,
                     currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        return _boundCall(request, currentUser, listBoundCloudInventories, page=page, pageSize=pageSize,
                          connectionId=connectionId, directoryProfileId=directoryProfileId)
    if connectionId is not None or directoryProfileId is not None:
        raise HTTPException(status_code=409, detail='storage_not_migrated')
    return _inventoryResponse(cloudInventoryJobs, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/inventory-jobs/{jobId}/entries")
def inventoryEntriesApi(jobId: str, request: Request, afterId: int = Query(0, ge=0, le=2**63 - 1),
                        limit: int = Query(100, ge=1, le=200), currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        return _inventoryResponse(_boundCall, request, currentUser, boundCloudInventoryObservations,
                                  jobId, afterId=afterId, limit=limit)
    return _inventoryResponse(cloudInventoryEntries, _cloudSettings(request), jobId, currentUser.id,
                              afterId=afterId, limit=limit)


@router.post("/inventory-jobs/{jobId}/cancel")
def inventoryCancelApi(jobId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        result = _boundCall(request, currentUser, cancelBoundCloudInventory, jobId)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=202 if result['workerSlotHeld'] else 200)
    result = _inventoryResponse(cancelCloudInventoryJob, _cloudSettings(request), jobId,
                                auditContext=_auditContext(request, currentUser))
    return JSONResponse(result, status_code=202 if result["workerSlotHeld"] else 200)


@router.get("/inventory-jobs/{jobId}/directories")
def inventoryDirectoriesApi(jobId: str, request: Request, afterId: int = Query(0, ge=0, le=2**63 - 1),
                            limit: int = Query(50, ge=1, le=200), page: int | None = Query(None, ge=1, le=1000000),
                            currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        return _inventoryResponse(_boundCall, request, currentUser, boundCloudInventoryObservations,
                                  jobId, afterId=afterId, limit=limit, directories=True, page=page)
    return _inventoryResponse(cloudInventoryDirectories, _cloudSettings(request), jobId, currentUser.id,
        afterId=afterId, limit=limit, page=page)


@router.post("/inventory-jobs/{jobId}/advice")
def inventoryAdviceApi(jobId: str, payload: CloudInventoryAdviceRequest, request: Request,
                        currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_inventory_service import boundCloudInventoryAdvice
        return _inventoryResponse(_boundCall, request, currentUser, boundCloudInventoryAdvice, jobId,
            afterId=payload.afterId, limit=payload.limit, folderEntryIds=payload.folderEntryIds,
            keepEntryIds=payload.keepEntryIds, folderTerms=payload.folderTerms, page=payload.page)
    return _inventoryResponse(buildCloudInventoryAdvice, _cloudSettings(request), jobId, currentUser.id,
        afterId=payload.afterId, limit=payload.limit, folderEntryIds=payload.folderEntryIds,
        keepEntryIds=payload.keepEntryIds, folderTerms=payload.folderTerms, page=payload.page)


@router.put("/inventory-jobs/{jobId}/entries/{entryId}/keep")
def inventoryKeepApi(jobId: str, entryId: int, payload: CloudInventoryKeepRequest, request: Request,
                     currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_inventory_service import setBoundCloudInventoryKeep
        return _inventoryResponse(_boundCall, request, currentUser, setBoundCloudInventoryKeep, jobId, entryId,
            kept=payload.kept, expectedRevision=payload.expectedRevision)
    return _inventoryResponse(setCloudInventoryKeep, _cloudSettings(request), jobId, entryId,
        kept=payload.kept, expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/inventory-jobs/{jobId}/clear-cache")
def inventoryClearApi(jobId: str, payload: CloudInventoryClearRequest, request: Request,
                      currentUser=Depends(require_permission("scan:run"))):
    if _multiMode(request):
        from app.service.cloud_bound_inventory_service import clearBoundCloudInventoryCache
        result = _inventoryResponse(_boundCall, request, currentUser, clearBoundCloudInventoryCache, jobId,
            confirmClear=payload.confirmClear, expectedRevision=payload.expectedRevision)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(result, status_code=202 if result['cacheState'] == 'clearing' else 200)
    result = _inventoryResponse(clearCloudInventoryCache, _cloudSettings(request), jobId,
        confirmClear=payload.confirmClear, expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))
    return JSONResponse(result, status_code=202 if result["cacheState"] == "clearing" else 200)


@router.get("/budget/status")
def budgetStatusApi(request: Request, _currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        return _budgetResponse(_connectionCall, request, _currentUser, globalCloudBudgetStatus)
    return _budgetResponse(cloudBudgetStatus, _cloudSettings(request))


@router.post("/budget/pause")
def budgetPauseApi(request: Request, currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        return _budgetResponse(_connectionCall, request, currentUser, pauseGlobalCloudBudget)
    return _budgetResponse(pauseCloudBudget, _cloudSettings(request), auditContext=_auditContext(request, currentUser))


@router.post("/budget/resume")
def budgetResumeApi(payload: CloudBudgetResumeRequest, request: Request,
                    currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        return _budgetResponse(_connectionCall, request, currentUser, resumeGlobalCloudBudget, **payload.model_dump())
    return _budgetResponse(resumeCloudBudget, _cloudSettings(request), expectedRevision=payload.expectedRevision,
        confirmRemoteIdle=payload.confirmRemoteIdle, auditContext=_auditContext(request, currentUser))


@router.post("/budget/release-directory-manual-pause")
def budgetReleaseDirectoryPauseApi(payload: CloudDirectoryPauseReleaseRequest, request: Request,
                                 currentUser=Depends(require_permission("settings:write"))):
    if _multiMode(request):
        from app.service.cloud_global_budget_service import releaseGlobalCloudDirectoryPause
        return _budgetResponse(_connectionCall, request, currentUser, releaseGlobalCloudDirectoryPause, **payload.model_dump())
    return _budgetResponse(releaseDirectoryManualPause, _cloudSettings(request), expectedRevision=payload.expectedRevision,
        confirmClientsStopped=payload.confirmClientsStopped, confirmRemoteIdle=payload.confirmRemoteIdle,
        auditContext=_auditContext(request, currentUser))


@router.post('/budget/review-read-lease')
def budgetReviewReadLeaseApi(payload: CloudReadLeaseReviewRequest, request: Request,
                            currentUser=Depends(require_permission('settings:write'))):
    if not _multiMode(request):
        return _connectionCall(request, currentUser, _missingSource)
    return _budgetResponse(_connectionCall, request, currentUser, reviewGlobalCloudReadLease, **payload.model_dump())


@router.get("/status")
def planningStatusApi(request: Request, _currentUser=Depends(require_permission("scan:run"))):
    return cloudDrivePlanningStatus(_cloudSettings(request).cloudDrive)


@router.post("/scan-advice")
def scanAdviceApi(payload: CloudScanAdviceRequest, request: Request,
                  _currentUser=Depends(require_permission("scan:run"))):
    settings = _cloudSettings(request).cloudDrive
    if not settings.approvedRoot:
        raise HTTPException(status_code=409, detail="云盘离线规划未配置，未创建扫描任务")
    try:
        return buildCloudScanAdvice(settings, payload.selectedPaths, payload.excludedPaths, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
