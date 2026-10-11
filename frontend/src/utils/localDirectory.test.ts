import { describe, expect, it } from "vitest";

import {
  canGoToParentDirectory,
  directoryCurrentPathLabel,
  directoryTipKey,
  parentDirectoryPath,
} from "./localDirectory";

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

  it("uses container-specific directory labels", () => {
    const listing = {
      current_path: "/app/media",
      parent_path: null,
      entries: [],
      runtime_environment: "container",
      root_label: "容器媒体目录",
    };

    expect(directoryTipKey(listing)).toBe("container");
    expect(directoryCurrentPathLabel(listing, "此电脑")).toBe("/app/media");
    expect(canGoToParentDirectory(listing)).toBe(false);
  });

  it("uses the backend root label when the current path is empty", () => {
    const listing = {
      current_path: null,
      parent_path: null,
      entries: [],
      runtime_environment: "windows",
      root_label: "此电脑",
    };

    expect(directoryCurrentPathLabel(listing, "文件系统")).toBe("此电脑");
  });

  it("uses a generic tip before runtime metadata is loaded", () => {
    const listing = {
      current_path: null,
      parent_path: null,
      entries: [],
    };

    expect(directoryTipKey(listing)).toBe("generic");
  });
});
