import { defineStore } from "pinia";

import {
  applyRenamePreviewMetadataCandidate,
  fetchRenamePreviewMetadataCandidates,
  fetchRenamePreviews,
  generateRenamePreviews,
  matchRenamePreviewMetadata,
  updateRenamePreview,
  type GenerateRenamePreviewsPayload,
  type MetadataMatchResult,
  type PreviewGenerationSummary,
  type RenamePreview,
  type RenamePreviewFilters,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

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
        renamed: state.previews.filter((preview) => preview.status === "renamed").length,
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
        this.errorMessage = error instanceof Error ? error.message : messages.errors.previewGenerationFailed;
      } finally {
        this.loading = false;
      }
    },

    async updatePreview(previewId: number, targetName: string) {
      const updated = await updateRenamePreview(previewId, targetName);
      this.replacePreview(updated);
      return updated;
    },

    replacePreview(updated: RenamePreview) {
      const index = this.previews.findIndex((preview) => preview.id === updated.id);
      if (index >= 0) {
        this.previews[index] = { ...this.previews[index], ...updated };
      }
    },

    async matchMetadata(previewId: number) {
      this.loading = true;
      this.errorMessage = "";
      try {
        const updated = await matchRenamePreviewMetadata(previewId);
        this.replacePreview(updated);
        return updated;
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async loadMetadataCandidates(previewId: number): Promise<MetadataMatchResult[]> {
      return fetchRenamePreviewMetadataCandidates(previewId);
    },

    async applyMetadataCandidate(previewId: number, match: MetadataMatchResult) {
      const updated = await applyRenamePreviewMetadataCandidate(previewId, match);
      this.replacePreview(updated);
      return updated;
    },
  },
});
