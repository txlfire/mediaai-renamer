import { apiClient } from "./client";
import { CloudConnectionRequestError, cloudConnectionRequest, type CloudPage } from "./cloudConnections";

export type CloudSourceRevisions = {
  connectionSecurityRevision: number; directorySecurityRevision: number;
  connectionStateRevision: number; directoryStateRevision: number;
  grantRevision: number; directoryGrantRevision: number;
};
export type CloudDiagnosticStage = "tls" | "token-runtime";
export type CloudDiagnosticPolicy = {
  connectionId: string; directoryProfileId: string; selectedPath: string;
  revisions: CloudSourceRevisions; policyHash: string; available: boolean; reasonCode: string | null;
  limits: { rpc: number; seconds: number; bytes: number }; networkAccess: false;
};
export type CloudDiagnosticInput = {
  requestId: string; connectionId: string; directoryProfileId: string; selectedPath: string;
  revisions: CloudSourceRevisions; policyHash: string; confirmNetwork: true;
} & ({ stage: "tls"; apiToken?: never } | { stage: "token-runtime"; apiToken: string });
export type CloudDiagnosticReceipt = {
  id: string; contextId: string; requestId: string; resourceId: string;
  resourceKind: "diagnostic_tls" | "diagnostic_token_runtime";
  status: "accepted" | "running" | "completed" | "interrupted" | "cancelled" | "review";
  revision: number; replayed: boolean; networkAccess: boolean | null; queryUrl?: string;
  source: { connectionId: string; directoryProfileId: string; connectionName: string; directoryName: string;
    currentConnectionName: string; currentDirectoryName: string; selectedPath: string };
  operationState?: "accepted";
};
export type CloudDiagnosticResult = CloudDiagnosticReceipt & {
  stage: CloudDiagnosticStage; tlsState: "not_run" | "passed" | "failed";
  tokenState: "not_run" | "passed" | "failed" | "not_provided";
  runtimeState: "not_run" | "passed" | "failed" | "permission_missing" | "unsupported";
  coreBuild: string | null; cloudApiBuild: string | null; reasonCode: string | null;
  usedRpc: number; receivedBytes: number; expiresAt: number; workerSlotHeld: boolean;
  unresolved: boolean; networkAttempted: boolean | null; writeAvailable: false; executionAvailable: false;
  limits: { rpc: number; seconds: number; bytes: number };
};
export type CloudProtectedKind = "inventory" | "diagnostic_tls" | "diagnostic_token_runtime"
  | "directory_execute" | "directory_observe" | "quarantine_move" | "quarantine_observe";
export type CloudReadProtection = {
  requestId: string; resourceKind: CloudProtectedKind;
  status: string; revision: number; workerSlotHeld: boolean; requestSlotHeld: boolean;
  unresolved: boolean; stopRequested: boolean; continuationBlocked: boolean;
  networkAccess: false; retryAutomatically: false; reasonCode?: string | null;
};
export type CloudProtectionItem = Pick<CloudReadProtection, "requestId" | "resourceKind" | "status" | "revision" | "workerSlotHeld" | "requestSlotHeld">
  & { actorId: number; workerPid: number | null; ownerPid: number | null; ownerHost: string | null };
export type CloudReadWorkerReview = {
  expectedRevision: number; confirmClientsStopped: true; confirmRemoteIdle: true;
};

const diagnosticUrl = (id: string) => `/cloud-drive/connection-diagnostics/${encodeURIComponent(id)}`;
export function fetchCloudDiagnosticPolicy(connectionId: string, directoryProfileId: string, selectedPath: string,
                                            stage: CloudDiagnosticStage): Promise<CloudDiagnosticPolicy> {
  return cloudConnectionRequest(() => apiClient.get("/cloud-drive/connection-diagnostic-policy", {
    params: { connectionId, directoryProfileId, selectedPath, stage },
  }));
}

export async function submitCloudDiagnostic(input: CloudDiagnosticInput): Promise<CloudDiagnosticReceipt> {
  if (typeof location === "undefined" || location.protocol !== "https:") {
    throw new CloudConnectionRequestError("trusted_https_required", null, "not_started", false);
  }
  if (input.stage !== "tls" && input.stage !== "token-runtime" || input.stage === "tls" && "apiToken" in input
      || input.stage === "token-runtime" && (!input.apiToken || input.apiToken.length > 8192 || !/^[!-~]+$/.test(input.apiToken))) {
    throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
  }
  if (input.confirmNetwork !== true) {
    throw new CloudConnectionRequestError("confirmation_required", null, "not_started", false);
  }
  const revisions = input.revisions;
  const payload: Record<string, string | number | boolean> = {
    requestId: input.requestId, connectionId: input.connectionId, directoryProfileId: input.directoryProfileId,
    selectedPath: input.selectedPath, policyHash: input.policyHash, stage: input.stage, confirmNetwork: input.confirmNetwork,
    expectedConnectionSecurityRevision: revisions.connectionSecurityRevision,
    expectedDirectorySecurityRevision: revisions.directorySecurityRevision,
    expectedConnectionStateRevision: revisions.connectionStateRevision,
    expectedDirectoryStateRevision: revisions.directoryStateRevision,
    expectedGrantRevision: revisions.grantRevision, expectedDirectoryGrantRevision: revisions.directoryGrantRevision,
  };
  if (input.stage === "token-runtime") payload.apiToken = input.apiToken;
  try {
    return await cloudConnectionRequest(() => apiClient.post("/cloud-drive/connection-diagnostics", payload, { timeout: 15000 }), true);
  } finally {
    // 仅清除本次交接副本；页面还须在隐藏、切源和会话变化时清空自己的草稿。
    delete payload.apiToken;
  }
}

export function fetchCloudDiagnostic(id: string): Promise<CloudDiagnosticResult> {
  return cloudConnectionRequest(() => apiClient.get(diagnosticUrl(id)));
}
export function findCloudDiagnosticRequest(requestId: string): Promise<CloudDiagnosticResult> {
  return cloudConnectionRequest(() => apiClient.get(`/cloud-drive/diagnostic-requests/${encodeURIComponent(requestId)}`));
}
export function cancelCloudDiagnostic(id: string): Promise<CloudDiagnosticResult> {
  return cloudConnectionRequest(() => apiClient.post(`${diagnosticUrl(id)}/cancel`, {}), true);
}
export function fetchCloudReadProtection(requestId: string): Promise<CloudReadProtection> {
  return cloudConnectionRequest(() => apiClient.get(`/cloud-drive/intents/${encodeURIComponent(requestId)}/protection`));
}
export function stopCloudReadIntent(requestId: string): Promise<CloudReadProtection> {
  return cloudConnectionRequest(() => apiClient.post(`/cloud-drive/intents/${encodeURIComponent(requestId)}/stop`, {}), true);
}

export function fetchCloudProtections(page = 1, pageSize = 20): Promise<CloudPage<CloudProtectionItem>> {
  return cloudConnectionRequest(() => {
    if (!Number.isInteger(page) || page < 1 || page > 1000 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
    }
    return apiClient.get("/settings/cloud-drive-protections", { params: { page, pageSize } });
  });
}
export function stopManagedCloudIntent(requestId: string): Promise<CloudReadProtection> {
  return cloudConnectionRequest(() => apiClient.post(`/settings/cloud-drive-protections/${encodeURIComponent(requestId)}/stop`, {}), true);
}
export function reviewCloudReadWorker(record: Pick<CloudReadProtection, "requestId" | "resourceKind">,
                                      input: CloudReadWorkerReview): Promise<CloudReadProtection> {
  return cloudConnectionRequest(() => {
    // 原生动作只有最小停止，不允许借通用只读回收清除其独立保护。
    if (!["inventory", "diagnostic_tls", "diagnostic_token_runtime"].includes(record.resourceKind)) {
      throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false);
    }
    if (input.confirmClientsStopped !== true || input.confirmRemoteIdle !== true) {
      throw new CloudConnectionRequestError("confirmation_required", null, "not_started", false);
    }
    return apiClient.post(`/settings/cloud-drive-protections/${encodeURIComponent(record.requestId)}/review-worker`, {
      expectedRevision: input.expectedRevision, confirmClientsStopped: true, confirmRemoteIdle: true,
    });
  }, true);
}
