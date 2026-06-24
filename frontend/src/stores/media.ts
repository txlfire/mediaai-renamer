/**
 * 媒体扫描状态。
 *
 * 保存 M1 阶段媒体源、扫描任务、扫描结果和日志抽屉状态。
 */

import { defineStore } from "pinia";

import {
  createMediaSource,
  createScanJob,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchScanJobs,
  type LogItem,
  type MediaFile,
  type MediaFileFilters,
  type MediaSource,
  type MediaSourceCreatePayload,
  type ScanJob,
  type ScanJobFilters,
} from "../api/client";

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
        this.errorMessage = error instanceof Error ? error.message : "扫描任务失败";
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
