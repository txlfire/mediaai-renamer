import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackgroundJobsStore } from "./backgroundJobs";
import { useAuthStore } from "./auth";
import { invalidateSessionRequests, SessionChangedError } from "../utils/sessionGeneration";
import type { BackgroundJob } from "../api/client";

const api = vi.hoisted(() => ({ acceptBackgroundJob: vi.fn(), fetchBackgroundJob: vi.fn(), fetchRecentBackgroundJobs: vi.fn() }));
vi.mock("../api/client", () => api);

const id = "11111111-1111-4111-8111-111111111111";
function job(status = "completed", requestId = id): BackgroundJob {
  return { id: 1, request_id: requestId, kind: "ai_batch", status, total_count: 2, completed_count: status === "running" ? 1 : 2,
    unknown_count: 0, success_count: 2, failed_count: 0, result_available: status === "completed", result_cleared_at: null,
    error_message: null, created_at: "2026-10-14T00:00:00Z", updated_at: "2026-10-14T00:00:00Z" };
}
function pending(owner = 1, requestId = id, ids = [1, 2]) {
  return { owner, requestId, kind: "ai_batch", key: JSON.stringify(["ai_batch", { rename_preview_ids: ids }]) };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure; });
  return { promise, resolve, reject };
}

describe("background job observation", () => {
  beforeEach(() => {
    invalidateSessionRequests();
    setActivePinia(createPinia());
    const data = new Map<string, string>();
    vi.stubGlobal("sessionStorage", { getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => data.set(key, value) });
    vi.resetAllMocks();
    useAuthStore().currentUser = { id: 1, username: "synthetic", enabled: true, permissions: ["metadata:submit"] } as never;
    api.acceptBackgroundJob.mockImplementation(async (_kind, requestId) => job("queued", requestId));
    api.fetchRecentBackgroundJobs.mockResolvedValue([]);
    api.fetchBackgroundJob.mockImplementation(async (requestId, result) => ({ ...job("completed", requestId), ...(result ? { result: { success_count: 2 } } : {}) }));
  });
  afterEach(() => { invalidateSessionRequests(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("stores a stable UUID before acceptance and fetches the large result only once", async () => {
    api.acceptBackgroundJob.mockImplementation(async (_kind, requestId) => {
      expect(JSON.parse(sessionStorage.getItem("mediaai-background-requests")!)[0].requestId).toBe(requestId);
      expect(requestId).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
      return job("queued", requestId);
    });
    const store = useBackgroundJobsStore();
    expect(await store.run("ai_batch", { rename_preview_ids: [2, 1, 2] })).toEqual({ success_count: 2 });
    expect(api.acceptBackgroundJob).toHaveBeenCalledTimes(1);
    expect(api.fetchBackgroundJob.mock.calls.map(call => call[1])).toEqual([false, true]);
    expect(store.activeJob).not.toHaveProperty("result");
    expect(store.hasPending(store.activeRequestId)).toBe(false);
  });

  it("never reposts when the acceptance response is lost; the next click queries the original ID", async () => {
    api.acceptBackgroundJob.mockRejectedValueOnce(new Error("synthetic network disconnect"));
    const store = useBackgroundJobsStore();
    await expect(store.run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toThrow("disconnect");
    const original = store.activeRequestId;
    expect(store.hasPending(original)).toBe(true);
    await store.run("ai_batch", { rename_preview_ids: [2, 1] });
    expect(store.activeRequestId).toBe(original);
    expect(api.acceptBackgroundJob).toHaveBeenCalledTimes(1);
  });

  it("restores pending observation after reload without sending an external request", async () => {
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify([pending()]));
    const store = useBackgroundJobsStore();
    await store.loadRecent();
    expect(store.activeRequestId).toBe(id);
    await store.run("ai_batch", { rename_preview_ids: [1, 2] });
    expect(api.acceptBackgroundJob).not.toHaveBeenCalled();
  });

  it("retains the ID when a query returns not found rather than silently recreating a task", async () => {
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify([pending()]));
    api.fetchBackgroundJob.mockRejectedValue(new Error("not found"));
    const store = useBackgroundJobsStore();
    await expect(store.run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toThrow("not found");
    expect(store.hasPending(id)).toBe(true);
    expect(api.acceptBackgroundJob).not.toHaveBeenCalled();
  });

  it.each([400, 401, 403, 409, 422])("known rejection %s can be corrected without retaining an unaccepted ID", async status => {
    api.acceptBackgroundJob.mockRejectedValue({ response: { status } });
    const store = useBackgroundJobsStore();
    await expect(store.run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toBeDefined();
    expect(store.hasPending(store.activeRequestId)).toBe(false);
  });

  it("keeps interrupted or unknown tasks until explicit acknowledgement", async () => {
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify([pending()]));
    api.fetchBackgroundJob.mockResolvedValue({ ...job("interrupted"), unknown_count: 1, error_message: "needs inspection" });
    const store = useBackgroundJobsStore();
    await expect(store.run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toThrow("inspection");
    expect(store.hasPending(id)).toBe(true);
    store.acknowledge(id);
    expect(store.hasPending(id)).toBe(false);
  });

  it("does not read a cleared result or replay its task", async () => {
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify([pending()]));
    api.fetchBackgroundJob.mockResolvedValue({ ...job(), result_available: false, result_cleared_at: "2026-10-14" });
    const store = useBackgroundJobsStore();
    await expect(store.run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toThrow("清理");
    expect(api.fetchBackgroundJob).toHaveBeenCalledTimes(1);
    expect(api.acceptBackgroundJob).not.toHaveBeenCalled();
  });

  it("refuses new work instead of discarding 50 pending IDs", async () => {
    const rows = Array.from({ length: 50 }, (_, index) => pending(1, id, [index + 100]));
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify(rows));
    await expect(useBackgroundJobsStore().run("ai_batch", { rename_preview_ids: [1, 2] })).rejects.toThrow("50");
    expect(JSON.parse(sessionStorage.getItem("mediaai-background-requests")!)).toHaveLength(50);
    expect(api.acceptBackgroundJob).not.toHaveBeenCalled();
  });

  it("does not submit when the recovery ID cannot be persisted", async () => {
    vi.stubGlobal("sessionStorage", { getItem: () => null, setItem: () => { throw new Error("quota"); } });
    await expect(useBackgroundJobsStore().run("ai_batch", { rename_preview_ids: [1] })).rejects.toThrow();
    expect(api.acceptBackgroundJob).not.toHaveBeenCalled();
  });

  it.each(["success", "failure"])("ignores a late %s from an older session", async result => {
    const delayed = deferred<BackgroundJob>();
    api.fetchBackgroundJob.mockReturnValue(delayed.promise);
    const store = useBackgroundJobsStore();
    const request = store.refresh(id, true);
    const rejected = expect(request).rejects.toBeInstanceOf(SessionChangedError);
    invalidateSessionRequests();
    store.$reset();
    store.errorMessage = "new session";
    if (result === "success") delayed.resolve({ ...job(), result: { private: "old" } });
    else delayed.reject(new Error("old failure"));
    await rejected;
    expect(store.jobs).toEqual({});
    expect(store.resultRecord).toBeNull();
    expect(store.errorMessage).toBe("new session");
  });

  it("deduplicates concurrent status reads", async () => {
    const delayed = deferred<BackgroundJob>();
    api.fetchBackgroundJob.mockReturnValue(delayed.promise);
    const store = useBackgroundJobsStore();
    const requests = [store.refresh(id), store.refresh(id)];
    delayed.resolve(job());
    await Promise.all(requests);
    expect(api.fetchBackgroundJob).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "failure"])("does not observe or overwrite the newer session after late acceptance %s", async result => {
    const delayed = deferred<BackgroundJob>();
    api.acceptBackgroundJob.mockReturnValue(delayed.promise);
    const store = useBackgroundJobsStore();
    const request = store.run("ai_batch", { rename_preview_ids: [1] });
    const rejected = expect(request).rejects.toBeInstanceOf(SessionChangedError);
    invalidateSessionRequests();
    store.$reset();
    store.activeRequestId = "new-session-request";
    if (result === "success") delayed.resolve(job());
    else delayed.reject({ response: { status: 401 } });
    await rejected;
    expect(store.activeRequestId).toBe("new-session-request");
    expect(store.jobs).toEqual({});
    expect(api.fetchBackgroundJob).not.toHaveBeenCalled();
    expect(JSON.parse(sessionStorage.getItem("mediaai-background-requests")!)).toHaveLength(1);
  });

  it("polls summaries at a finite interval then reads no unsolicited result", async () => {
    vi.useFakeTimers();
    api.fetchBackgroundJob.mockResolvedValueOnce(job("running")).mockResolvedValueOnce(job());
    const store = useBackgroundJobsStore();
    const request = store.observe(id);
    await vi.advanceTimersByTimeAsync(1499);
    expect(api.fetchBackgroundJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect((await request).status).toBe("completed");
    expect(api.fetchBackgroundJob.mock.calls.every(call => call[1] === false)).toBe(true);
    expect(store.isObserved(id)).toBe(false);
  });

  it("never restores another user's pending request", async () => {
    sessionStorage.setItem("mediaai-background-requests", JSON.stringify([pending(2)]));
    const store = useBackgroundJobsStore();
    await store.loadRecent();
    expect(store.activeRequestId).toBe("");
    await store.run("ai_batch", { rename_preview_ids: [1, 2] });
    expect(api.acceptBackgroundJob).toHaveBeenCalledTimes(1);
    expect(JSON.parse(sessionStorage.getItem("mediaai-background-requests")!)[0].owner).toBe(2);
  });
});
