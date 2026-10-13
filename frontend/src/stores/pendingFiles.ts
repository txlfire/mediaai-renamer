import { getSessionGeneration, sessionRequest, SessionChangedError } from "../utils/sessionGeneration";
import { defineStore } from "pinia";

import {
  clearPendingFiles,
  fetchPendingFiles,
  movePendingFiles,
  parsePendingFileWithAi,
  removePendingFile,
  acceptPendingMove, fetchPendingMove, findPendingMove,
  type PendingMoveTask,
  type AiParseResult,
  type PendingFile,
  type PendingFileFilters,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const usePendingFileStore = defineStore("pendingFiles", {
  state: () => ({
    pendingFiles: [] as PendingFile[],
    filters: {} as PendingFileFilters,
    loading: false,
    refreshFailed: false,
    currentMoveTask: null as PendingMoveTask | null,
    moveTaskId: null as number | null,
    moveRequestId: null as string | null,
    moveLoading: false,
    moveUncertain: false,
    moveError: "",
    moveVersion: 0,
  }),
  actions: {
    stopMovePolling() {
      this.moveVersion += 1;
      this.moveLoading = false;
    },
    async executePendingMove(ids: number[], target: string, onProgress?: (task: PendingMoveTask) => void,
      beforeSubmit?: (requestId: string) => Promise<void>) {
      const sessionGeneration = getSessionGeneration();
      if (this.moveLoading || this.moveUncertain || ["queued", "running"].includes(this.currentMoveTask?.status ?? "")) return;
      this.moveLoading = true;
      this.moveError = "";
      this.moveUncertain = true;
      this.currentMoveTask = null;
      this.moveTaskId = null;
      const version = ++this.moveVersion;
      let submitted = false;
      try {
        // 局域网HTTP也使用可用的getRandomValues，不依赖安全上下文限定的randomUUID。
        const requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
        this.moveRequestId = requestId;
        await sessionRequest(beforeSubmit?.(requestId), sessionGeneration);
        if (version !== this.moveVersion) return;
        submitted = true;
        const task = await sessionRequest(acceptPendingMove([...ids], target, requestId), sessionGeneration);
        if (version !== this.moveVersion) return;
        this.currentMoveTask = task;
        this.moveTaskId = task.id;
        this.moveUncertain = false;
        onProgress?.(task);
        await sessionRequest(this.pollPendingMove(task.id, version, onProgress), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        if (version !== this.moveVersion) return;
        const response = (error as { response?: { status?: number; data?: { detail?: string } } })?.response;
        if (!submitted || (!this.currentMoveTask && [400, 401, 403, 404, 422].includes(response?.status ?? 0))) {
          this.moveUncertain = false;
          this.moveRequestId = null;
          this.moveError = typeof response?.data?.detail === "string" ? response.data.detail : messages.errors.unknown;
        } else {
          this.moveUncertain = true;
          this.moveError = messages.renamePreviews.pendingFiles.queryBeforeRetry;
        }
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          // 离页仅停止观察；但尚未发出的请求没有未知副作用，可以解除本地阻塞。
          if (!submitted) {
            this.moveUncertain = false;
            this.moveRequestId = null;
          }
          if (version === this.moveVersion) this.moveLoading = false;
        }
      }
    },
    async pollPendingMove(taskId: number, version: number, onProgress?: (task: PendingMoveTask) => void) {
      const sessionGeneration = getSessionGeneration();
      while (version === this.moveVersion && this.currentMoveTask?.id === taskId
        && ["queued", "running"].includes(this.currentMoveTask.status)) {
        await sessionRequest(new Promise(resolve => setTimeout(resolve, 1000)), sessionGeneration);
        if (version !== this.moveVersion || this.currentMoveTask?.id !== taskId) return;
        const task = await sessionRequest(fetchPendingMove(taskId), sessionGeneration);
        if (version !== this.moveVersion || this.currentMoveTask?.id !== taskId) return;
        this.currentMoveTask = task;
        onProgress?.(task);
      }
    },
    async refreshPendingMove(taskId?: number, requestId?: string, onProgress?: (task: PendingMoveTask) => void) {
      const sessionGeneration = getSessionGeneration();
      if (this.moveLoading) return;
      const id = taskId ?? (requestId ? undefined : this.moveTaskId ?? this.currentMoveTask?.id);
      const key = requestId ?? this.moveRequestId;
      if (!id && !key) return;
      const version = ++this.moveVersion;
      this.moveLoading = true;
      this.moveUncertain = true;
      this.moveError = "";
      if (requestId && !taskId) {
        this.currentMoveTask = null;
        this.moveTaskId = null;
        this.moveRequestId = requestId;
      } else if (taskId) {
        if (taskId !== this.currentMoveTask?.id) {
          this.currentMoveTask = null;
          this.moveRequestId = null;
        }
        this.moveTaskId = taskId;
      }
      try {
        const task = id ? await sessionRequest(fetchPendingMove(id), sessionGeneration) : await sessionRequest(findPendingMove(key!), sessionGeneration);
        if (version !== this.moveVersion) return;
        if (!task) throw new Error("not found");
        this.currentMoveTask = task;
        this.moveTaskId = task.id;
        this.moveUncertain = false;
        onProgress?.(task);
        await sessionRequest(this.pollPendingMove(task.id, version, onProgress), sessionGeneration);
      } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
        if (version === this.moveVersion) this.moveError = messages.renamePreviews.pendingFiles.queryBeforeRetry;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.moveVersion) this.moveLoading = false;
        }
      }
    },
    async loadPendingFiles(filters?: PendingFileFilters) {
      const sessionGeneration = getSessionGeneration();
      this.filters = { ...(filters ?? this.filters) };
      this.pendingFiles = await sessionRequest(fetchPendingFiles(this.filters), sessionGeneration);
      this.refreshFailed = false;
    },

    async removePendingFile(id: number) {
      const sessionGeneration = getSessionGeneration();
      await sessionRequest(removePendingFile(id), sessionGeneration);
      this.pendingFiles = this.pendingFiles.filter((item) => item.id !== id);
    },

    async clearPendingFiles(filters?: PendingFileFilters) {
      const sessionGeneration = getSessionGeneration();
      const result = await sessionRequest(clearPendingFiles(filters ?? this.filters), sessionGeneration);
      this.pendingFiles = this.pendingFiles.filter((item) => item.status !== "pending");
      return result.removed_count;
    },

    async movePendingFiles(ids: number[], targetDirectory: string, refreshHistory = true) {
      const sessionGeneration = getSessionGeneration();
      try {
        const moved = await sessionRequest(movePendingFiles(ids, targetDirectory), sessionGeneration);
        if (Array.isArray(moved)) {
          const movedIds = new Set(moved.map((item) => item.id));
          this.pendingFiles = this.pendingFiles.filter((item) => !movedIds.has(item.id));
        } else {
          this.currentMoveTask = moved;
          if (refreshHistory) await sessionRequest(this.loadPendingFiles(), sessionGeneration);
          else this.refreshFailed = true;
        }
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        try {
          if (refreshHistory) await sessionRequest(this.loadPendingFiles(), sessionGeneration);
          else this.refreshFailed = true;
        } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
          this.refreshFailed = true;
        }
        throw error;
      }
    },

    async parseWithAi(id: number): Promise<AiParseResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      try {
        return await sessionRequest(parsePendingFileWithAi(id), sessionGeneration);
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },
  },
});
