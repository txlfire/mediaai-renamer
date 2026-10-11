import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePendingFileStore } from "./pendingFiles";
import type { PendingFile, PendingMoveTask } from "../api/client";

const api = vi.hoisted(() => ({
  clearPendingFiles: vi.fn(), fetchPendingFiles: vi.fn(), movePendingFiles: vi.fn(),
  parsePendingFileWithAi: vi.fn(), removePendingFile: vi.fn(),
  acceptPendingMove: vi.fn(), fetchPendingMove: vi.fn(), findPendingMove: vi.fn(),
}));
vi.mock("../api/client", () => api);

function row(id: number, status: string): PendingFile {
  return { id, status, media_source_id: 1, scan_job_id: 1, file_path: `synthetic/${id}.mkv`,
    file_name: `${id}.mkv`, extension: ".mkv", file_size: 5, reason: "size_filtered", created_at: "now" };
}

describe("pending file recovery state", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
  });

  it("keeps unresolved rows when clearing editable tasks", async () => {
    const store = usePendingFileStore();
    store.pendingFiles = [row(1, "pending"), row(2, "recovery_required")];
    api.clearPendingFiles.mockResolvedValue({ removed_count: 1 });
    expect(await store.clearPendingFiles()).toBe(1);
    expect(store.pendingFiles.map((item) => item.id)).toEqual([2]);
  });

  it("refreshes actual rows after partial failure and preserves the primary error", async () => {
    const store = usePendingFileStore();
    const error = new Error("partial move");
    store.pendingFiles = [row(1, "pending"), row(2, "pending")];
    api.movePendingFiles.mockRejectedValue(error);
    api.fetchPendingFiles.mockResolvedValue([row(2, "recovery_required")]);
    await expect(store.movePendingFiles([1, 2], "synthetic-target")).rejects.toBe(error);
    expect(store.pendingFiles).toEqual([row(2, "recovery_required")]);
  });

  it("does not replace the primary failure when refresh also fails", async () => {
    const store = usePendingFileStore();
    const error = new Error("move failed");
    api.movePendingFiles.mockRejectedValue(error);
    api.fetchPendingFiles.mockRejectedValue(new Error("refresh failed"));
    await expect(store.movePendingFiles([1], "synthetic-target")).rejects.toBe(error);
    expect(store.refreshFailed).toBe(true);
  });
  it("lets paged callers refresh bounded rows without fetching the legacy array", async () => {
    const store = usePendingFileStore();
    const error = new Error("partial move");
    api.movePendingFiles.mockRejectedValue(error);
    await expect(store.movePendingFiles([1], "synthetic-target", false)).rejects.toBe(error);
    expect(api.fetchPendingFiles).not.toHaveBeenCalled();
    expect(store.refreshFailed).toBe(true);
  });
});

function task(status = "completed"): PendingMoveTask {
  return { id: 1, status, target_directory: "synthetic-target", total_count: 2, moved_count: status === "completed" ? 2 : 0,
    failed_count: 0, error_message: null, created_at: "now", updated_at: "now", items: [] };
}

describe("persistent pending move observation", () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.resetAllMocks(); vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  it("submits once and observes serially until the real terminal result", async () => {
    api.acceptPendingMove.mockResolvedValue(task("queued"));
    api.fetchPendingMove.mockResolvedValue(task());
    const store = usePendingFileStore();
    const progress = vi.fn();
    const execution = store.executePendingMove([1, 2], "target", progress);
    await vi.advanceTimersByTimeAsync(0);
    await store.executePendingMove([1, 2], "target", progress);
    expect(api.acceptPendingMove).toHaveBeenCalledTimes(1);
    expect(api.fetchPendingMove).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    await execution;
    expect(api.fetchPendingMove).toHaveBeenCalledTimes(1);
    expect(store.currentMoveTask?.moved_count).toBe(2);
    expect(progress).toHaveBeenCalledTimes(2);
  });

  it("lost acceptance response is recovered by request key without another POST", async () => {
    api.acceptPendingMove.mockRejectedValue(new Error("timeout"));
    const store = usePendingFileStore();
    await store.executePendingMove([1], "target");
    expect(store.moveUncertain).toBe(true);
    const key = store.moveRequestId;
    await store.executePendingMove([1], "target");
    api.findPendingMove.mockResolvedValue(task());
    await store.refreshPendingMove();
    expect(api.acceptPendingMove).toHaveBeenCalledTimes(1);
    expect(api.findPendingMove).toHaveBeenCalledWith(key);
    expect(store.moveUncertain).toBe(false);
  });

  it("missing read-only lookup remains uncertain and does not authorize a retry", async () => {
    const store = usePendingFileStore();
    store.moveRequestId = "synthetic-request";
    api.findPendingMove.mockResolvedValue(null);
    await store.refreshPendingMove();
    expect(store.moveUncertain).toBe(true);
    expect(store.moveError).not.toBe("");
    api.acceptPendingMove.mockResolvedValue(task());
    await store.executePendingMove([1], "target");
    expect(api.acceptPendingMove).not.toHaveBeenCalled();
  });

  it("an explicit request link never queries a previous in-memory task", async () => {
    const store = usePendingFileStore();
    store.currentMoveTask = task();
    store.moveRequestId = "old-request";
    const newer = { ...task(), id: 2 };
    api.findPendingMove.mockResolvedValue(newer);
    await store.refreshPendingMove(undefined, "new-request");
    expect(api.findPendingMove).toHaveBeenCalledWith("new-request");
    expect(api.fetchPendingMove).not.toHaveBeenCalled();
    expect(store.currentMoveTask?.id).toBe(2);
    expect(store.moveRequestId).toBe("new-request");
  });

  it("a failed explicit task lookup clears stale results and keeps only its read-only target", async () => {
    const store = usePendingFileStore();
    store.currentMoveTask = task();
    store.moveRequestId = "old-request";
    api.fetchPendingMove.mockRejectedValue(new Error("unavailable"));
    await store.refreshPendingMove(2);
    expect(store.currentMoveTask).toBeNull();
    expect(store.moveRequestId).toBeNull();
    expect(store.moveUncertain).toBe(true);
    expect(store.moveTaskId).toBe(2);
    expect(api.fetchPendingMove).toHaveBeenCalledWith(2);
    expect(api.acceptPendingMove).not.toHaveBeenCalled();
    api.fetchPendingMove.mockResolvedValue({ ...task(), id: 2 });
    await store.refreshPendingMove();
    expect(api.fetchPendingMove).toHaveBeenLastCalledWith(2);
    expect(store.currentMoveTask?.id).toBe(2);
  });

  it("leaving during acceptance ignores late response and retains request lookup", async () => {
    let resolve!: (value: PendingMoveTask) => void;
    api.acceptPendingMove.mockReturnValue(new Promise(done => { resolve = done; }));
    const store = usePendingFileStore();
    const progress = vi.fn();
    const execution = store.executePendingMove([1], "target", progress);
    await vi.advanceTimersByTimeAsync(0);
    store.stopMovePolling();
    resolve(task());
    await execution;
    expect(store.currentMoveTask).toBeNull();
    expect(store.moveRequestId).not.toBeNull();
    expect(progress).not.toHaveBeenCalled();
    expect(api.fetchPendingMove).not.toHaveBeenCalled();
  });

  it("leaving during GET does not adopt its late result or send further requests", async () => {
    let resolve!: (value: PendingMoveTask) => void;
    api.acceptPendingMove.mockResolvedValue(task("running"));
    api.fetchPendingMove.mockReturnValue(new Promise(done => { resolve = done; }));
    const store = usePendingFileStore();
    const execution = store.executePendingMove([1], "target");
    await vi.advanceTimersByTimeAsync(1000);
    store.stopMovePolling();
    resolve(task());
    await execution;
    expect(store.currentMoveTask?.status).toBe("running");
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.fetchPendingMove).toHaveBeenCalledTimes(1);
  });

  it("leaving before POST clears known unsubmitted state and allows a later action", async () => {
    let finishNavigation!: () => void;
    const store = usePendingFileStore();
    const execution = store.executePendingMove([1], "target", undefined,
      () => new Promise<void>(resolve => { finishNavigation = resolve; }));
    store.stopMovePolling();
    finishNavigation();
    await execution;
    expect(api.acceptPendingMove).not.toHaveBeenCalled();
    expect(store.moveUncertain).toBe(false);
    expect(store.moveRequestId).toBeNull();
    expect(store.moveLoading).toBe(false);
    api.acceptPendingMove.mockResolvedValue(task());
    await store.executePendingMove([1], "target");
    expect(api.acceptPendingMove).toHaveBeenCalledTimes(1);
  });

  it("explicit rejection and failure before POST are not misreported as unknown external work", async () => {
    const store = usePendingFileStore();
    api.acceptPendingMove.mockRejectedValue({ response: { status: 400, data: { detail: "synthetic validation" } } });
    await store.executePendingMove([1], "target");
    expect(store.moveUncertain).toBe(false);
    expect(store.moveError).toBe("synthetic validation");
    api.acceptPendingMove.mockClear();
    await store.executePendingMove([1], "target", undefined, async () => { throw new Error("navigation stopped"); });
    expect(api.acceptPendingMove).not.toHaveBeenCalled();
    expect(store.moveUncertain).toBe(false);
  });
});
