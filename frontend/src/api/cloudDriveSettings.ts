import { apiClient } from "./client";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { zhCnMessages as messages } from "../locales/zh-CN";

export type CloudDriveSettingsValues = {
  endpoint: string; approvedRoot: string; caCertificatePem: string;
  minIntervalSeconds: number; failureCooldownSeconds: number;
  webExecutionEnabled: boolean; directoryRenameEnabled: boolean;
  directoryMappingRevision: string; quarantineEnabled: boolean; quarantineFolderName: string;
};
export type CloudDriveSettingsSnapshot = {
  values: CloudDriveSettingsValues; revision: number; updatedAt: number; changeBlocked: boolean;
  reason: string | null; caSha256: string | null; source: "database"; networkAccess: false;
};

function failure(error: unknown, saving = false): never {
  if (isSessionChangedError(error)) throw error;
  const code = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  const text = messages.cloudDriveSettings;
  throw new Error(code === "configuration_changed" ? text.conflict : code === "cloud_work_or_unknown_pending" ? text.blocked
    : code === "invalid_public_ca" ? text.invalidCa : code === "invalid_configuration" || code === "invalid_configuration_fields" ? text.invalid
    : saving ? text.saveUnconfirmed : text.failed);
}

export async function fetchCloudDriveSettings(): Promise<CloudDriveSettingsSnapshot> {
  try { return (await apiClient.get<CloudDriveSettingsSnapshot>("/settings/cloud-drive")).data; }
  catch (error) { return failure(error); }
}

export async function saveCloudDriveSettings(values: CloudDriveSettingsValues, expectedRevision: number): Promise<CloudDriveSettingsSnapshot> {
  try { return (await apiClient.put<CloudDriveSettingsSnapshot>("/settings/cloud-drive", { values, expectedRevision })).data; }
  catch (error) { return failure(error, true); }
}
