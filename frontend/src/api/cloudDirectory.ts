import { apiClient } from "./client";
import { InventoryRequestError } from "./cloudInventory";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { zhCnMessages as messages } from "../locales/zh-CN";

export type DirectoryPlanItem = { entryId: number; generation: number; remoteId: string; sourcePath: string;
  sourceName: string; targetName: string; targetPath: string };
export type DirectoryPlan = { id: string; requestId: string; inventoryJobId: string; inventoryRevision: number;
  kind?: "inventory" | "reverse"; originPlanId?: string | null; originRevision?: number | null; basisEpoch?: number;
  revision: number; status: "prepared" | "cancelled" | "expired"; valid: boolean; invalidReason: string | null;
  policyHash: string; rulesHash: string; ruleVersion: string; parentPath: string; items: DirectoryPlanItem[];
  createdAt: number; expiresAt: number; networkAccess: false; writeAvailable: false };
export type DirectoryPlanHistoryItem = { id: string; requestId: string; kind: "inventory" | "reverse"; parentPath: string;
  status: string; createdAt: number; expiresAt: number; itemCount: number; executionRequestId: string | null;
  executionStatus: string | null; executionReason: string | null; recoveryRequestId: string | null; usedRpc: number | null; maxRpc: number | null };
export type DirectoryPlanInput = { requestId: string; jobId: string; expectedRevision: number; entryIds: number[]; folderTerms: string[] };
export type DirectoryExecutionPolicy = { available: boolean; reason: string | null; policyHash: string;
  limits: { items: number; baseRpc: number; rpcPerItem: number; seconds: number; bytes: number };
  controlledMode: true; atomicIdentityGuaranteed: false; oldPlansGloballyInvalidated: true; automaticRescan: false; networkAccess: false };
export type DirectoryExecution = { planId: string; requestId: string;
  status: "accepted" | "running" | "review" | "cancelled" | "completed"; reason: string | null; revision: number;
  cancelRequested: boolean; workerSlotHeld: boolean; globalSlotHeld: boolean; pollingRecommended: boolean;
  logId: number; logAvailable: boolean;
  latestRecovery?: DirectoryRecovery | null;
  usedRpc: number; maxRpc: number; receivedBytes: number; maxBytes: number; createdAt: number; expiresAt: number;
  items: { ordinal: number; sourceName: string; targetName: string; state: string; reason: string | null; sentAt: number | null; verifiedAt: number | null }[];
  events: { ordinal: number; method: string; itemOrdinal: number | null; phase: string; responseCode: string | null;
    receivedBytes: number; sentAt: number; respondedAt: number | null }[]; networkAccess: false; replayed: false };
export type DirectoryExecutionInput = { requestId: string; expectedRevision: number; policyHash: string;
  confirmMaintenance: boolean; confirmGlobalFence: boolean; apiToken: string };
export type DirectoryRecovery = { id: string; requestId: string; planId: string; attempt: number; revision: number;
  status: "accepted" | "running" | "ready" | "review" | "committed"; reason: string | null;
  workerSlotHeld: boolean; recoverySlotHeld: boolean; cancelRequested: boolean; pollingRecommended: boolean;
  usedRpc: number; maxRpc: number; receivedBytes: number; maxBytes: number; createdAt: number; expiresAt: number; updatedAt: number;
  items: { ordinal: number; sourceName: string; targetName: string; position: "not_observed" | "at_source" | "at_target" | "ambiguous" }[];
  events: { ordinal: number; method: string; itemOrdinal: number | null; phase: string; responseCode: string | null; receivedBytes: number }[];
  originalOutcomeRewritten: false; networkAccess: false; replayed: false };
export type DirectoryRecoveryInput = { requestId: string; expectedRevision: number; confirmMaintenance: boolean; confirmRemoteIdle: boolean; apiToken: string };

function failed(error: unknown): never {
  if (isSessionChangedError(error)) throw error;
  throw new InventoryRequestError(messages.cloudInventory.directoryPlan.requestFailed, error);
}

export async function prepareDirectoryPlan(input: DirectoryPlanInput): Promise<DirectoryPlan> {
  try { return (await apiClient.post<DirectoryPlan>("/cloud-drive/directory-plans", input)).data; }
  catch (error) { return failed(error); }
}

export async function findDirectoryPlan(requestId: string): Promise<DirectoryPlan> {
  try { return (await apiClient.get<DirectoryPlan>(`/cloud-drive/directory-plan-requests/${encodeURIComponent(requestId)}`)).data; }
  catch (error) { return failed(error); }
}

export async function cancelDirectoryPlan(id: string, expectedRevision: number): Promise<DirectoryPlan> {
  try { return (await apiClient.post<DirectoryPlan>(`/cloud-drive/directory-plans/${encodeURIComponent(id)}/cancel`, { expectedRevision })).data; }
  catch (error) { return failed(error); }
}

export async function fetchDirectoryPolicy(): Promise<DirectoryExecutionPolicy> {
  try { return (await apiClient.get<DirectoryExecutionPolicy>("/cloud-drive/directory-policy")).data; }
  catch (error) { return failed(error); }
}

export async function startDirectoryExecution(id: string, input: DirectoryExecutionInput): Promise<DirectoryExecution> {
  try { return (await apiClient.post<DirectoryExecution>(`/cloud-drive/directory-plans/${encodeURIComponent(id)}/execute`, input)).data; }
  catch (error) { return failed(error); }
}

export async function findDirectoryExecution(requestId: string): Promise<DirectoryExecution> {
  try { return (await apiClient.get<DirectoryExecution>(`/cloud-drive/directory-execution-requests/${encodeURIComponent(requestId)}`)).data; }
  catch (error) { return failed(error); }
}

export async function cancelDirectoryExecution(id: string, expectedRevision: number): Promise<DirectoryExecution> {
  try { return (await apiClient.post<DirectoryExecution>(`/cloud-drive/directory-plans/${encodeURIComponent(id)}/execution/cancel`, { expectedRevision })).data; }
  catch (error) { return failed(error); }
}

export async function commitDirectoryLocally(id: string, expectedRevision: number): Promise<DirectoryExecution> {
  try { return (await apiClient.post<DirectoryExecution>(`/cloud-drive/directory-plans/${encodeURIComponent(id)}/commit-local`,
    { expectedRevision, confirmLocalOnly: true })).data; }
  catch (error) { return failed(error); }
}

export async function startDirectoryRecovery(planId: string, input: DirectoryRecoveryInput): Promise<DirectoryRecovery> {
  try { return (await apiClient.post<DirectoryRecovery>(`/cloud-drive/directory-plans/${encodeURIComponent(planId)}/recoveries`, input)).data; }
  catch (error) { return failed(error); }
}

export async function findDirectoryRecovery(requestId: string): Promise<DirectoryRecovery> {
  try { return (await apiClient.get<DirectoryRecovery>(`/cloud-drive/directory-recovery-requests/${encodeURIComponent(requestId)}`)).data; }
  catch (error) { return failed(error); }
}

export async function cancelDirectoryRecovery(id: string, expectedRevision: number): Promise<DirectoryRecovery> {
  try { return (await apiClient.post<DirectoryRecovery>(`/cloud-drive/directory-recoveries/${encodeURIComponent(id)}/cancel`, { expectedRevision })).data; }
  catch (error) { return failed(error); }
}

export async function reviewDirectoryRecoveryWorker(id: string, input: { expectedRevision: number;
  confirmClientsStopped: boolean; confirmRemoteIdle: boolean }): Promise<DirectoryRecovery> {
  try { return (await apiClient.post<DirectoryRecovery>(`/cloud-drive/directory-recoveries/${encodeURIComponent(id)}/review-worker`, input)).data; }
  catch (error) { return failed(error); }
}

export async function commitDirectoryRecovery(id: string, expectedRevision: number): Promise<DirectoryRecovery> {
  try { return (await apiClient.post<DirectoryRecovery>(`/cloud-drive/directory-recoveries/${encodeURIComponent(id)}/commit-local`,
    { expectedRevision, confirmCurrentPositions: true })).data; }
  catch (error) { return failed(error); }
}

export async function prepareReverseDirectoryPlan(id: string, input: { requestId: string; expectedRevision: number }): Promise<DirectoryPlan> {
  try { return (await apiClient.post<DirectoryPlan>(`/cloud-drive/directory-plans/${encodeURIComponent(id)}/reverse`, input)).data; }
  catch (error) { return failed(error); }
}

export async function fetchDirectoryPlanHistory(page = 1, pageSize = 20): Promise<{ items: DirectoryPlanHistoryItem[]; page: number; pageSize: number; total: number; networkAccess: false; replayed: false }> {
  try { return (await apiClient.get("/cloud-drive/directory-plans", { params: { page, pageSize } })).data; }
  catch (error) { return failed(error); }
}
