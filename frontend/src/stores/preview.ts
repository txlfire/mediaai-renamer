import { defineStore } from "pinia";

import {
  fetchRenamePreviews,
  generateRenamePreviews,
  updateRenamePreview,
  type GenerateRenamePreviewsPayload,
  type PreviewGenerationSummary,
  type RenamePreview,
  type RenamePreviewFilters,
} from "../api/client";

export const usePreviewStore = defineStore("preview", {
  state: () => ({
    previews: [] as RenamePreview[],
    filters: {} as RenamePreviewFilters,
    generationSummary: null as PreviewGenerationSummary | null,
    loading: false,
    errorMessage: "",
  }),
  getters: {
    stats(state) {
      return {
        total: state.previews.length,
        generated: state.previews.filter((preview) => preview.status === "generated").length,
        needsReview: state.previews.filter((preview) => preview.status === "needs_review").length,
        edited: state.previews.filter((preview) => preview.status === "edited").length,
      };
    },
  },
  actions: {
    async loadPreviews(filters?: RenamePreviewFilters) {
      this.filters = { ...(filters ?? this.filters) };
      this.previews = await fetchRenamePreviews(this.filters);
    },

    async generatePreviews(payload: GenerateRenamePreviewsPayload = {}) {
      this.loading = true;
      this.errorMessage = "";
      try {
        this.generationSummary = await generateRenamePreviews(payload);
        await this.loadPreviews();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : "生成命名预览失败";
      } finally {
        this.loading = false;
      }
    },

    async updatePreview(previewId: number, targetName: string) {
      const updated = await updateRenamePreview(previewId, targetName);
      const index = this.previews.findIndex((preview) => preview.id === previewId);
      if (index >= 0) {
        this.previews[index] = { ...this.previews[index], ...updated };
      }
      return updated;
    },
  },
});
