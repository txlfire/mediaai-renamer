import { CloudConnectionRequestError } from "../api/cloudConnections";
import type { BoundCloudSource, BoundCloudSourceSnapshot } from "../api/cloudBoundInventory";

export type CloudTaskBinding = {
  bindingState?: "bound" | "legacy_unbound";
  contextId?: string | null;
  source?: BoundCloudSourceSnapshot | null;
};
export type ResolvedCloudTaskBinding = { mode: "bound"; contextId: string; source: Readonly<BoundCloudSourceSnapshot> }
  | { mode: "single" | "legacy_unbound"; contextId: null; source: null };
const uuid = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
function invalid(): never { throw new CloudConnectionRequestError("invalid_scope", null, "not_started", false); }

export function resolveCloudTaskBinding(value: CloudTaskBinding): ResolvedCloudTaskBinding {
  if (value.bindingState === undefined && value.contextId === undefined && value.source === undefined) {
    return { mode: "single", contextId: null, source: null };
  }
  if (value.bindingState === "legacy_unbound") {
    if (value.contextId !== null || value.source !== null) invalid();
    return { mode: "legacy_unbound", contextId: null, source: null };
  }
  const source = value.source;
  if ((value.bindingState !== undefined && value.bindingState !== "bound") || !uuid(value.contextId) || !source
    || !uuid(source.connectionId) || !uuid(source.directoryProfileId) || typeof source.selectedPath !== "string"
    || !source.selectedPath.startsWith("/") || source.selectedPath.length > 4096
    || [source.connectionName, source.directoryName, source.currentConnectionName, source.currentDirectoryName]
      .some(name => typeof name !== "string" || name.length > 80)) invalid();
  // 只复制公开来源；派生动作有自己的context，不能要求它与父计划context相同。
  return { mode: "bound", contextId: value.contextId, source: Object.freeze({
    connectionId: source.connectionId, directoryProfileId: source.directoryProfileId, selectedPath: source.selectedPath,
    connectionName: source.connectionName, directoryName: source.directoryName,
    currentConnectionName: source.currentConnectionName, currentDirectoryName: source.currentDirectoryName,
  }) };
}

export function sameCloudTaskSource(value: CloudTaskBinding, expected: BoundCloudSource): boolean {
  try {
    const binding = resolveCloudTaskBinding(value);
    return binding.mode === "bound" && binding.source.connectionId === expected.connectionId
      && binding.source.directoryProfileId === expected.directoryProfileId && binding.source.selectedPath === expected.selectedPath;
  } catch { return false; }
}
