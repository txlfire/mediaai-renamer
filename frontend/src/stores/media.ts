import { defineStore } from "pinia";

import {
  bulkDeleteMediaSources,
  createMediaSource,
  createScanJob,
  deleteMediaSource,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchScanJobs,
  setMediaSourceEnabled,
  updateMediaSource,
  type CleanupSummary,
  type LogItem,
  type MediaFile,
  type MediaFileFilters,
  type MediaSource,
  type MediaSourceCreatePayload,
  type MediaSourceMutationResult,
  type MediaSourceUpdatePayload,
  type ScanJob,
  type ScanJobFilters,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const useMediaStore = defineStore("media", {
  state: () => ({
    mediaSources: [] as MediaSource[],
    scanJobs: [] as ScanJob[],
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

    async startScan(mediaSourceId: number) {
      this.loading = true;
      this.errorMessage = "";
      try {
        await createScanJob(mediaSourceId);
        await Promise.all([
          this.loadScanJobs({ media_source_id: mediaSourceId }),
          this.loadLogs(),
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

    async openLogDrawer() {
      await this.loadLogs();
      this.logDrawerVisible = true;
    },
  },
});
