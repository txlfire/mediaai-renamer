import type { LocalDirectoryListing } from "../api/client";

export function canGoToParentDirectory(listing: LocalDirectoryListing): boolean {
  return Boolean(listing.current_path);
}

export function parentDirectoryPath(listing: LocalDirectoryListing): string {
  return listing.parent_path ?? "";
}
