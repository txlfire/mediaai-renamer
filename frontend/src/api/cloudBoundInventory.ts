import { apiClient } from "./client";
import { CloudConnectionRequestError, cloudConnectionRequest, type CloudPage } from "./cloudConnections";
import type { CloudSourceRevisions } from "./cloudDiagnostics";
import type { InventoryJobOption, InventorySummary } from "./cloudInventory";

export type BoundCloudSource = { connectionId: string; directoryProfileId: string; selectedPath: string };
export type BoundCloudSourceSnapshot = BoundCloudSource & {
  connectionName: string; directoryName: string; currentConnectionName: string; currentDirectoryName: string;
};
export type BoundInventoryPolicy = BoundCloudSource & {
  revisions: CloudSourceRevisions; policyHash: string; available: boolean; reasonCode: string | null;
  limits: { rpc: number; seconds: number; entries: number; bytes: number; directories: number; depth: number };
  networkAccess: false;
};
export type BoundInventoryPrepare = BoundCloudSource & { requestId: string; revisions: CloudSourceRevisions; policyHash: string; confirmRead: true };
export type BoundInventoryReceipt = {
  id: string; jobId: string; contextId: string; resourceId: string; resourceKind: "inventory"; requestId: string;
  status: InventorySummary["status"]; revision: number; replayed: boolean; source: BoundCloudSourceSnapshot;
  bindingState: "bound"; networkAccess: false; executionAvailable: false; writeAvailable: false;
  limits: BoundInventoryPolicy["limits"];
};
export type BoundInventorySummary = InventorySummary & {
  contextId: string | null; requestId: string; bindingState: "bound" | "legacy_unbound";
  source: BoundCloudSourceSnapshot | null;
};
export type BoundInventoryItem = Omit<InventoryJobOption, "mode"> & {
  revision: number; contextId: string | null; bindingState: "bound" | "legacy_unbound";
  source: Omit<BoundCloudSourceSnapshot, "selectedPath"> | null;
};
export type BoundInventoryAccepted = {
  id: string; contextId: string; requestId: string; operationState: "accepted"; retryAutomatically: false;
};

function sourceParams(source: BoundCloudSource) {
  if (!source.connectionId || !source.directoryProfileId || !source.selectedPath) {
    throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false);
  }
  return { connectionId: source.connectionId, directoryProfileId: source.directoryProfileId, selectedPath: source.selectedPath };
}

export function fetchBoundInventoryPolicy(source: BoundCloudSource): Promise<BoundInventoryPolicy> {
  return cloudConnectionRequest(() => apiClient.get("/cloud-drive/inventory-policy", { params: sourceParams(source) }));
}
export function prepareBoundInventory(input: BoundInventoryPrepare): Promise<BoundInventoryReceipt> {
  return cloudConnectionRequest(() => {
    if (input.confirmRead !== true) throw new CloudConnectionRequestError("confirmation_required", null, "not_started", false);
    const revisions = input.revisions;
    return apiClient.post("/cloud-drive/inventory-jobs", {
      ...sourceParams(input), requestId: input.requestId, policyHash: input.policyHash, confirmRead: true,
      expectedConnectionSecurityRevision: revisions.connectionSecurityRevision,
      expectedDirectorySecurityRevision: revisions.directorySecurityRevision,
      expectedConnectionStateRevision: revisions.connectionStateRevision,
      expectedDirectoryStateRevision: revisions.directoryStateRevision,
      expectedGrantRevision: revisions.grantRevision, expectedDirectoryGrantRevision: revisions.directoryGrantRevision,
    });
  }, true);
}
export async function startBoundInventory(id: string, expectedRevision: number, policyHash: string, apiToken: string): Promise<BoundInventoryAccepted> {
  if (typeof location === "undefined" || location.protocol !== "https:") {
    throw new CloudConnectionRequestError("trusted_https_required", null, "not_started", false);
  }
  if (!apiToken || apiToken.length > 8192 || !/^[!-~]+$/.test(apiToken)) {
    throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
  }
  const payload = { expectedRevision, policyHash, confirmRead: true, apiToken };
  try {
    return await cloudConnectionRequest(() => apiClient.post(`/cloud-drive/inventory-jobs/${encodeURIComponent(id)}/start`, payload, { timeout: 15000 }), true);
  } finally {
    payload.apiToken = "";
    apiToken = "";
  }
}
export function fetchBoundInventories(input: { page?: number; connectionId?: string; directoryProfileId?: string } = {}): Promise<CloudPage<BoundInventoryItem>> {
  return cloudConnectionRequest(() => {
    if (input.directoryProfileId && !input.connectionId) throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false);
    return apiClient.get("/cloud-drive/inventory-jobs", { params: {
      page: input.page ?? 1, pageSize: 20, connectionId: input.connectionId, directoryProfileId: input.directoryProfileId,
    } });
  });
}
export function fetchBoundInventory(id: string): Promise<BoundInventorySummary> {
  return cloudConnectionRequest(() => apiClient.get(`/cloud-drive/inventory-jobs/${encodeURIComponent(id)}`));
}
export function findBoundInventoryRequest(requestId: string): Promise<BoundInventorySummary> {
  return cloudConnectionRequest(() => apiClient.get(`/cloud-drive/inventory-requests/${encodeURIComponent(requestId)}`));
}
