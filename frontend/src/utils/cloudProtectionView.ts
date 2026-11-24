import type { CloudPage } from "../api/cloudConnections";
import type { CloudProtectedKind, CloudProtectionItem, CloudReadProtection } from "../api/cloudDiagnostics";
import { inventoryRequestId } from "../api/cloudInventory";

const kinds: CloudProtectedKind[] = ["inventory", "diagnostic_tls", "diagnostic_token_runtime", "directory_execute", "directory_observe", "quarantine_move", "quarantine_observe"];
const states = ["queued", "accepted", "running", "paused", "completed", "cancelled", "interrupted", "review", "committed", "failed"];
function invalid(): never { throw new Error("invalid_cloud_protection_response"); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) invalid(); return value as Record<string, unknown>; }
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function pid(value: unknown): value is number | null { return value === null || integer(value) && value > 0 && value <= 2147483647; }
function kind(value: unknown): CloudProtectedKind { if (typeof value !== "string" || !kinds.includes(value as CloudProtectedKind)) invalid(); return value as CloudProtectedKind; }
function status(value: unknown): string { return typeof value === "string" && states.includes(value) ? value : "unknown"; }

export function normalizeCloudProtection(value: unknown, requestId: string, resourceKind?: CloudProtectedKind): CloudReadProtection {
  const row = record(value), parsedKind = kind(row.resourceKind);
  if (!inventoryRequestId(requestId) || row.requestId !== requestId || resourceKind && resourceKind !== parsedKind
      || row.networkAccess !== false || row.retryAutomatically !== false || !integer(row.revision)
      || [row.workerSlotHeld, row.requestSlotHeld, row.unresolved, row.stopRequested, row.continuationBlocked].some(v => typeof v !== "boolean")) invalid();
  // 只保留最小摘要白名单，不缓存服务端附带的目录、端点或任意错误原文。
  return { requestId, resourceKind: parsedKind, status: status(row.status), revision: row.revision,
    workerSlotHeld: row.workerSlotHeld as boolean, requestSlotHeld: row.requestSlotHeld as boolean,
    unresolved: row.unresolved as boolean, stopRequested: row.stopRequested as boolean,
    continuationBlocked: row.continuationBlocked as boolean, networkAccess: false, retryAutomatically: false };
}
export function normalizeCloudProtectionPage(value: unknown, page: number): CloudPage<CloudProtectionItem> {
  const result = record(value);
  if (result.networkAccess !== false || result.page !== page || result.pageSize !== 20 || !integer(result.total)
      || !Array.isArray(result.items) || result.items.length > 20) invalid();
  const items = result.items.map(item => {
    const row = record(item), resourceKind = kind(row.resourceKind);
    if (typeof row.requestId !== "string" || !inventoryRequestId(row.requestId) || !integer(row.revision)
        || !integer(row.actorId) || row.actorId < 1 || !pid(row.workerPid) || !pid(row.ownerPid)
        || row.ownerHost !== null && (typeof row.ownerHost !== "string" || row.ownerHost.length > 256 || /[\x00-\x1f\x7f]/.test(row.ownerHost))
        || typeof row.workerSlotHeld !== "boolean" || typeof row.requestSlotHeld !== "boolean") invalid();
    return { requestId: row.requestId, resourceKind, actorId: row.actorId, status: status(row.status), revision: row.revision,
      workerPid: row.workerPid, ownerPid: row.ownerPid, ownerHost: row.ownerHost as string | null,
      workerSlotHeld: row.workerSlotHeld, requestSlotHeld: row.requestSlotHeld };
  });
  if (new Set(items.map(row => row.requestId)).size !== items.length) invalid();
  return { items, total: result.total, page, pageSize: 20, networkAccess: false };
}
export function canReviewCloudReadProcess(row: Pick<CloudProtectionItem, "resourceKind" | "workerSlotHeld">): boolean {
  return row.workerSlotHeld && ["inventory", "diagnostic_tls", "diagnostic_token_runtime"].includes(row.resourceKind);
}
