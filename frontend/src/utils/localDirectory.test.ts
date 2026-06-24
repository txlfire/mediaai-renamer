import { describe, expect, it } from "vitest";

import { canGoToParentDirectory, parentDirectoryPath } from "./localDirectory";

describe("local directory navigation", () => {
  it("allows returning from a drive root to the computer root", () => {
    const listing = {
      current_path: "D:\\",
      parent_path: null,
      entries: [],
    };

    expect(canGoToParentDirectory(listing)).toBe(true);
    expect(parentDirectoryPath(listing)).toBe("");
  });

  it("disables parent navigation at the computer root", () => {
    const listing = {
      current_path: null,
      parent_path: null,
      entries: [],
    };

    expect(canGoToParentDirectory(listing)).toBe(false);
    expect(parentDirectoryPath(listing)).toBe("");
  });
});
