import type { LocalDirectoryListing } from "../api/client";

export type DirectoryTipKey = "container" | "windows" | "linux" | "macos" | "generic";

export function canGoToParentDirectory(listing: LocalDirectoryListing): boolean {
  if (listing.runtime_environment && listing.runtime_environment !== "windows") {
    return Boolean(listing.parent_path);
  }
  return Boolean(listing.current_path);
}

export function parentDirectoryPath(listing: LocalDirectoryListing): string {
  return listing.parent_path ?? "";
}

export function directoryTipKey(listing: LocalDirectoryListing): DirectoryTipKey {
  const runtimeEnvironment = listing.runtime_environment?.trim().toLowerCase();
  if (runtimeEnvironment === "container") {
    return "container";
  }
  if (runtimeEnvironment === "linux") {
    return "linux";
  }
  if (runtimeEnvironment === "macos") {
    return "macos";
  }
  if (runtimeEnvironment === "windows") {
    return "windows";
  }
  return "generic";
}

export function directoryCurrentPathLabel(
  listing: LocalDirectoryListing,
  fallbackLabel: string,
): string {
  return listing.current_path || listing.root_label || fallbackLabel;
}
