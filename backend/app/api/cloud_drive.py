"""云盘本地规划、观察维护及默认关闭的受控单目录盘点入口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, StrictStr, field_validator

from app.api.auth import require_permission
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
            raise HTTPException(status_code=503, detail=exc.code) from None
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


InventoryEntryId = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]


class CloudInventoryAdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    afterId: StrictInt = Field(default=0, ge=0, le=2**63 - 1)
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
def quarantinePolicyApi(request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(quarantinePlanPolicy, _cloudSettings(request), currentUser.id)


@router.get("/quarantine-targets/{reviewId}")
def quarantineTargetsApi(reviewId: str, request: Request, entryId: int = Query(ge=1,le=2**63-1),
                          currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(quarantineTargets,_cloudSettings(request),reviewId,entryId,currentUser.id)


@router.post("/quarantine-plans", status_code=201)
def quarantineCreateApi(payload: QuarantinePlanRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(createQuarantinePlan, _cloudSettings(request), requestId=payload.requestId,
        reviewId=payload.reviewId, entryIds=payload.entryIds, targetEntryId=payload.targetEntryId,
        auditContext=_auditContext(request, currentUser))


@router.get("/quarantine-plan-requests/{requestId}")
def quarantineRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(getQuarantinePlan, _cloudSettings(request), requestId, currentUser.id, byRequest=True)


@router.get("/quarantine-plans")
def quarantineListApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                      currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(listQuarantinePlans, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/quarantine-plans/{planId}")
def quarantineGetApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(getQuarantinePlan, _cloudSettings(request), planId, currentUser.id)


@router.post("/quarantine-plans/{planId}/cancel")
def quarantineCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(cancelQuarantinePlan, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/quarantine-plans/{planId}/execute", status_code=202)
def quarantineExecuteApi(planId: str, payload: DirectoryExecutionStartRequest, request: Request,
                         currentUser=Depends(require_permission("cloud-quarantine:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400,detail="临时Token仅允许通过可信HTTPS提交")
    runId = _directoryResponse(request.app.state.cloudQuarantineRunner.start,planId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,policyHash=payload.policyHash,
        confirmMaintenance=payload.confirmMaintenance,confirmGlobalFence=payload.confirmGlobalFence,
        apiToken=payload.apiToken.get_secret_value(),auditContext=_auditContext(request,currentUser))
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)


@router.get("/quarantine-runs/{runId}")
def quarantineRunApi(runId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),runId,currentUser.id)


@router.get("/quarantine-run-requests/{requestId}")
def quarantineRunRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),requestId,currentUser.id,byRequest=True)


@router.post("/quarantine-runs/{runId}/cancel")
def quarantineRunCancelApi(runId: str, payload: DirectoryPlanCancelRequest, request: Request,
                           currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(requestQuarantineCancel,_cloudSettings(request),runId,
        expectedRevision=payload.expectedRevision,actorId=currentUser.id,auditContext=_auditContext(request,currentUser))


@router.post("/quarantine-runs/{runId}/commit-local")
def quarantineCommitApi(runId: str, payload: DirectoryLocalCommitRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-quarantine:execute"))):
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
    observedId = _directoryResponse(request.app.state.cloudQuarantineRunner.startObservation,runId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,confirmMaintenance=payload.confirmMaintenance,
        confirmRemoteIdle=payload.confirmRemoteIdle,apiToken=payload.apiToken.get_secret_value(),auditContext=_auditContext(request,currentUser))
    return _directoryResponse(getQuarantineRun,_cloudSettings(request),observedId,currentUser.id)


@router.post("/quarantine-runs/{runId}/review-worker")
def quarantineWorkerReviewApi(runId: str, payload: CloudDirectoryPauseReleaseRequest, request: Request,
                              currentUser=Depends(require_permission("settings:write"))):
    return _directoryResponse(reviewQuarantineWorker,_cloudSettings(request),runId,
        expectedRevision=payload.expectedRevision,confirmClientsStopped=payload.confirmClientsStopped,
        confirmRemoteIdle=payload.confirmRemoteIdle,auditContext=_auditContext(request,currentUser))


@router.post("/quarantine-plans/{planId}/reverse", status_code=201)
def quarantineReverseApi(planId: str, payload: DirectoryReverseRequest, request: Request,
                         currentUser=Depends(require_permission("cloud-quarantine:plan"))):
    return _directoryResponse(createReverseQuarantinePlan,_cloudSettings(request),planId,
        requestId=payload.requestId,expectedRevision=payload.expectedRevision,auditContext=_auditContext(request,currentUser))


@router.get("/directory-policy")
def directoryPolicyApi(request: Request, _currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(directoryWebPolicy, _cloudSettings(request))


@router.post("/ad-reviews", status_code=201)
def adReviewCreateApi(payload: CloudAdReviewRequest, request: Request, currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(createCloudAdReview, _cloudSettings(request), requestId=payload.requestId,
        jobId=payload.jobId, expectedRevision=payload.expectedRevision, items=[item.model_dump() for item in payload.items],
        confirmLocalOnly=payload.confirmLocalOnly, auditContext=_auditContext(request, currentUser))


@router.get("/ad-reviews")
def adReviewListApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                    currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(listCloudAdReviews, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/ad-review-requests/{requestId}")
def adReviewRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(getCloudAdReview, _cloudSettings(request), requestId, currentUser.id, byRequest=True)


@router.get("/ad-reviews/{reviewId}")
def adReviewGetApi(reviewId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(getCloudAdReview, _cloudSettings(request), reviewId, currentUser.id)


@router.post("/directory-plans/{planId}/recoveries", status_code=202)
def directoryRecoveryStartApi(planId: str, payload: DirectoryRecoveryStartRequest, request: Request,
                              currentUser=Depends(require_permission("cloud-directory:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="只读临时Token仅允许通过可信HTTPS提交")
    recoveryId = _directoryResponse(request.app.state.cloudDirectoryRunner.startRecovery, planId,
        requestId=payload.requestId, expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        confirmMaintenance=payload.confirmMaintenance, confirmRemoteIdle=payload.confirmRemoteIdle,
        auditContext=_auditContext(request, currentUser))
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.get("/directory-recovery-requests/{requestId}")
def directoryRecoveryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(findDirectoryRecoveryRequest, _cloudSettings(request), requestId, currentUser.id)


@router.get("/directory-recoveries/{recoveryId}")
def directoryRecoveryApi(recoveryId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/cancel")
def directoryRecoveryCancelApi(recoveryId: str, payload: DirectoryPlanCancelRequest, request: Request,
                               currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(cancelDirectoryRecovery, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, actorId=currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/commit-local")
def directoryRecoveryCommitApi(recoveryId: str, payload: DirectoryRecoveryCommitRequest, request: Request,
                               currentUser=Depends(require_permission("cloud-directory:execute"))):
    _directoryResponse(commitRecoveryPositions, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, confirmCurrentPositions=payload.confirmCurrentPositions, actorId=currentUser.id)
    return _directoryResponse(getDirectoryRecovery, _cloudSettings(request), recoveryId, currentUser.id)


@router.post("/directory-recoveries/{recoveryId}/review-worker")
def directoryRecoveryWorkerReviewApi(recoveryId: str, payload: CloudDirectoryPauseReleaseRequest, request: Request,
                                     currentUser=Depends(require_permission("settings:write"))):
    return _directoryResponse(reviewDirectoryRecoveryWorker, _cloudSettings(request), recoveryId,
        expectedRevision=payload.expectedRevision, confirmClientsStopped=payload.confirmClientsStopped,
        confirmRemoteIdle=payload.confirmRemoteIdle, auditContext=_auditContext(request, currentUser))


@router.get("/directory-execution-requests/{requestId}")
def directoryExecutionRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(findDirectoryExecutionRequest, _cloudSettings(request), requestId, currentUser.id)


@router.post("/directory-plans/{planId}/execute", status_code=202)
def directoryExecuteApi(planId: str, payload: DirectoryExecutionStartRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-directory:execute"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="临时Token仅允许通过可信HTTPS入口提交")
    _directoryResponse(request.app.state.cloudDirectoryRunner.start, planId,
        requestId=payload.requestId, expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        policyHash=payload.policyHash, confirmMaintenance=payload.confirmMaintenance, confirmGlobalFence=payload.confirmGlobalFence,
        auditContext=_auditContext(request, currentUser))
    return _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)


@router.get("/directory-plans/{planId}/execution")
def directoryExecutionApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(getDirectoryExecution, _cloudSettings(request), planId, currentUser.id)


@router.post("/directory-plans/{planId}/execution/cancel")
def directoryExecutionCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                                currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(requestDirectoryCancel, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/directory-plans/{planId}/commit-local")
def directoryCommitLocalApi(planId: str, payload: DirectoryLocalCommitRequest, request: Request,
                           currentUser=Depends(require_permission("cloud-directory:execute"))):
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
    return _directoryResponse(createDirectoryPlan, _cloudSettings(request), **payload.model_dump(),
                              auditContext=_auditContext(request, currentUser))


@router.get("/directory-plans")
def directoryPlansApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                      currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(listDirectoryPlans, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.post("/directory-plans/{planId}/reverse", status_code=201)
def directoryReverseApi(planId: str, payload: DirectoryReverseRequest, request: Request,
                        currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(createReverseDirectoryPlan, _cloudSettings(request), planId,
        **payload.model_dump(), auditContext=_auditContext(request, currentUser))


@router.get("/directory-plan-requests/{requestId}")
def directoryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(directoryPlanByRequest, _cloudSettings(request), requestId, currentUser.id)


@router.get("/directory-plans/{planId}")
def directoryPlanApi(planId: str, request: Request, currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(getDirectoryPlan, _cloudSettings(request), planId, currentUser.id)


@router.post("/directory-plans/{planId}/cancel")
def directoryCancelApi(planId: str, payload: DirectoryPlanCancelRequest, request: Request,
                       currentUser=Depends(require_permission("cloud-directory:plan"))):
    return _directoryResponse(cancelDirectoryPlan, _cloudSettings(request), planId,
        expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.get("/inventory-policy")
def inventoryPolicyApi(request: Request, _currentUser=Depends(require_permission("scan:run"))):
    return cloudInventoryWebPolicy(_cloudSettings(request))


@router.post("/inventory-jobs")
def inventoryPrepareApi(payload: CloudInventoryPrepareRequest, request: Request,
                        currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(prepareCloudInventoryWebJob, _cloudSettings(request), **payload.model_dump(),
                              auditContext=_auditContext(request, currentUser))


@router.get("/inventory-requests/{requestId}")
def inventoryRequestApi(requestId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(cloudInventoryWebRequest, _cloudSettings(request), requestId, currentUser.id)


@router.post("/inventory-jobs/{jobId}/start", status_code=202)
def inventoryStartApi(jobId: str, payload: CloudInventoryStartRequest, request: Request,
                      currentUser=Depends(require_permission("scan:run"))):
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="受限令牌仅允许通过HTTPS提交；请配置可信TLS入口")
    return _inventoryResponse(startCloudInventoryWebJob, _cloudSettings(request),
        request.app.state.cloudInventoryRunner, jobId, actorId=currentUser.id,
        expectedRevision=payload.expectedRevision, apiToken=payload.apiToken.get_secret_value(),
        policyHash=payload.policyHash, confirmRead=payload.confirmRead)


@router.get("/inventory-jobs/{jobId}")
def inventorySummaryApi(jobId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(cloudInventorySummary, _cloudSettings(request), jobId, currentUser.id)


@router.get("/inventory-jobs")
def inventoryJobsApi(request: Request, page: int = Query(1, ge=1, le=1000), pageSize: int = Query(20, ge=1, le=50),
                     currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(cloudInventoryJobs, _cloudSettings(request), currentUser.id, page=page, pageSize=pageSize)


@router.get("/inventory-jobs/{jobId}/entries")
def inventoryEntriesApi(jobId: str, request: Request, afterId: int = Query(0, ge=0, le=2**63 - 1),
                        limit: int = Query(100, ge=1, le=200), currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(cloudInventoryEntries, _cloudSettings(request), jobId, currentUser.id,
                              afterId=afterId, limit=limit)


@router.post("/inventory-jobs/{jobId}/cancel")
def inventoryCancelApi(jobId: str, request: Request, currentUser=Depends(require_permission("scan:run"))):
    result = _inventoryResponse(cancelCloudInventoryJob, _cloudSettings(request), jobId,
                                auditContext=_auditContext(request, currentUser))
    return JSONResponse(result, status_code=202 if result["workerSlotHeld"] else 200)


@router.get("/inventory-jobs/{jobId}/directories")
def inventoryDirectoriesApi(jobId: str, request: Request, afterId: int = Query(0, ge=0, le=2**63 - 1),
                            limit: int = Query(50, ge=1, le=200), currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(cloudInventoryDirectories, _cloudSettings(request), jobId, currentUser.id,
        afterId=afterId, limit=limit)


@router.post("/inventory-jobs/{jobId}/advice")
def inventoryAdviceApi(jobId: str, payload: CloudInventoryAdviceRequest, request: Request,
                        currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(buildCloudInventoryAdvice, _cloudSettings(request), jobId, currentUser.id,
        afterId=payload.afterId, limit=payload.limit, folderEntryIds=payload.folderEntryIds,
        keepEntryIds=payload.keepEntryIds, folderTerms=payload.folderTerms)


@router.put("/inventory-jobs/{jobId}/entries/{entryId}/keep")
def inventoryKeepApi(jobId: str, entryId: int, payload: CloudInventoryKeepRequest, request: Request,
                     currentUser=Depends(require_permission("scan:run"))):
    return _inventoryResponse(setCloudInventoryKeep, _cloudSettings(request), jobId, entryId,
        kept=payload.kept, expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))


@router.post("/inventory-jobs/{jobId}/clear-cache")
def inventoryClearApi(jobId: str, payload: CloudInventoryClearRequest, request: Request,
                      currentUser=Depends(require_permission("scan:run"))):
    result = _inventoryResponse(clearCloudInventoryCache, _cloudSettings(request), jobId,
        confirmClear=payload.confirmClear, expectedRevision=payload.expectedRevision, auditContext=_auditContext(request, currentUser))
    return JSONResponse(result, status_code=202 if result["cacheState"] == "clearing" else 200)


@router.get("/budget/status")
def budgetStatusApi(request: Request, _currentUser=Depends(require_permission("settings:write"))):
    return _budgetResponse(cloudBudgetStatus, _cloudSettings(request))


@router.post("/budget/pause")
def budgetPauseApi(request: Request, currentUser=Depends(require_permission("settings:write"))):
    return _budgetResponse(pauseCloudBudget, _cloudSettings(request), auditContext=_auditContext(request, currentUser))


@router.post("/budget/resume")
def budgetResumeApi(payload: CloudBudgetResumeRequest, request: Request,
                    currentUser=Depends(require_permission("settings:write"))):
    return _budgetResponse(resumeCloudBudget, _cloudSettings(request), expectedRevision=payload.expectedRevision,
        confirmRemoteIdle=payload.confirmRemoteIdle, auditContext=_auditContext(request, currentUser))


@router.post("/budget/release-directory-manual-pause")
def budgetReleaseDirectoryPauseApi(payload: CloudDirectoryPauseReleaseRequest, request: Request,
                                 currentUser=Depends(require_permission("settings:write"))):
    return _budgetResponse(releaseDirectoryManualPause, _cloudSettings(request), expectedRevision=payload.expectedRevision,
        confirmClientsStopped=payload.confirmClientsStopped, confirmRemoteIdle=payload.confirmRemoteIdle,
        auditContext=_auditContext(request, currentUser))


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
