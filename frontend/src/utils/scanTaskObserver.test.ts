import { afterEach, expect, it, vi } from "vitest";
import type { ScanJob } from "../api/client";
import { createScanTaskObserver } from "./scanTaskObserver";

afterEach(() => vi.useRealTimers());
const job = (id: number, status: string) => ({ id, status } as ScanJob);

it("stops polling after a terminal result", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValueOnce(job(1, "queued")).mockResolvedValueOnce(job(1, "completed"));
  const observed = vi.fn();
  const observer = createScanTaskObserver(fetch, observed, vi.fn());
  observer.start(1);
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(observed).toHaveBeenLastCalledWith(job(1, "completed"));
  observer.stop();
});

it("does not overlap requests or accept stale task results", async () => {
  vi.useFakeTimers();
  let resolve: (job: ScanJob) => void = () => {};
  const fetch = vi.fn().mockReturnValueOnce(new Promise<ScanJob>((done) => { resolve = done; }))
    .mockResolvedValue(job(2, "completed"));
  const observed = vi.fn();
  const observer = createScanTaskObserver(fetch, observed, vi.fn());
  observer.start(1);
  observer.start(2);
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(1);
  resolve(job(1, "running"));
  await vi.advanceTimersByTimeAsync(1);
  expect(observed).toHaveBeenCalledTimes(1);
  expect(observed).toHaveBeenCalledWith(job(2, "completed"));
  observer.stop();
});

it("ignores a response after stop", async () => {
  let resolve: (job: ScanJob) => void = () => {};
  const fetch = vi.fn().mockReturnValue(new Promise<ScanJob>((done) => { resolve = done; }));
  const observed = vi.fn();
  const observer = createScanTaskObserver(fetch, observed, vi.fn());
  observer.start(1);
  observer.stop();
  resolve(job(1, "completed"));
  await Promise.resolve();
  expect(observed).not.toHaveBeenCalled();
});

it("backs off status read failures without submitting a new task", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockRejectedValue(new Error("synthetic"));
  const error = vi.fn();
  const observer = createScanTaskObserver(fetch, vi.fn(), error);
  observer.start(1);
  await vi.advanceTimersByTimeAsync(3999);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(error).toHaveBeenCalledTimes(2);
  observer.stop();
});
