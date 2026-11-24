import { apiClient } from "./client";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { getInterfaceMessages } from "../locales/interfaceLanguage";
import type { CloudTaskBinding } from "../utils/cloudTaskBinding";
import { cloudHistoryQuery, validateCloudHistorySources, type CloudHistoryFilter } from "../utils/cloudHistorySource";

export type InventoryStatus = "queued" | "running" | "paused" | "interrupted" | "completed" | "cancelled";
export type InventoryJobOption = CloudTaskBinding & { id: string; approvedRoot: string; mode: "overview" | "subtrees";
  status: InventoryStatus; reason: string | null; storedEntries: number; createdAt: number };
export type InventoryJobPage = { items: InventoryJobOption[]; total: number; page: number; pageSize: number };
export type InventorySummary = Omit<InventoryJobOption, "createdAt"> & { revision: number; cancelRequested: boolean;
  pathCurrent?: boolean;
  receivedEntries: number; receivedBytes: number; completedDirectories: number; directoryStates: Record<string, number>;
  estimatedTotalEntries: null; limits: { entries: number; bytes: number; directories: number; depth: number };
  scopeComplete: boolean; workerSlotHeld: boolean; writeAvailable: false; executionAvailable: false;
  cacheState: "available" | "clearing" | "cleared"; clearedAt: number | null; cacheClearAvailable: boolean; numberedPagination?: boolean;
  createdAt: number; updatedAt: number; storedBytes: number; remainingEntries: number; remainingReceiveBytes: number;
  budget: { maxRpc: number; usedRpc: number; remainingRpc: number; status: "active" | "paused" | "exhausted" | "expired" | "closed"; expiresAt: number; expired: boolean };
  selectedPaths: string[]; excludedPaths: string[]; freshness: "unknown" };
export type FolderSuggestion = { status: "protected" | "unselected" | "blocked" | "unchanged" | "suggested";
  proposedName: string | null; reason: string; conflict?: string };
export type InventoryAdviceItem = { id: number; name: string; relativePath: string; isDirectory: boolean; size: number;
  directoryComplete: boolean; decision: "keep" | "review"; protection: string | null; evidenceLevel: "none" | "low" | "medium";
  reasons: string[]; partialObservation: boolean; folderSuggestion: FolderSuggestion | null; selected: false; actionAvailable: false;
  userKept?: boolean };
type InventoryNumberedPage = { page: number; pageSize: number; total: number };
export type InventoryAdvicePage = { items: InventoryAdviceItem[]; hasMore: boolean; nextId: number; networkAccess: false; pagination?: InventoryNumberedPage;
  jobRevision: number; ruleVersion: string; rulesHash: string; freshness: "unknown"; contentReadBytes: 0;
  externalSubmission: false; writeAvailable: false; keepExceptionsPersisted: false };
export type InventoryAdviceInput = { limit: number; folderEntryIds?: number[]; folderTerms?: string[]; keepEntryIds?: number[] }
  & ({ afterId: number; page?: never } | { page: number; afterId?: never });
export type InventoryDirectoryPage = { items: { id: number; relativePath: string; depth: number;
  status: "pending" | "reading" | "partial" | "complete" }[]; hasMore: boolean; nextId: number; jobRevision: number;
  networkAccess: false; pagination?: InventoryNumberedPage };

export type InventoryPolicy = { available: boolean; reason: string | null; approvedRoot: string; policyHash: string;
  limits: { rpc: number; seconds: number; entries: number; bytes: number; directories: number; depth: number };
  networkAccess: false; writeAvailable: false };
export type InventoryPrepareInput = { requestId: string; selectedPath: string; policyHash: string; confirmRead: true };

export function inventoryRequestId(value: unknown): string {
  return typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value) ? value : "";
}

export class InventoryRequestError extends Error {
  readonly status: number | null;
  readonly reasonCode: "multi_connection_required" | null;
  constructor(message: string, error: unknown) {
    super(message);
    this.name = "InventoryRequestError";
    const status = (error as { response?: { status?: unknown } } | null)?.response?.status;
    this.status = typeof status === "number" && Number.isInteger(status) ? status : null;
    const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
    this.reasonCode = typeof detail === "object" && detail !== null
      && (detail as Record<string, unknown>).reasonCode === "multi_connection_required" ? "multi_connection_required" : null;
  }
}

function requestFailure(message: string, error: unknown): never {
  if (isSessionChangedError(error)) throw error;
  // 不把含请求体、Token或远端原文的Axios对象传到页面或错误提示。
  throw new InventoryRequestError(message, error);
}

export async function fetchInventoryPolicy(): Promise<InventoryPolicy> {
  try { return (await apiClient.get<InventoryPolicy>("/cloud-drive/inventory-policy")).data; }
  catch (error) { return requestFailure(getInterfaceMessages().cloudInventory.startFlow.policyFailed, error); }
}

export async function prepareInventoryJob(input: InventoryPrepareInput): Promise<InventorySummary> {
  try { return (await apiClient.post<InventorySummary>("/cloud-drive/inventory-jobs", input)).data; }
  catch (error) { return requestFailure(getInterfaceMessages().cloudInventory.startFlow.unknown, error); }
}

export async function findInventoryRequest(requestId: string): Promise<InventorySummary> {
  try { return (await apiClient.get<InventorySummary>(`/cloud-drive/inventory-requests/${encodeURIComponent(requestId)}`)).data; }
  catch (error) { return requestFailure(getInterfaceMessages().cloudInventory.startFlow.lookupFailed, error); }
}

export async function startInventoryJob(id: string, expectedRevision: number, policyHash: string, apiToken: string): Promise<InventorySummary> {
  if (typeof location === "undefined" || location.protocol !== "https:") throw new Error(getInterfaceMessages().cloudInventory.startFlow.httpsRequired);
  const payload = { expectedRevision, policyHash, confirmRead: true, apiToken };
  try { return (await apiClient.post<InventorySummary>(`${jobUrl(id)}/start`, payload, { timeout: 15000 })).data; }
  catch (error) { return requestFailure(getInterfaceMessages().cloudInventory.startFlow.unknown, error); }
  finally { payload.apiToken = ""; }
}

const jobUrl = (id: string) => `/cloud-drive/inventory-jobs/${encodeURIComponent(id)}`;
export async function fetchInventoryJobs(page = 1, filter: CloudHistoryFilter = {}): Promise<InventoryJobPage> {
  const value = (await apiClient.get<InventoryJobPage>(`/cloud-drive/inventory-jobs?page=${page}&pageSize=20${cloudHistoryQuery(filter)}`)).data;
  if (!value || !Array.isArray(value.items) || value.items.length > 20 || value.page !== page) throw new Error("invalid_inventory_history");
  validateCloudHistorySources(value.items, filter);
  return value;
}
export async function fetchInventorySummary(id: string): Promise<InventorySummary> {
  return (await apiClient.get<InventorySummary>(jobUrl(id))).data;
}
export async function fetchInventoryAdvice(id: string, input: InventoryAdviceInput): Promise<InventoryAdvicePage> {
  return (await apiClient.post<InventoryAdvicePage>(`${jobUrl(id)}/advice`, input)).data;
}
export async function cancelInventoryJob(id: string): Promise<InventorySummary> {
  return (await apiClient.post<InventorySummary>(`${jobUrl(id)}/cancel`, {})).data;
}
export async function setInventoryKeep(id: string, entryId: number, kept: boolean, expectedRevision: number): Promise<InventorySummary> {
  return (await apiClient.put<InventorySummary>(`${jobUrl(id)}/entries/${entryId}/keep`, { kept, expectedRevision })).data;
}
export async function clearInventoryCache(id: string, expectedRevision: number): Promise<InventorySummary> {
  return (await apiClient.post<InventorySummary>(`${jobUrl(id)}/clear-cache`, { confirmClear: true, expectedRevision })).data;
}
export const inventoryDirectoryPageSize = 50;

export async function fetchInventoryDirectories(id: string, afterId = 0, page?: number, limit = inventoryDirectoryPageSize): Promise<InventoryDirectoryPage> {
  const position = page === undefined ? `afterId=${afterId}` : `page=${page}`;
  return (await apiClient.get<InventoryDirectoryPage>(`${jobUrl(id)}/directories?${position}&limit=${limit}`)).data;
}
