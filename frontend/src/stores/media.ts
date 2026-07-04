import { defineStore } from "pinia";

import {
  bulkDeleteMediaSources,
  cleanupLogs,
  createMediaSource,
  createScanJob,
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

export const useMediaStore = defineStore("media", {
  state: () => ({
    mediaSources: [] as MediaSource[],
    scanJobs: [] as ScanJob[],
    scanModeSuggestion: null as ScanModeSuggestion | null,
    mediaFiles: [] as MediaFile[],
    logItems: [] as LogItem[],
    loading: false,
    errorMessage: "",
    logDrawerVisible: false,
  }),
  actions: {
    async loadMediaSources() {
      this.mediaSources = await fetchMediaSources();
    },

    async addMediaSource(payload: MediaSourceCreatePayload) {
      await createMediaSource(payload);
      await this.loadMediaSources();
    },

    async editMediaSource(
      sourceId: number,
      payload: MediaSourceUpdatePayload,
    ): Promise<MediaSourceMutationResult> {
      const result = await updateMediaSource(sourceId, payload);
      await this.loadMediaSources();
      return result;
    },

    async toggleMediaSource(sourceId: number, enabled: boolean) {
      await setMediaSourceEnabled(sourceId, enabled);
      await this.loadMediaSources();
    },

    async removeMediaSource(sourceId: number): Promise<MediaSourceMutationResult> {
      const result = await deleteMediaSource(sourceId);
      await this.loadMediaSources();
      return result;
    },

    async removeMediaSources(sourceIds: number[]): Promise<MediaSourceMutationResult> {
      const result = await bulkDeleteMediaSources(sourceIds);
      await this.loadMediaSources();
      return result;
    },

    cleanupTotal(summary: CleanupSummary) {
      return Object.values(summary).reduce((total, count) => total + count, 0);
    },

    async loadScanJobs(filters: ScanJobFilters = {}) {
      this.scanJobs = await fetchScanJobs(filters);
    },

    async loadScanModeSuggestion(mediaSourceId: number) {
      this.scanModeSuggestion = await fetchScanModeSuggestion(mediaSourceId);
      return this.scanModeSuggestion;
    },

    async startScan(mediaSourceId: number, scanMode: ScanMode = "full") {
      this.loading = true;
      this.errorMessage = "";
      try {
        await createScanJob(mediaSourceId, scanMode);
        await Promise.all([
          this.loadScanJobs({ media_source_id: mediaSourceId }),
          this.loadLogs(),
          this.loadScanModeSuggestion(mediaSourceId),
        ]);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.errors.scanJobFailed;
      } finally {
        this.loading = false;
      }
    },

    async loadMediaFiles(filters: MediaFileFilters = {}) {
      this.mediaFiles = await fetchMediaFiles(filters);
    },

    async loadLogs() {
      this.logItems = await fetchLogs();
    },

    async cleanupLogs(): Promise<LogCleanupSummary> {
      const result = await cleanupLogs();
      await this.loadLogs();
      return result;
    },

    async openLogDrawer() {
      await this.loadLogs();
      this.logDrawerVisible = true;
    },
  },
});
