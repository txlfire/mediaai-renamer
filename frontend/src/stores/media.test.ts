import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ScanJob } from "../api/client";
import { useAuthStore } from "./auth";
import { useMediaStore } from "./media";
import { invalidateSessionRequests, SessionChangedError } from "../utils/sessionGeneration";

const api = vi.hoisted(() => ({ acceptScanJob: vi.fn(), fetchScanJobs: vi.fn(), fetchLogs: vi.fn(), fetchScanModeSuggestion: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => ({ ...await importOriginal<typeof import("../api/client")>(), ...api }));

const job = { id: 7, media_source_id: 1, status: "completed", batch_size: 100, batch_interval_seconds: 0,
  scan_mode: "full", scanned_count: 1, video_count: 1, warning_count: 0, new_count: 1, changed_count: 0,
  skipped_count: 0, missing_count: 0, indexed_count: 1, error_message: null,
  started_at: "now", ended_at: "now" } satisfies ScanJob;

describe("scan result and auxiliary refresh", () => {
  it("lets a paged caller own refresh without downloading all history", async () => {
    setActivePinia(createPinia());
    api.acceptScanJob.mockResolvedValue(job);
    const store = useMediaStore();
    expect(await store.startScan(1, "full", false)).toEqual(job);
    expect(api.fetchScanJobs).not.toHaveBeenCalled();
    expect(api.fetchScanModeSuggestion).not.toHaveBeenCalled();
    expect(store.scanJobs).toEqual([]);
  });
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    api.acceptScanJob.mockResolvedValue(job);
    api.fetchScanJobs.mockResolvedValue([job]);
    api.fetchScanModeSuggestion.mockResolvedValue({ recommended_mode: "incremental" });
    api.fetchLogs.mockResolvedValue([]);
  });

  it("does not read protected logs without permission", async () => {
    vi.spyOn(useAuthStore(), "hasPermission").mockReturnValue(false);
    const store = useMediaStore();
    expect(await store.startScan(1)).toEqual(job);
    expect(api.fetchLogs).not.toHaveBeenCalled();
    expect(store.errorMessage).toBe("");
    expect(store.scanRefreshFailed).toBe(false);
  });

  it("preserves queued acceptance instead of reporting completion", async () => {
    const queued = { ...job, status: "queued", scanned_count: 0, video_count: 0, started_at: null, ended_at: null };
    api.acceptScanJob.mockResolvedValue(queued);
    api.fetchScanJobs.mockResolvedValue([queued]);
    const store = useMediaStore();
    expect(await store.startScan(1)).toEqual(queued);
    expect(store.scanJobs[0]?.status).toBe("queued");
  });

  it("keeps successful scan result when log refresh fails", async () => {
    vi.spyOn(useAuthStore(), "hasPermission").mockReturnValue(true);
    api.fetchLogs.mockRejectedValue(new Error("synthetic log read failure"));
    const store = useMediaStore();
    expect(await store.startScan(1)).toEqual(job);
    expect(store.errorMessage).toBe("");
    expect(store.scanRefreshFailed).toBe(true);
    expect(api.acceptScanJob).toHaveBeenCalledTimes(1);
  });

  it("retains returned task when list refresh fails", async () => {
    api.fetchScanJobs.mockRejectedValue(new Error("synthetic list read failure"));
    const store = useMediaStore();
    expect(await store.startScan(1)).toEqual(job);
    expect(store.scanJobs).toEqual([job]);
    expect(store.scanRefreshFailed).toBe(true);
    expect(store.errorMessage).toBe("");
  });

  it("reports primary failure without trying auxiliary refresh", async () => {
    api.acceptScanJob.mockRejectedValue(new Error("synthetic scan failure"));
    const store = useMediaStore();
    expect(await store.startScan(1)).toBeUndefined();
    expect(store.errorMessage).toBe("synthetic scan failure");
    expect(api.fetchScanJobs).not.toHaveBeenCalled();
  });

  it("does not refill cleared workspace from a previous session", async () => {
    let resolve!: (jobs: ScanJob[]) => void;
    api.fetchScanJobs.mockReturnValueOnce(new Promise<ScanJob[]>((done) => { resolve = done; }));
    const store = useMediaStore();
    const request = store.loadScanJobs();
    const rejected = expect(request).rejects.toBeInstanceOf(SessionChangedError);
    invalidateSessionRequests();
    store.$reset();
    resolve([job]);
    await rejected;
    expect(store.scanJobs).toEqual([]);
    expect(store.loading).toBe(false);
  });
});
