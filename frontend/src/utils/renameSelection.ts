import type { RenamePreview } from "../api/client";

const READY_RENAME_STATUSES = new Set(["generated", "edited"]);

export function canPrepareRename(preview: Pick<RenamePreview, "status">) {
  return READY_RENAME_STATUSES.has(preview.status);
}

export function hasEmptyTargetName(preview: Pick<RenamePreview, "current_target_name">) {
  return preview.current_target_name.trim().length === 0;
}

export function getRenameablePreviewIds(previews: Array<Pick<RenamePreview, "id" | "status">>) {
  return previews.filter(canPrepareRename).map((preview) => preview.id);
}

export function findEmptyTargetNamePreviews<T extends Pick<RenamePreview, "current_target_name">>(
  previews: T[],
) {
  return previews.filter(hasEmptyTargetName);
}

export function removeEmptyTargetNamePreviews<T extends Pick<RenamePreview, "current_target_name">>(
  previews: T[],
) {
  return previews.filter((preview) => !hasEmptyTargetName(preview));
}
