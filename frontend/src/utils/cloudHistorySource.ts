import { CloudConnectionRequestError } from "../api/cloudConnections";
import { resolveCloudTaskBinding, type CloudTaskBinding } from "./cloudTaskBinding";

export type CloudHistoryFilter = { connectionId?: string; directoryProfileId?: string };
const uuid = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
export function cloudHistoryParams(filter: CloudHistoryFilter = {}): CloudHistoryFilter {
  if (filter.connectionId === undefined && filter.directoryProfileId === undefined) return {};
  if (!uuid(filter.connectionId) || filter.directoryProfileId !== undefined && !uuid(filter.directoryProfileId)) {
    throw new CloudConnectionRequestError("invalid_fields", null, "not_started", false);
  }
  return filter.directoryProfileId ? { connectionId: filter.connectionId, directoryProfileId: filter.directoryProfileId } : { connectionId: filter.connectionId };
}
export function cloudHistoryQuery(filter: CloudHistoryFilter = {}): string {
  const params = new URLSearchParams(cloudHistoryParams(filter));
  return params.size ? "&" + params.toString() : "";
}
export function validateCloudHistorySources<T extends CloudTaskBinding>(items: T[], filter: CloudHistoryFilter = {}): T[] {
  const expected = cloudHistoryParams(filter);
  for (const item of items) {
    const binding = resolveCloudTaskBinding(item);
    if (expected.connectionId && (binding.mode !== "bound" || binding.source.connectionId !== expected.connectionId
        || expected.directoryProfileId && binding.source.directoryProfileId !== expected.directoryProfileId)) {
      throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false);
    }
  }
  return items;
}
