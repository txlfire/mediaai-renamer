import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRenameOperationStore } from "./renameOperation";

vi.mock("../api/client", () => ({
  createRenameRollbackPlan: vi.fn(async () => ({
    id: 20,
    operation_id: 10,
    status: "draft",
    item_count: 1,
    executable_count: 0,
    conflict_count: 0,
    created_by: "admin",
    items: [],
  })),
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
    ready_count: 0,
    conflict_count: 1,
    renamed_count: 1,
    failed_count: 0,
    items: [],
  })),
  dryRunRenameRollbackPlan: vi.fn(async () => ({
    id: 20,
    operation_id: 10,
    status: "checked",
    item_count: 1,
    executable_count: 1,
    conflict_count: 0,
    created_by: "admin",
    items: [],
  })),
  executeRenameRollbackPlan: vi.fn(async () => ({
    id: 20,
    operation_id: 10,
    status: "executed",
    item_count: 1,
    executable_count: 1,
    conflict_count: 0,
    created_by: "admin",
    items: [],
  })),
  fetchRenameRollbackPlans: vi.fn(async () => []),
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

  it("does not expose executable state after operation completes", async () => {
    const store = useRenameOperationStore();
    store.currentOperation = {
      id: 10,
      status: "completed",
      mode: "safe_rename",
      total_count: 2,
      ready_count: 1,
      conflict_count: 1,
      renamed_count: 1,
      failed_count: 0,
      items: [],
    };

    expect(store.canExecute).toBe(false);
  });

  it("executes the current operation", async () => {
    const store = useRenameOperationStore();
    await store.runDryRun([1, 2]);

    await store.executeCurrentOperation();

    expect(store.currentOperation?.renamed_count).toBe(1);
    expect(store.canExecute).toBe(false);
    expect(store.currentOperation?.status).toBe("completed");
  });

  it("creates, dry-runs and executes rollback plan", async () => {
    const store = useRenameOperationStore();
    store.currentOperation = {
      id: 10,
      status: "completed",
      mode: "safe_rename",
      total_count: 1,
      ready_count: 0,
      conflict_count: 0,
      renamed_count: 1,
      failed_count: 0,
      items: [],
    };

    await store.createRollbackPlan();
    await store.dryRunRollbackPlan();
    await store.executeRollbackPlan();

    expect(store.currentRollbackPlan?.id).toBe(20);
    expect(store.currentRollbackPlan?.status).toBe("executed");
    expect(store.rollbackErrorMessage).toBe("");
  });
});
