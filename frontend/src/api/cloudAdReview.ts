import { apiClient } from "./client";
import { inventoryRequestId, InventoryRequestError } from "./cloudInventory";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { zhCnMessages as messages } from "../locales/zh-CN";

export type AdDecision = "keep" | "review" | "suggest_quarantine" | "suggest_delete";
export type AdReviewInput = { requestId: string; jobId: string; expectedRevision: number;
  items: { entryId: number; decision: AdDecision; reason: string }[]; confirmLocalOnly: true };
export type AdReviewItem = AdReviewInput["items"][number] & { name: string; path: string; size: number;
  remoteId: string | null; generation: number; protection: string | null; evidence: string[]; evidenceLevel: "none" | "low" | "medium" };
export type AdReview = { id: string; requestId: string; jobId: string; inventoryRevision: number; pathEpoch: number;
  ruleVersion: string; items: AdReviewItem[]; createdAt: number; snapshotHash: string; sourceCurrent: boolean;
  staleReason: string | null; networkAccess: false; contentReadBytes: 0; writeAvailable: false; executionAuthorized: false };
export type AdReviewHistory = { items: { id: string; requestId: string; jobId: string; createdAt: number;
  itemCount: number; sourceCurrent: boolean }[]; page: number; pageSize: number; total: number; networkAccess: false; writeAvailable: false };

function validRecord(value: AdReview, requestId: string) {
  if (!value || !inventoryRequestId(value.id) || value.requestId !== requestId || !inventoryRequestId(value.jobId)
      || value.networkAccess !== false || value.writeAvailable !== false || value.executionAuthorized !== false || value.contentReadBytes !== 0
      || typeof value.sourceCurrent !== "boolean" || !Number.isFinite(value.createdAt) || value.createdAt < 0 || value.createdAt > 253402300799
      || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > 100
      || new Set(value.items.map(item => item.entryId)).size !== value.items.length
      || !/^[a-f0-9]{64}$/.test(value.snapshotHash)
      || value.items.some(item => !Number.isSafeInteger(item.entryId) || item.entryId <= 0 || typeof item.name !== "string"
        || item.name.length > 4096 || typeof item.path !== "string" || item.path.length > 4096
        || typeof item.reason !== "string" || item.reason.length > 500 || !Array.isArray(item.evidence) || item.evidence.length > 10
        || item.evidence.some(code => typeof code !== "string" || code.length > 80)
        || !["keep", "review", "suggest_quarantine", "suggest_delete"].includes(item.decision))) throw new Error("invalid_review_response");
  return value;
}

async function safeRequest<T>(action: () => Promise<T>): Promise<T> {
  try { return await action(); }
  catch (error) {
    if (isSessionChangedError(error)) throw error;
    const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
    const detail = response?.data?.detail;
    const known = messages.cloudInventory.adReview.errors;
    const code = response?.status === 404 ? "not_found" : typeof detail === "string"
      ? Object.keys(known).find(key => detail.includes(`：${key}；`)) : undefined;
    throw new InventoryRequestError(code ? known[code as keyof typeof known] : messages.cloudInventory.adReview.failed, error);
  }
}

export async function saveAdReview(input: AdReviewInput): Promise<AdReview> {
  return safeRequest(async () => validRecord((await apiClient.post<AdReview>("/cloud-drive/ad-reviews", input)).data, input.requestId));
}
export async function findAdReview(requestId: string): Promise<AdReview> {
  return safeRequest(async () => validRecord((await apiClient.get<AdReview>(`/cloud-drive/ad-review-requests/${encodeURIComponent(requestId)}`)).data, requestId));
}
export async function listAdReviews(page = 1): Promise<AdReviewHistory> {
  return safeRequest(async () => {
    const value = (await apiClient.get<AdReviewHistory>(`/cloud-drive/ad-reviews?page=${page}&pageSize=20`)).data;
    if (!value || value.networkAccess !== false || value.writeAvailable !== false || value.page !== page || value.pageSize !== 20
        || !Number.isSafeInteger(value.total) || value.total < 0 || value.total > 200 || !Array.isArray(value.items) || value.items.length > 20
        || new Set(value.items.map(item => item.id)).size !== value.items.length || value.items.some(item => !inventoryRequestId(item.id)
          || !inventoryRequestId(item.requestId) || !inventoryRequestId(item.jobId) || !Number.isInteger(item.itemCount)
          || typeof item.sourceCurrent !== "boolean" || !Number.isFinite(item.createdAt) || item.createdAt < 0 || item.createdAt > 253402300799
          || item.itemCount < 1 || item.itemCount > 100)) throw new Error("invalid_review_history");
    return value;
  });
}
