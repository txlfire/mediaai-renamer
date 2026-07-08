export type NamingTemplateStatusText = {
  current: string;
  outdated: string;
  unknown: string;
  currentTooltip: string;
  outdatedTooltip: string;
  unknownTooltip: string;
};

function versionText(value: number | null | undefined) {
  return value === undefined || value === null ? "-" : String(value);
}

function formatTemplateStatusMessage(
  template: string,
  snapshotVersion: number | null | undefined,
  currentVersion: number | null | undefined,
) {
  return template
    .replace("{snapshotVersion}", versionText(snapshotVersion))
    .replace("{currentVersion}", versionText(currentVersion));
}

export function namingTemplateStatusLabel(status: string | null | undefined, text: NamingTemplateStatusText) {
  if (status === "outdated") {
    return text.outdated;
  }
  if (status === "current") {
    return text.current;
  }
  return text.unknown;
}

export function namingTemplateStatusTagType(status: string | null | undefined) {
  if (status === "outdated") {
    return "warning";
  }
  if (status === "current") {
    return "success";
  }
  return "info";
}

export function namingTemplateStatusTooltip(
  status: string | null | undefined,
  snapshotVersion: number | null | undefined,
  currentVersion: number | null | undefined,
  text: NamingTemplateStatusText,
) {
  if (status === "outdated") {
    return formatTemplateStatusMessage(text.outdatedTooltip, snapshotVersion, currentVersion);
  }
  if (status === "current") {
    return formatTemplateStatusMessage(text.currentTooltip, snapshotVersion, currentVersion);
  }
  return formatTemplateStatusMessage(text.unknownTooltip, snapshotVersion, currentVersion);
}
