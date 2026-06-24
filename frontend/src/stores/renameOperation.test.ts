import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRenameOperationStore } from "./renameOperation";

vi.mock("../api/client", () => ({
  createRenameDryRun: vi.fn(async () => ({
    id: 10,
    status: "dry_run",
    total_count: 2,
    ready_count: 1,
    conflict_count: 1,
    renamed_count: 0,
    failed_count: 0,
    items: [
      {
        id: 1,
        status: "ready",
        source_path: "D:/media/A.mkv",
        target_path: "D:/media/A.2024.mkv",
      },
      {
        id: 2,
        status: "conflict",
        source_path: "D:/media/B.mkv",
        target_path: "D:/media/B.2024.mkv",
        message: "目标文件已存在",
      },
    ],
  })),
  executeRenameOperation: vi.fn(async () => ({
    id: 10,
    status: "completed",
    total_count: 2,
    ready_count: 1,
    conflict_count: 1,
    renamed_count: 1,
    failed_count: 0,
    items: [],
  })),
}));

describe("rename operation store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("creates a dry-run operation and exposes executable state", async () => {
    const store = useRenameOperationStore();

    await store.runDryRun([1, 2]);

    expect(store.currentOperation?.id).toBe(10);
    expect(store.canExecute).toBe(true);
    expect(store.currentOperation?.conflict_count).toBe(1);
  });

  it("executes the current operation", async () => {
    const store = useRenameOperationStore();
    await store.runDryRun([1, 2]);

    await store.executeCurrentOperation();

    expect(store.currentOperation?.renamed_count).toBe(1);
    expect(store.currentOperation?.status).toBe("completed");
  });
});
