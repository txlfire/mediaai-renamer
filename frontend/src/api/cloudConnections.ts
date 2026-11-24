import { apiClient } from "./client";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { getInterfaceMessages } from "../locales/interfaceLanguage";

export type CloudSourceState = "enabled" | "disabled" | "archived";
export type CloudPage<T> = { items: T[]; total: number; page: number; pageSize: number; networkAccess: false };
export type CloudConnectionRecord = {
  id: string; name: string; state: CloudSourceState; endpoint: string;
  metadataRevision: number; stateRevision: number; securityRevision: number;
  caSha256: string | null; caCertificatePem?: string; createdAt: number; updatedAt: number;
  networkAccess: false; cloudExecutionAvailable: false; replayed?: boolean;
};
export type CloudDirectoryProfile = {
  id: string; connectionId: string; name: string; state: CloudSourceState;
  metadataRevision: number; stateRevision: number; securityRevision: number;
  approvedRoot: string; mappingKey: string; quarantineFolderName: string;
  webExecutionEnabled: boolean; directoryRenameEnabled: boolean; quarantineEnabled: boolean;
  networkAccess: false; cloudExecutionAvailable: false; replayed?: boolean;
};
export type CloudConnectionChoice = Pick<CloudConnectionRecord, "id" | "name" | "state" | "securityRevision" | "stateRevision"> & { grantRevision: number };
export type CloudDirectoryChoice = CloudDirectoryProfile & { directoryGrantRevision: number };
export type CloudUserGrant = { connectionId: string; userId: number; allowed: boolean; grantRevision: number;
  directoryProfileIds: string[]; networkAccess: false };
export type CloudPreference = { connectionId: string | null; directoryProfileId: string | null;
  revision: number; available: boolean; networkAccess: false };
export type CloudGlobalPolicy = { revision: number; minIntervalSeconds: number; failureCooldownSeconds: number; networkAccess: false };
export type CloudListQuery = { page?: number; pageSize?: number; search?: string };

const reasons = ["invalid_fields", "invalid_scope", "invalid_revision", "parent_mismatch", "authentication_required",
  "action_permission_denied", "resource_not_found", "revision_conflict", "request_context_conflict", "root_overlap",
  "endpoint_duplicate", "connection_endpoint_immutable", "name_duplicate", "source_disabled", "global_protection_held", "context_stale", "legacy_unbound",
  "worker_busy", "worker_ownership_lost", "worker_exit_unconfirmed", "multi_connection_required", "web_execution_disabled",
  "directory_execution_disabled", "quarantine_execution_disabled",
  "capacity_reached", "storage_unavailable", "storage_not_migrated", "storage_version_unsupported", "audit_write_failed",
  "trusted_https_required", "confirmation_required", "worker_start_failed"] as const;
export type CloudConnectionReason = typeof reasons[number] | "request_unconfirmed";

export class CloudConnectionRequestError extends Error {
  readonly reasonCode: CloudConnectionReason;
  readonly status: number | null;
  readonly operationState: "unknown" | "not_started";
  readonly retryAutomatically = false;

  constructor(reasonCode: CloudConnectionReason, status: number | null, operationState: "unknown" | "not_started", mutation: boolean) {
    const text = getInterfaceMessages().cloudDriveSettings;
    super(reasonCode === "revision_conflict" ? text.conflict
      : reasonCode === "connection_endpoint_immutable" ? getInterfaceMessages().cloudConnections.endpointFixed
      : reasonCode === "storage_not_migrated" ? text.storageNotMigrated
      : reasonCode === "trusted_https_required" ? getInterfaceMessages().cloudInventory.startFlow.httpsRequired
      : reasonCode === "invalid_fields" ? text.invalid
      : mutation ? text.saveUnconfirmed : text.failed);
    this.name = "CloudConnectionRequestError";
    this.reasonCode = reasonCode;
    this.status = status;
    this.operationState = operationState;
  }
}

export async function cloudConnectionRequest<T>(send: () => PromiseLike<{ data: T }>, mutation = false): Promise<T> {
  try { return (await send()).data; }
  catch (error) {
    if (isSessionChangedError(error) || error instanceof CloudConnectionRequestError) throw error;
    const response = (error as { response?: { status?: unknown; data?: { detail?: unknown } } } | null)?.response;
    const detail = response?.data?.detail;
    const record = typeof detail === "object" && detail !== null ? detail as Record<string, unknown> : {};
    const code = reasons.find(reason => reason === record.reasonCode) ?? "request_unconfirmed";
    const status = typeof response?.status === "number" && Number.isInteger(response.status)
      && response.status >= 100 && response.status <= 599 ? response.status : null;
    // 只传递有限状态，不保留Axios对象、请求载荷或服务端任意原文。
    const uncertain = code === "request_unconfirmed" || code === "storage_unavailable" || code === "audit_write_failed";
    const state = record.operationState === "not_started" && !uncertain ? "not_started" : "unknown";
    throw new CloudConnectionRequestError(code, status, state, mutation);
  }
}

const localRequest = cloudConnectionRequest;

function idPart(id: string): string {
  if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id)) {
    throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
  }
  return encodeURIComponent(id);
}

const connectionUrl = (id: string) => `/settings/cloud-connections/${idPart(id)}`;
const profileUrl = (connectionId: string, profileId: string) => `${connectionUrl(connectionId)}/directories/${idPart(profileId)}`;
function listParams(query: CloudListQuery) {
  return { page: query.page ?? 1, pageSize: query.pageSize ?? 20, search: query.search ?? "" };
}

export function fetchCloudConnections(query: CloudListQuery & { state?: CloudSourceState } = {}): Promise<CloudPage<CloudConnectionRecord>> {
  return localRequest(() => apiClient.get("/settings/cloud-connections", { params: { ...listParams(query), state: query.state } }));
}
export function fetchCloudConnection(id: string): Promise<CloudConnectionRecord> {
  return localRequest(() => apiClient.get(connectionUrl(id)));
}
export function createCloudConnection(input: { requestId: string; name: string; endpoint: string; caCertificatePem: string }): Promise<CloudConnectionRecord> {
  return localRequest(() => apiClient.post("/settings/cloud-connections", {
    requestId: input.requestId, name: input.name, endpoint: input.endpoint, caCertificatePem: input.caCertificatePem,
  }), true);
}
export function renameCloudConnection(id: string, name: string, expectedMetadataRevision: number): Promise<CloudConnectionRecord> {
  return localRequest(() => apiClient.put(`${connectionUrl(id)}/metadata`, { name, expectedMetadataRevision }), true);
}
export function updateCloudConnectionSecurity(id: string, input: { endpoint: string; caCertificatePem: string; expectedSecurityRevision: number }): Promise<CloudConnectionRecord> {
  return localRequest(() => apiClient.put(`${connectionUrl(id)}/security`, {
    endpoint: input.endpoint, caCertificatePem: input.caCertificatePem, expectedSecurityRevision: input.expectedSecurityRevision,
  }), true);
}
export function setCloudConnectionState(id: string, state: CloudSourceState, expectedStateRevision: number): Promise<CloudConnectionRecord> {
  return localRequest(() => apiClient.put(`${connectionUrl(id)}/state`, { state, expectedStateRevision }), true);
}
export function fetchCloudDirectoryProfiles(connectionId: string, query: CloudListQuery = {}): Promise<CloudPage<CloudDirectoryProfile>> {
  return localRequest(() => apiClient.get(`${connectionUrl(connectionId)}/directories`, { params: listParams(query) }));
}
export function createCloudDirectoryProfile(connectionId: string, input: {
  requestId: string; name: string; approvedRoot: string; mappingKey: string; quarantineFolderName: string;
}): Promise<CloudDirectoryProfile> {
  return localRequest(() => apiClient.post(`${connectionUrl(connectionId)}/directories`, {
    requestId: input.requestId, name: input.name, approvedRoot: input.approvedRoot,
    mappingKey: input.mappingKey, quarantineFolderName: input.quarantineFolderName,
  }), true);
}
export function renameCloudDirectoryProfile(connectionId: string, profileId: string, name: string, expectedMetadataRevision: number): Promise<CloudDirectoryProfile> {
  return localRequest(() => apiClient.put(`${profileUrl(connectionId, profileId)}/metadata`, { name, expectedMetadataRevision }), true);
}
export function updateCloudDirectoryPolicy(connectionId: string, profileId: string, input: {
  webExecutionEnabled: boolean; directoryRenameEnabled: boolean; quarantineEnabled: boolean;
  quarantineFolderName: string; expectedSecurityRevision: number;
}): Promise<CloudDirectoryProfile> {
  return localRequest(() => apiClient.put(`${profileUrl(connectionId, profileId)}/policy`, {
    webExecutionEnabled: input.webExecutionEnabled, directoryRenameEnabled: input.directoryRenameEnabled,
    quarantineEnabled: input.quarantineEnabled, quarantineFolderName: input.quarantineFolderName,
    expectedSecurityRevision: input.expectedSecurityRevision,
  }), true);
}
export function setCloudDirectoryState(connectionId: string, profileId: string, state: CloudSourceState, expectedStateRevision: number): Promise<CloudDirectoryProfile> {
  return localRequest(() => apiClient.put(`${profileUrl(connectionId, profileId)}/state`, { state, expectedStateRevision }), true);
}
function grantUrl(connectionId: string, userId: number) {
  if (!Number.isSafeInteger(userId) || userId <= 0) throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
  return `${connectionUrl(connectionId)}/user-grants/${userId}`;
}
export function fetchCloudUserGrant(connectionId: string, userId: number): Promise<CloudUserGrant> {
  return localRequest(() => apiClient.get(grantUrl(connectionId, userId)));
}
export function saveCloudUserGrant(connectionId: string, userId: number, input: {
  allowed: boolean; directoryProfileIds: string[]; expectedGrantRevision: number;
}): Promise<CloudUserGrant> {
  return localRequest(() => apiClient.put(grantUrl(connectionId, userId), {
    allowed: input.allowed, directoryProfileIds: [...input.directoryProfileIds], expectedGrantRevision: input.expectedGrantRevision,
  }), true);
}
export function fetchCloudConnectionChoices(query: CloudListQuery = {}): Promise<CloudPage<CloudConnectionChoice>> {
  return localRequest(() => apiClient.get("/cloud-drive/connection-choices", { params: listParams(query) }));
}
export function fetchCloudDirectoryChoices(connectionId: string, query: CloudListQuery = {}): Promise<CloudPage<CloudDirectoryChoice>> {
  return localRequest(() => apiClient.get("/cloud-drive/connection-choices", { params: { ...listParams(query), connectionId: idPart(connectionId) } }));
}
export function fetchCloudPreference(): Promise<CloudPreference> {
  return localRequest(() => apiClient.get("/cloud-drive/preferences"));
}
export function saveCloudPreference(connectionId: string | null, directoryProfileId: string | null, expectedRevision: number): Promise<CloudPreference> {
  return localRequest(() => apiClient.put("/cloud-drive/preferences", { connectionId, directoryProfileId, expectedRevision }), true);
}
export function fetchCloudGlobalPolicy(): Promise<CloudGlobalPolicy> {
  return localRequest(() => apiClient.get("/settings/cloud-drive-policy"));
}
export function saveCloudGlobalPolicy(minIntervalSeconds: number, failureCooldownSeconds: number, expectedRevision: number): Promise<CloudGlobalPolicy> {
  return localRequest(() => apiClient.put("/settings/cloud-drive-policy", { minIntervalSeconds, failureCooldownSeconds, expectedRevision }), true);
}
