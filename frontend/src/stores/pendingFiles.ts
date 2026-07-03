import { defineStore } from "pinia";

import {
  clearPendingFiles,
  fetchPendingFiles,
  movePendingFiles,
  parsePendingFileWithAi,
  removePendingFile,
  type AiParseResult,
  type PendingFile,
  type PendingFileFilters,
} from "../api/client";

export const usePendingFileStore = defineStore("pendingFiles", {
  state: () => ({
    pendingFiles: [] as PendingFile[],
    filters: {} as PendingFileFilters,
    loading: false,
  }),
  actions: {
    async loadPendingFiles(filters?: PendingFileFilters) {
      this.filters = { ...(filters ?? this.filters) };
      this.pendingFiles = await fetchPendingFiles(this.filters);
    },

    async removePendingFile(id: number) {
      await removePendingFile(id);
      this.pendingFiles = this.pendingFiles.filter((item) => item.id !== id);
    },

    async clearPendingFiles(filters?: PendingFileFilters) {
      await clearPendingFiles(filters ?? this.filters);
      this.pendingFiles = [];
    },

    async movePendingFiles(ids: number[], targetDirectory: string) {
      await movePendingFiles(ids, targetDirectory);
      this.pendingFiles = this.pendingFiles.filter((item) => !ids.includes(item.id));
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
