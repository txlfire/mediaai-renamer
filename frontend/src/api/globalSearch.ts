import { apiClient } from "./client";

export const searchKinds = ["source", "scan", "file", "preview", "background"] as const;
export type SearchKind = typeof searchKinds[number];
export type SearchItem = { kind: SearchKind; id: number; title: string; subtitle: string; status: string;
  sourceId: number | null; scanJobId: number | null; requestId: string | null };
export type SearchGroup = { kind: SearchKind; items: SearchItem[]; hasMore: boolean; nextBeforeId: number | null };
const positive = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) > 0;
const nullableId = (v: unknown) => v === null || positive(v);

function item(value: SearchItem): SearchItem {
  if (!value || !searchKinds.includes(value.kind) || !positive(value.id) || typeof value.title !== "string"
    || typeof value.subtitle !== "string" || typeof value.status !== "string" || !nullableId(value.sourceId)
    || !nullableId(value.scanJobId) || !(value.requestId === null || typeof value.requestId === "string" && /^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(value.requestId))) {
    throw new Error("search_invalid_response");
  }
  return { kind: value.kind, id: value.id, title: value.title, subtitle: value.subtitle, status: value.status,
    sourceId: value.sourceId, scanJobId: value.scanJobId, requestId: value.requestId };
}

export async function searchLocal(query: string, kind: SearchKind | "all", beforeId: number | null, signal: AbortSignal): Promise<SearchGroup[]> {
  const { data } = await apiClient.post("/search/query", { query, kind, limit: 10, beforeId }, { signal, timeout: 2500 });
  if (data.networkAccess !== false || !Array.isArray(data.groups) || data.groups.length > 5) throw new Error("search_invalid_response");
  const expected = kind === "all" ? [...searchKinds] : [kind];
  if (data.groups.length !== expected.length) throw new Error("search_invalid_response");
  return data.groups.map((group: SearchGroup, index: number) => {
    if (!group || group.kind !== expected[index] || !Array.isArray(group.items) || group.items.length > 10
      || typeof group.hasMore !== "boolean" || !(group.nextBeforeId === null || positive(group.nextBeforeId))) throw new Error("search_invalid_response");
    const items = group.items.map(item);
    if (items.some((row, i) => row.kind !== group.kind || i > 0 && row.id >= items[i - 1].id)
      || group.hasMore && (!items.length || group.nextBeforeId !== items[items.length - 1]?.id)
      || !group.hasMore && group.nextBeforeId !== null) throw new Error("search_invalid_response");
    return { kind: group.kind, items, hasMore: group.hasMore, nextBeforeId: group.nextBeforeId };
  });
}

export async function readSearchRecord(kind: SearchKind, id: number, signal: AbortSignal): Promise<SearchItem> {
  if (!searchKinds.includes(kind) || !positive(id)) throw new Error("search_invalid_record");
  const { data } = await apiClient.get(`/search/records/${kind}/${id}`, { signal, timeout: 2500 });
  const result = item(data.item);
  if (data.networkAccess !== false || result.kind !== kind || result.id !== id) throw new Error("search_invalid_response");
  return result;
}

export function searchDestination(value: SearchItem) {
  if (value.kind === "background") return null;
  if (value.kind === "source") return { path: "/scan-jobs", query: { media_source_id: String(value.id) } };
  if (!positive(value.sourceId) || !positive(value.scanJobId)) return null;
  return { path: value.kind === "preview" ? "/rename-previews" : "/scan-results",
    query: { media_source_id: String(value.sourceId), scan_job_id: String(value.scanJobId) } };
}
