import { defineStore } from "pinia";
import { acceptBackgroundJob, fetchBackgroundJob, fetchRecentBackgroundJobs, type BackgroundJob, type BackgroundJobKind, type BackgroundJobPayload } from "../api/client";
import { useAuthStore } from "./auth";
import { getSessionGeneration, sessionRequest, SessionChangedError } from "../utils/sessionGeneration";
import { zhCnMessages as messages } from "../locales/zh-CN";

const storageKey = "mediaai-background-requests";
type PendingRequest = { owner: number; key: string; requestId: string; kind: BackgroundJobKind };
const observations = new Map<string, Promise<BackgroundJob>>();
const reads = new Map<string, Promise<BackgroundJob>>();
const terminal = new Set(["completed", "partial_completed", "failed", "interrupted"]);

function savedRequests(): PendingRequest[] {
  try {
    const value: unknown = JSON.parse(globalThis.sessionStorage?.getItem(storageKey) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is PendingRequest => Boolean(item && Number.isSafeInteger(item.owner)
      && typeof item.key === "string" && typeof item.requestId === "string" && /^[\da-f-]{36}$/i.test(item.requestId)
      && ["rename_dry_run", "rollback_dry_run", "metadata_batch", "ai_batch"].includes(item.kind)));
  } catch {
    return [];
  }
}

function saveRequests(value: PendingRequest[]) {
  try {
    if (!globalThis.sessionStorage) throw new Error();
    globalThis.sessionStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    throw new Error(messages.backgroundJobs.storageUnavailable);
  }
}

function requestKey(kind: BackgroundJobKind, payload: BackgroundJobPayload): string {
  const values = { ...payload, ...(payload.rename_preview_ids ? { rename_preview_ids: [...new Set(payload.rename_preview_ids)].sort((a, b) => a - b) } : {}) };
  return JSON.stringify([kind, Object.fromEntries(Object.entries(values).sort(([a], [b]) => a.localeCompare(b)))]);
}

function requestId() {
  // 局域网HTTP未必提供randomUUID，但现代浏览器仍提供安全随机字节。
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const useBackgroundJobsStore = defineStore("backgroundJobs", {
  state: () => ({ jobs: {} as Record<string, BackgroundJob>, activeRequestId: "", resultRecord: null as BackgroundJob | null, errorMessage: "" }),
  getters: {
    activeJob: state => state.jobs[state.activeRequestId] ?? null,
    recent: state => Object.values(state.jobs).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 20),
  },
  actions: {
    async loadRecent() {
      const generation = getSessionGeneration();
      const rows = await sessionRequest(fetchRecentBackgroundJobs(), generation);
      this.jobs = Object.fromEntries(rows.map(row => [row.request_id, row]));
      if (!this.activeRequestId) {
        const owner = useAuthStore().currentUser?.id;
        this.activeRequestId = savedRequests().filter(item => item.owner === owner).slice(-1)[0]?.requestId || rows[0]?.request_id || "";
      }
    },

    async refresh(id: string, includeResult = false): Promise<BackgroundJob> {
      const generation = getSessionGeneration();
      const owner = useAuthStore().currentUser?.id;
      const key = `${generation}:${owner}:${id}:${includeResult}`;
      if (reads.has(key)) return reads.get(key)!;
      const promise = (async () => {
        try {
          const job = await sessionRequest(fetchBackgroundJob(id, includeResult), generation);
          const { result, items, ...summary } = job;
          this.jobs[id] = summary;
          if (includeResult && this.activeRequestId === id) this.resultRecord = job;
          this.errorMessage = "";
          return job;
        } finally {
          reads.delete(key);
        }
      })();
      reads.set(key, promise);
      return promise;
    },

    async observe(id: string): Promise<BackgroundJob> {
      const generation = getSessionGeneration();
      const key = `${generation}:${useAuthStore().currentUser?.id}:${id}`;
      if (observations.has(key)) return observations.get(key)!;
      const promise = (async () => {
        try {
          while (generation === getSessionGeneration()) {
            const job = await this.refresh(id);
            if (terminal.has(job.status)) return job;
            const delay = typeof document !== "undefined" && document.hidden ? 6000 : 1500;
            await new Promise(resolve => setTimeout(resolve, delay));
          }
          throw new SessionChangedError();
        } finally {
          observations.delete(key);
        }
      })();
      observations.set(key, promise);
      return promise;
    },

    isObserved(id: string) {
      return observations.has(`${getSessionGeneration()}:${useAuthStore().currentUser?.id}:${id}`);
    },

    acknowledge(id: string) {
      const owner = useAuthStore().currentUser?.id;
      if (!this.jobs[id] || !terminal.has(this.jobs[id].status)) return;
      saveRequests(savedRequests().filter(item => item.owner !== owner || item.requestId !== id));
    },

    async run<T>(kind: BackgroundJobKind, payload: BackgroundJobPayload): Promise<T> {
      const generation = getSessionGeneration();
      const owner = useAuthStore().currentUser?.id;
      if (!owner) throw new Error(messages.backgroundJobs.noPermission);
      const key = requestKey(kind, payload);
      const previous = savedRequests().find(item => item.owner === owner && item.key === key);
      const id = previous?.requestId || requestId();
      this.activeRequestId = id;
      this.resultRecord = null;
      this.errorMessage = "";
      if (!previous) {
        if (savedRequests().length >= 50) throw new Error(messages.backgroundJobs.pendingLimit);
        saveRequests([...savedRequests(), { owner, key, requestId: id, kind }]);
        try {
          const accepted = await sessionRequest(acceptBackgroundJob(kind, id, payload), generation);
          this.jobs[id] = accepted;
        } catch (error) {
          if (generation !== getSessionGeneration()) throw new SessionChangedError();
          const status = (error as { response?: { status?: number } }).response?.status;
          if (status && [400, 401, 403, 409, 422].includes(status)) {
            saveRequests(savedRequests().filter(item => item.requestId !== id || item.owner !== owner));
          }
          this.errorMessage = `${messages.backgroundJobs.queryBeforeRetry} ${id}`;
          throw error;
        }
      }
      try {
        const current = await this.observe(id);
        if (!["completed", "partial_completed"].includes(current.status)) {
          throw new Error(current.error_message || messages.backgroundJobs.queryBeforeRetry);
        }
        if (!current.result_available) throw new Error(messages.backgroundJobs.resultCleared);
        const result = await this.refresh(id, true);
        if (!current.unknown_count) this.acknowledge(id);
        return result.result as T;
      } catch (error) {
        if (generation !== getSessionGeneration()) throw new SessionChangedError();
        this.errorMessage = error instanceof Error ? error.message : messages.backgroundJobs.queryBeforeRetry;
        throw error;
      }
    },

    hasPending(id: string) {
      const owner = useAuthStore().currentUser?.id;
      return savedRequests().some(item => item.owner === owner && item.requestId === id);
    },
  },
});
