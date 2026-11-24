import { apiClient } from "./client";
import { CloudConnectionRequestError, cloudConnectionRequest } from "./cloudConnections";
import type { BoundCloudSource, BoundCloudSourceSnapshot } from "./cloudBoundInventory";
import type { CloudSourceRevisions } from "./cloudDiagnostics";
import type { DirectoryExecutionPolicy } from "./cloudDirectory";
import type { QuarantinePolicy } from "./cloudQuarantine";

export type BoundDirectoryPolicy = DirectoryExecutionPolicy & BoundCloudSource & {
  revisions: CloudSourceRevisions; reasonCode: string | null; writeAvailable: false;
};
export type BoundQuarantinePolicy = QuarantinePolicy & BoundCloudSource & {
  revisions: CloudSourceRevisions; automaticRescan: false;
};
export type BoundNativeKind = "directory_execute" | "directory_observe" | "quarantine_move" | "quarantine_observe";
export type BoundNativeReceipt = {
  id: string; contextId: string; requestId: string; resourceId: string; resourceKind: BoundNativeKind;
  status: "accepted" | "running" | "review" | "ready" | "committed" | "cancelled" | "completed";
  revision: number; replayed: boolean; source: BoundCloudSourceSnapshot;
  networkAccess: false | null; operationState?: "accepted"; retryAutomatically?: false;
};
export type BoundWriteInput = {
  requestId: string; expectedRevision: number; policyHash: string;
  confirmMaintenance: true; confirmGlobalFence: true; apiToken: string;
};
export type BoundObserveInput = {
  requestId: string; expectedRevision: number; confirmMaintenance: true; confirmRemoteIdle: true; apiToken: string;
};

const uuid = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
function invalid(): never { throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false); }
function sourceParams(source: BoundCloudSource): BoundCloudSource {
  if (!uuid(source.connectionId) || !uuid(source.directoryProfileId) || typeof source.selectedPath !== "string"
      || !source.selectedPath.startsWith("/") || source.selectedPath.length > 4096) invalid();
  return { connectionId: source.connectionId, directoryProfileId: source.directoryProfileId, selectedPath: source.selectedPath };
}

export function fetchBoundDirectoryPolicy(source: BoundCloudSource): Promise<BoundDirectoryPolicy> {
  return cloudConnectionRequest(() => apiClient.get("/cloud-drive/directory-policy", { params: sourceParams(source) }));
}
export function fetchBoundQuarantinePolicy(source: BoundCloudSource): Promise<BoundQuarantinePolicy> {
  return cloudConnectionRequest(() => apiClient.get("/cloud-drive/quarantine/policy", { params: sourceParams(source) }));
}

function receipt(value: BoundNativeReceipt, kind: BoundNativeKind, requestId: string,
                 source: BoundCloudSource, targetId: string): BoundNativeReceipt {
  if (!value || !uuid(value.id) || !uuid(value.contextId) || value.id !== value.resourceId
      || value.requestId !== requestId || value.resourceKind !== kind
      || kind === "directory_execute" && value.id !== targetId
      || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.replayed !== "boolean"
      || !["accepted", "running", "review", "ready", "committed", "cancelled", "completed"].includes(value.status)
      || value.networkAccess !== false && value.networkAccess !== null
      || value.source?.connectionId !== source.connectionId || value.source?.directoryProfileId !== source.directoryProfileId
      || value.source?.selectedPath !== source.selectedPath
      || [value.source?.connectionName, value.source?.directoryName, value.source?.currentConnectionName,
        value.source?.currentDirectoryName].some(name => typeof name !== "string")
      || value.operationState !== undefined && value.operationState !== "accepted"
      || value.retryAutomatically !== undefined && value.retryAutomatically !== false) {
    // 请求可能已经受理；不把不匹配回执解释为未发出，也不自动重试。
    throw new CloudConnectionRequestError("request_unconfirmed", null, "unknown", true);
  }
  return value;
}

async function submit(targetId: string, source: BoundCloudSource, input: BoundWriteInput | BoundObserveInput,
                      kind: BoundNativeKind): Promise<BoundNativeReceipt> {
  if (typeof location === "undefined" || location.protocol !== "https:") {
    throw new CloudConnectionRequestError("trusted_https_required", null, "not_started", false);
  }
  const originalSource = sourceParams(source);
  const requestId = input.requestId;
  if (!uuid(targetId) || !uuid(requestId) || !Number.isSafeInteger(input.expectedRevision) || input.expectedRevision < 0
      || typeof input.apiToken !== "string" || !/^[!-~]{1,8192}$/.test(input.apiToken)) invalid();
  const observing = kind === "directory_observe" || kind === "quarantine_observe";
  if (input.confirmMaintenance !== true || (observing
      ? (input as BoundObserveInput).confirmRemoteIdle !== true : (input as BoundWriteInput).confirmGlobalFence !== true)) {
    throw new CloudConnectionRequestError("confirmation_required", null, "not_started", false);
  }
  if (!observing && !/^[a-f0-9]{64}$/.test((input as BoundWriteInput).policyHash)) invalid();
  const body: Record<string, string | number | boolean> = {
    requestId, expectedRevision: input.expectedRevision, confirmMaintenance: true, apiToken: input.apiToken,
    ...(observing ? { confirmRemoteIdle: true } : { confirmGlobalFence: true, policyHash: (input as BoundWriteInput).policyHash }),
  };
  const base = kind === "quarantine_observe" ? "/cloud-drive/quarantine-runs/" : kind === "quarantine_move"
    ? "/cloud-drive/quarantine-plans/" : "/cloud-drive/directory-plans/";
  const command = kind === "quarantine_observe" ? "observe" : kind === "directory_observe" ? "recoveries" : "execute";
  try {
    const result = await cloudConnectionRequest<BoundNativeReceipt>(() => apiClient.post(`${base}${encodeURIComponent(targetId)}/${command}`,
      body, { timeout: 15000 }), true);
    return receipt(result, kind, requestId, originalSource, targetId);
  } finally {
    body.apiToken = "";
  }
}

// source必须取自原计划/动作，不取当前下拉框；仅用于核对回执，绝不作为来源覆盖提交。
export const startBoundDirectoryExecution = (planId: string, source: BoundCloudSource, input: BoundWriteInput) => submit(planId, source, input, "directory_execute");
export const startBoundDirectoryRecovery = (planId: string, source: BoundCloudSource, input: BoundObserveInput) => submit(planId, source, input, "directory_observe");
export const startBoundQuarantineMove = (planId: string, source: BoundCloudSource, input: BoundWriteInput) => submit(planId, source, input, "quarantine_move");
export const startBoundQuarantineObservation = (runId: string, source: BoundCloudSource, input: BoundObserveInput) => submit(runId, source, input, "quarantine_observe");
