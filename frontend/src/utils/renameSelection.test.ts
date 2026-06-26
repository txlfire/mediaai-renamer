import { describe, expect, it } from "vitest";

import {
  canPrepareRename,
  findEmptyTargetNamePreviews,
  getRenameablePreviewIds,
  removeEmptyTargetNamePreviews,
} from "./renameSelection";

describe("rename selection helpers", () => {
  it("allows only generated and edited previews to prepare rename", () => {
    expect(canPrepareRename({ status: "generated" })).toBe(true);
    expect(canPrepareRename({ status: "edited" })).toBe(true);
    expect(canPrepareRename({ status: "needs_review" })).toBe(false);
    expect(canPrepareRename({ status: "renamed" })).toBe(false);
  });

  it("returns all renameable preview ids", () => {
    expect(
      getRenameablePreviewIds([
        { id: 1, status: "generated" },
        { id: 2, status: "needs_review" },
        { id: 3, status: "edited" },
        { id: 4, status: "renamed" },
      ]),
    ).toEqual([1, 3]);
  });

  it("finds and removes empty target names before rename", () => {
    const previews = [
      { id: 1, current_target_name: "Movie.2024.mkv" },
      { id: 2, current_target_name: "   " },
      { id: 3, current_target_name: "" },
    ];

    expect(findEmptyTargetNamePreviews(previews).map((preview) => preview.id)).toEqual([2, 3]);
    expect(removeEmptyTargetNamePreviews(previews).map((preview) => preview.id)).toEqual([1]);
  });
});
