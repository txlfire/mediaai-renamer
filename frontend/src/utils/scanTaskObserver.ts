import type { ScanJob } from "../api/client";

export function isScanActive(status: string): boolean {
  return status === "queued" || status === "running";
}

export function createScanTaskObserver(fetchJob: (id: number) => Promise<ScanJob>,
  onJob: (job: ScanJob) => void, onError: () => void) {
  let taskId: number | undefined;
  let generation = 0;
  let inFlight = false;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function stop() {
    generation += 1;
    taskId = undefined;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  async function poll() {
    if (taskId === undefined || inFlight) return;
    const id = taskId;
    const current = generation;
    inFlight = true;
    try {
      const job = await fetchJob(id);
      if (current !== generation) return;
      failures = 0;
      onJob(job);
      if (!isScanActive(job.status)) taskId = undefined;
    } catch {
      if (current === generation) {
        failures += 1;
        onError();
      }
    } finally {
      inFlight = false;
      if (taskId !== undefined) {
        const delay = current !== generation ? 0 : failures
          ? Math.min(30000, 2000 * 2 ** Math.min(failures, 4))
          : typeof document !== "undefined" && document.hidden ? 6000 : 2000;
        timer = setTimeout(() => void poll(), delay);
      }
    }
  }

  return {
    stop,
    start(id: number) {
      stop();
      taskId = id;
      failures = 0;
      void poll();
    },
  };
}
