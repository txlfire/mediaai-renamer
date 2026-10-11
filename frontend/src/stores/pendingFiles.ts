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
        await beforeSubmit?.(requestId);
        if (version !== this.moveVersion) return;
        submitted = true;
        const task = await acceptPendingMove([...ids], target, requestId);
        if (version !== this.moveVersion) return;
        this.currentMoveTask = task;
        this.moveTaskId = task.id;
        this.moveUncertain = false;
        onProgress?.(task);
        await this.pollPendingMove(task.id, version, onProgress);
      } catch (error) {
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
        // 离页仅停止观察；但尚未发出的请求没有未知副作用，可以解除本地阻塞。
        if (!submitted) {
          this.moveUncertain = false;
          this.moveRequestId = null;
        }
        if (version === this.moveVersion) this.moveLoading = false;
      }
    },
    async pollPendingMove(taskId: number, version: number, onProgress?: (task: PendingMoveTask) => void) {
      while (version === this.moveVersion && this.currentMoveTask?.id === taskId
        && ["queued", "running"].includes(this.currentMoveTask.status)) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (version !== this.moveVersion || this.currentMoveTask?.id !== taskId) return;
        const task = await fetchPendingMove(taskId);
        if (version !== this.moveVersion || this.currentMoveTask?.id !== taskId) return;
        this.currentMoveTask = task;
        onProgress?.(task);
      }
    },
    async refreshPendingMove(taskId?: number, requestId?: string, onProgress?: (task: PendingMoveTask) => void) {
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
        const task = id ? await fetchPendingMove(id) : await findPendingMove(key!);
        if (version !== this.moveVersion) return;
        if (!task) throw new Error("not found");
        this.currentMoveTask = task;
        this.moveTaskId = task.id;
        this.moveUncertain = false;
        onProgress?.(task);
        await this.pollPendingMove(task.id, version, onProgress);
      } catch {
        if (version === this.moveVersion) this.moveError = messages.renamePreviews.pendingFiles.queryBeforeRetry;
      } finally {
        if (version === this.moveVersion) this.moveLoading = false;
      }
    },
    async loadPendingFiles(filters?: PendingFileFilters) {
      this.filters = { ...(filters ?? this.filters) };
      this.pendingFiles = await fetchPendingFiles(this.filters);
      this.refreshFailed = false;
    },

    async removePendingFile(id: number) {
      await removePendingFile(id);
      this.pendingFiles = this.pendingFiles.filter((item) => item.id !== id);
    },

    async clearPendingFiles(filters?: PendingFileFilters) {
      const result = await clearPendingFiles(filters ?? this.filters);
      this.pendingFiles = this.pendingFiles.filter((item) => item.status !== "pending");
      return result.removed_count;
    },

    async movePendingFiles(ids: number[], targetDirectory: string, refreshHistory = true) {
      try {
        const moved = await movePendingFiles(ids, targetDirectory);
        if (Array.isArray(moved)) {
          const movedIds = new Set(moved.map((item) => item.id));
          this.pendingFiles = this.pendingFiles.filter((item) => !movedIds.has(item.id));
        } else {
          this.currentMoveTask = moved;
          if (refreshHistory) await this.loadPendingFiles();
          else this.refreshFailed = true;
        }
      } catch (error) {
        try {
          if (refreshHistory) await this.loadPendingFiles();
          else this.refreshFailed = true;
        } catch {
          this.refreshFailed = true;
        }
        throw error;
      }
    },

    async parseWithAi(id: number): Promise<AiParseResult> {
      this.loading = true;
      try {
        return await parsePendingFileWithAi(id);
      } finally {
        this.loading = false;
      }
    },
  },
});
