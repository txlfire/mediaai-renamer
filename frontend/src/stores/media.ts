import { getSessionGeneration, sessionRequest } from "../utils/sessionGeneration";
import { defineStore } from "pinia";

import {
  bulkDeleteMediaSources,
  cleanupLogs,
  createMediaSource,
  acceptScanJob,
  deleteMediaSource,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchScanJobs,
  fetchScanModeSuggestion,
  setMediaSourceEnabled,
  updateMediaSource,
  type CleanupSummary,
  type LogItem,
  type LogCleanupSummary,
  type MediaFile,
  type MediaFileFilters,
  type MediaSource,
  type MediaSourceCreatePayload,
  type MediaSourceMutationResult,
  type MediaSourceUpdatePayload,
  type ScanJob,
  type ScanJobFilters,
  type ScanMode,
  type ScanModeSuggestion,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useAuthStore } from "./auth";

export const useMediaStore = defineStore("media", {
  state: () => ({
    mediaSources: [] as MediaSource[],
    scanJobs: [] as ScanJob[],
    scanModeSuggestion: null as ScanModeSuggestion | null,
    mediaFiles: [] as MediaFile[],
    logItems: [] as LogItem[],
    loading: false,
    errorMessage: "",
    scanRefreshFailed: false,
    logDrawerVisible: false,
  }),
  actions: {
    async loadMediaSources() {
      const sessionGeneration = getSessionGeneration();
      this.mediaSources = await sessionRequest(fetchMediaSources(), sessionGeneration);
    },

    async addMediaSource(payload: MediaSourceCreatePayload) {
      const sessionGeneration = getSessionGeneration();
      await sessionRequest(createMediaSource(payload), sessionGeneration);
      await sessionRequest(this.loadMediaSources(), sessionGeneration);
    },

    async editMediaSource(
      sourceId: number,
      payload: MediaSourceUpdatePayload,
    ): Promise<MediaSourceMutationResult> {
      const sessionGeneration = getSessionGeneration();
      const result = await sessionRequest(updateMediaSource(sourceId, payload), sessionGeneration);
      await sessionRequest(this.loadMediaSources(), sessionGeneration);
      return result;
    },

    async toggleMediaSource(sourceId: number, enabled: boolean) {
      const sessionGeneration = getSessionGeneration();
      await sessionRequest(setMediaSourceEnabled(sourceId, enabled), sessionGeneration);
      await sessionRequest(this.loadMediaSources(), sessionGeneration);
    },

    async removeMediaSource(sourceId: number): Promise<MediaSourceMutationResult> {
      const sessionGeneration = getSessionGeneration();
      const result = await sessionRequest(deleteMediaSource(sourceId), sessionGeneration);
      await sessionRequest(this.loadMediaSources(), sessionGeneration);
      return result;
    },

    async removeMediaSources(sourceIds: number[]): Promise<MediaSourceMutationResult> {
      const sessionGeneration = getSessionGeneration();
      const result = await sessionRequest(bulkDeleteMediaSources(sourceIds), sessionGeneration);
      await sessionRequest(this.loadMediaSources(), sessionGeneration);
      return result;
    },

    cleanupTotal(summary: CleanupSummary) {
      return Object.values(summary).reduce((total, count) => total + count, 0);
    },

    async loadScanJobs(filters: ScanJobFilters = {}) {
      const sessionGeneration = getSessionGeneration();
      this.scanJobs = await sessionRequest(fetchScanJobs(filters), sessionGeneration);
    },

    async loadScanModeSuggestion(mediaSourceId: number) {
      const sessionGeneration = getSessionGeneration();
      this.scanModeSuggestion = await sessionRequest(fetchScanModeSuggestion(mediaSourceId), sessionGeneration);
      return this.scanModeSuggestion;
    },

    async startScan(mediaSourceId: number, scanMode: ScanMode = "full", refreshHistory = true) {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      this.scanRefreshFailed = false;
      try {
        const job = await sessionRequest(acceptScanJob(mediaSourceId, scanMode), sessionGeneration);
        if (!refreshHistory) return job;
        this.scanJobs = [...this.scanJobs.filter((item) => item.id !== job.id), job];
        const refreshes = [
          this.loadScanJobs({ media_source_id: mediaSourceId }),
          this.loadScanModeSuggestion(mediaSourceId),
        ];
        if (useAuthStore().hasPermission("audit:read")) refreshes.push(this.loadLogs());
        const results = await sessionRequest(Promise.allSettled(refreshes), sessionGeneration);
        this.scanRefreshFailed = results.some((item) => item.status === "rejected");
        return job;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.scanJobFailed;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async loadMediaFiles(filters: MediaFileFilters = {}) {
      const sessionGeneration = getSessionGeneration();
      this.mediaFiles = await sessionRequest(fetchMediaFiles(filters), sessionGeneration);
    },

    async loadLogs() {
      const sessionGeneration = getSessionGeneration();
      this.logItems = await sessionRequest(fetchLogs(), sessionGeneration);
    },

    async cleanupLogs(): Promise<LogCleanupSummary> {
      const sessionGeneration = getSessionGeneration();
      const result = await sessionRequest(cleanupLogs(), sessionGeneration);
      await sessionRequest(this.loadLogs(), sessionGeneration);
      return result;
    },

    async openLogDrawer() {
      const sessionGeneration = getSessionGeneration();
      await sessionRequest(this.loadLogs(), sessionGeneration);
      this.logDrawerVisible = true;
    },
  },
});
