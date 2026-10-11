import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRenameOperationStore } from "./renameOperation";
import { acceptRenameOperation, fetchRenameOperation, acceptRenameRollbackPlan, fetchRenameRollbackPlan, type RenameOperation, type RenameRollbackPlan } from "../api/client";

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
  acceptRenameOperation: vi.fn(async () => ({
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
  acceptRenameRollbackPlan: vi.fn(async () => ({
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
  fetchRenameRollbackPlan: vi.fn(),
  fetchRenameOperation: vi.fn(),
}));

describe("rename operation store", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchRenameOperation).mockReset();
    vi.mocked(fetchRenameRollbackPlan).mockReset();
    setActivePinia(createPinia());
  });
  afterEach(() => { vi.useRealTimers(); });

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

  it("accepts once and polls serially without overlapping slow requests", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore(); await store.runDryRun([1]);
    const queued = { ...store.currentOperation!, status: "queued" };
    vi.mocked(acceptRenameOperation).mockResolvedValueOnce(queued);
    let resolve!: (value: RenameOperation) => void;
    vi.mocked(fetchRenameOperation).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const progress = vi.fn(); const execution = store.executeCurrentOperation(progress);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchRenameOperation).toHaveBeenCalledOnce();
    await store.executeCurrentOperation();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchRenameOperation).toHaveBeenCalledOnce();
    expect(acceptRenameOperation).toHaveBeenCalledOnce();
    resolve({ ...queued, status: "completed", renamed_count: 1, ready_count: 0 });
    await execution;
    expect(progress).toHaveBeenCalledTimes(2); expect(store.loading).toBe(false);
  });

  it("unknown acceptance prevents replay and a read-only refresh restores state", async () => {
    const store = useRenameOperationStore(); await store.runDryRun([1]);
    const operation = store.currentOperation!;
    vi.mocked(acceptRenameOperation).mockRejectedValueOnce(new Error("timeout"));
    await store.executeCurrentOperation();
    expect(store.executionUncertain).toBe(true); expect(store.canExecute).toBe(false);
    expect(store.currentOperation?.id).toBe(10);
    await store.executeCurrentOperation();
    expect(acceptRenameOperation).toHaveBeenCalledOnce();
    vi.mocked(fetchRenameOperation).mockResolvedValueOnce({ ...operation, status: "completed", renamed_count: 1 });
    await store.refreshCurrentOperation();
    expect(store.executionUncertain).toBe(false); expect(store.currentOperation?.status).toBe("completed");
    expect(acceptRenameOperation).toHaveBeenCalledOnce();
  });

  it("leaving stops polling and ignores the old response", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore(); await store.runDryRun([1]);
    const queued = { ...store.currentOperation!, status: "queued" };
    vi.mocked(acceptRenameOperation).mockResolvedValueOnce(queued);
    let resolve!: (value: RenameOperation) => void;
    vi.mocked(fetchRenameOperation).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const execution = store.executeCurrentOperation(); await vi.advanceTimersByTimeAsync(1000);
    store.stopExecutionPolling();
    resolve({ ...queued, status: "completed", renamed_count: 1 }); await execution;
    await vi.advanceTimersByTimeAsync(5000);
    expect(store.currentOperation?.status).toBe("queued");
    expect(fetchRenameOperation).toHaveBeenCalledOnce(); expect(store.loading).toBe(false);
  });

  it("query failure stops polling without replaying the accepted operation", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore(); await store.runDryRun([1]);
    vi.mocked(acceptRenameOperation).mockResolvedValueOnce({ ...store.currentOperation!, status: "queued" });
    vi.mocked(fetchRenameOperation).mockRejectedValueOnce(new Error("offline"));
    const execution = store.executeCurrentOperation(); await vi.advanceTimersByTimeAsync(1000); await execution;
    expect(store.currentOperation?.status).toBe("queued"); expect(store.errorMessage).not.toBe("");
    expect(store.canExecute).toBe(false); expect(acceptRenameOperation).toHaveBeenCalledOnce();
  });

  it("task navigation loads the exact operation without posting an execution", async () => {
    const store = useRenameOperationStore();
    vi.mocked(fetchRenameOperation).mockResolvedValueOnce({ id: 90, status: "interrupted", items: [] } as unknown as RenameOperation);
    await store.refreshCurrentOperation(undefined, 90);
    expect(fetchRenameOperation).toHaveBeenCalledWith(90); expect(acceptRenameOperation).not.toHaveBeenCalled();
    expect(store.currentOperation?.status).toBe("interrupted");
  });

  it("rollback submits once and never overlaps slow polling", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore();
    store.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    await store.createRollbackPlan(); await store.dryRunRollbackPlan();
    const queued = { ...store.currentRollbackPlan!, status: "queued" };
    vi.mocked(acceptRenameRollbackPlan).mockResolvedValueOnce(queued);
    let resolve!: (plan: RenameRollbackPlan) => void;
    vi.mocked(fetchRenameRollbackPlan).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const progress = vi.fn(); const running = store.executeRollbackPlan(progress);
    await vi.advanceTimersByTimeAsync(1000);
    await store.executeRollbackPlan();
    await vi.advanceTimersByTimeAsync(5000);
    expect(acceptRenameRollbackPlan).toHaveBeenCalledOnce();
    expect(fetchRenameRollbackPlan).toHaveBeenCalledOnce();
    resolve({ ...queued, status: "partial_failed", rolled_back_count: 1, failed_count: 1 });
    await running;
    expect(store.currentRollbackPlan?.status).toBe("partial_failed");
    expect(progress).toHaveBeenCalledTimes(2); expect(store.rollbackLoading).toBe(false);
  });

  it("rollback query failure stops without reissuing the accepted action", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore();
    store.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    await store.createRollbackPlan(); await store.dryRunRollbackPlan();
    vi.mocked(acceptRenameRollbackPlan).mockResolvedValueOnce({ ...store.currentRollbackPlan!, status: "queued" });
    vi.mocked(fetchRenameRollbackPlan).mockRejectedValueOnce(new Error("offline"));
    const running = store.executeRollbackPlan();
    await vi.advanceTimersByTimeAsync(1000); await running;
    await store.executeRollbackPlan(); await vi.advanceTimersByTimeAsync(5000);
    expect(store.currentRollbackPlan?.status).toBe("queued");
    expect(store.rollbackErrorMessage).not.toBe("");
    expect(acceptRenameRollbackPlan).toHaveBeenCalledOnce();
    expect(fetchRenameRollbackPlan).toHaveBeenCalledOnce();
  });

  it("unknown rollback acceptance prevents replay until a read-only refresh", async () => {
    const store = useRenameOperationStore();
    store.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    await store.createRollbackPlan(); await store.dryRunRollbackPlan();
    const plan = store.currentRollbackPlan!;
    vi.mocked(acceptRenameRollbackPlan).mockRejectedValueOnce(new Error("timeout"));
    await store.executeRollbackPlan(); await store.executeRollbackPlan(); await store.dryRunRollbackPlan();
    expect(store.rollbackUncertain).toBe(true);
    expect(acceptRenameRollbackPlan).toHaveBeenCalledOnce();
    vi.mocked(fetchRenameRollbackPlan).mockResolvedValueOnce({ ...plan, status: "interrupted" });
    await store.refreshRollbackPlan();
    expect(store.rollbackUncertain).toBe(false);
    expect(store.currentRollbackPlan?.status).toBe("interrupted");
    expect(acceptRenameRollbackPlan).toHaveBeenCalledOnce();
  });

  it("leaving ignores late rollback response and stops further requests", async () => {
    vi.useFakeTimers();
    const store = useRenameOperationStore();
    store.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    await store.createRollbackPlan(); await store.dryRunRollbackPlan();
    const queued = { ...store.currentRollbackPlan!, status: "queued" };
    vi.mocked(acceptRenameRollbackPlan).mockResolvedValueOnce(queued);
    let resolve!: (plan: RenameRollbackPlan) => void;
    vi.mocked(fetchRenameRollbackPlan).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const running = store.executeRollbackPlan();
    await vi.advanceTimersByTimeAsync(1000); store.stopExecutionPolling();
    resolve({ ...queued, status: "executed" }); await running;
    await vi.advanceTimersByTimeAsync(5000);
    expect(store.currentRollbackPlan?.status).toBe("queued");
    expect(fetchRenameRollbackPlan).toHaveBeenCalledOnce(); expect(store.rollbackLoading).toBe(false);
  });

  it("refreshing original operation preserves its rollback and navigation only reads the exact plan", async () => {
    const store = useRenameOperationStore();
    store.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    await store.createRollbackPlan();
    const plan = store.currentRollbackPlan!;
    vi.mocked(fetchRenameOperation).mockResolvedValueOnce(store.currentOperation);
    await store.refreshCurrentOperation();
    expect(store.currentRollbackPlan?.id).toBe(20);
    vi.mocked(fetchRenameRollbackPlan).mockResolvedValueOnce({ ...plan, id: 90, status: "interrupted" });
    await store.refreshRollbackPlan(90);
    expect(fetchRenameRollbackPlan).toHaveBeenCalledWith(90);
    expect(store.currentRollbackPlan?.id).toBe(90);
    expect(acceptRenameRollbackPlan).not.toHaveBeenCalled();
  });
});
