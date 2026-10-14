import { getSessionGeneration, sessionRequest } from "../utils/sessionGeneration";
import { defineStore } from "pinia";
import { matchRenamePreviewsMultiSourceBackground as matchRenamePreviewsMultiSource, parseRenamePreviewsWithAiBackground as parseRenamePreviewsWithAi } from "../api/backgroundJobs";

import {
  applyRenamePreviewMetadataCandidate,
  applyAiParseCandidate,
  excludeRenamePreview,
  excludeRenamePreviews,
  fetchRenamePreviewMetadataCandidates,
  fetchRenamePreviews,
  generateRenamePreviews,
  matchAllUnmatchedMetadataWithAiFallback,
  matchAllUnmatchedMetadata,
  matchRenamePreviewMultiSource,
  matchRenamePreviewMetadata,
  matchRenamePreviewsMetadataWithAiFallback,
  matchRenamePreviewsMetadata,
  parseRenamePreviewWithAi,
  updateRenamePreview,
  type AiParseCandidate,
  type AiParseResult,
  type BatchAiParseResult,
  type BatchMetadataMatchResult,
  type BatchMultiSourceMatchResult,
  type BatchRenamePreviewExcludeResult,
  type GenerateRenamePreviewsPayload,
  type MetadataMatchSource,
  type MetadataMatchResult,
  type MultiSourceMatchMode,
  type MultiSourceMatchResponse,
  type PreviewGenerationSummary,
  type RenamePreview,
  type RenamePreviewFilters,
  type MetadataAiFallbackResult,
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
        noRename: state.previews.filter((preview) => preview.status === "no_rename").length,
        unableRename: state.previews.filter((preview) => preview.status === "unable_rename").length,
      };
    },
  },
  actions: {
    async loadPreviews(filters?: RenamePreviewFilters) {
      const sessionGeneration = getSessionGeneration();
      this.filters = { ...(filters ?? this.filters) };
      this.previews = await sessionRequest(fetchRenamePreviews(this.filters), sessionGeneration);
    },

    async generatePreviews(payload: GenerateRenamePreviewsPayload = {}, refreshHistory = true) {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      this.generationSummary = null;
      try {
        this.generationSummary = await sessionRequest(generateRenamePreviews(payload), sessionGeneration);
        if (refreshHistory) await sessionRequest(this.loadPreviews(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.previewGenerationFailed;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async updatePreview(previewId: number, targetName: string) {
      const sessionGeneration = getSessionGeneration();
      const updated = await sessionRequest(updateRenamePreview(previewId, targetName), sessionGeneration);
      this.replacePreview(updated);
      return updated;
    },

    async excludePreview(previewId: number, reason = "manual_excluded") {
      const sessionGeneration = getSessionGeneration();
      const updated = await sessionRequest(excludeRenamePreview(previewId, reason), sessionGeneration);
      this.replacePreview(updated);
      return updated;
    },

    async excludePreviews(previewIds: number[], reason = "manual_excluded"): Promise<BatchRenamePreviewExcludeResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(excludeRenamePreviews(previewIds, reason), sessionGeneration);
        result.items.forEach((item) => this.replacePreview(item));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    replacePreview(updated: RenamePreview) {
      const index = this.previews.findIndex((preview) => preview.id === updated.id);
      if (index >= 0) {
        this.previews[index] = { ...this.previews[index], ...updated };
      }
    },

    async matchMetadata(previewId: number, metadataMatchSource: MetadataMatchSource = "parsed_title") {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const updated = await sessionRequest(matchRenamePreviewMetadata(previewId, metadataMatchSource), sessionGeneration);
        this.replacePreview(updated);
        return updated;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchMetadataBatch(
      previewIds: number[],
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<BatchMetadataMatchResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchRenamePreviewsMetadata(previewIds, metadataMatchSource), sessionGeneration);
        result.items.forEach((item) => this.replacePreview(item));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchAllUnmatched(
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<BatchMetadataMatchResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchAllUnmatchedMetadata(
          {
            media_source_id: this.filters.media_source_id,
            scan_job_id: this.filters.scan_job_id,
          },
          metadataMatchSource,
        ), sessionGeneration);
        result.items.forEach((item) => this.replacePreview(item));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchMultiSource(
      previewId: number,
      metadataMatchSource: MetadataMatchSource = "parsed_title",
      mode: MultiSourceMatchMode = "fallback",
    ): Promise<MultiSourceMatchResponse> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchRenamePreviewMultiSource(previewId, metadataMatchSource, mode), sessionGeneration);
        this.replacePreview(result.preview);
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchMultiSourceBatch(
      previewIds: number[],
      metadataMatchSource: MetadataMatchSource = "parsed_title",
      mode: MultiSourceMatchMode = "fallback",
    ): Promise<BatchMultiSourceMatchResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchRenamePreviewsMultiSource(previewIds, metadataMatchSource, mode), sessionGeneration);
        result.items.forEach((item) => this.replacePreview(item.preview));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchMetadataBatchWithAiFallback(
      previewIds: number[],
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<MetadataAiFallbackResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchRenamePreviewsMetadataWithAiFallback(previewIds, metadataMatchSource), sessionGeneration);
        result.metadata.items.forEach((item) => this.replacePreview(item));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async matchAllUnmatchedWithAiFallback(
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<MetadataAiFallbackResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await sessionRequest(matchAllUnmatchedMetadataWithAiFallback(
          {
            media_source_id: this.filters.media_source_id,
            scan_job_id: this.filters.scan_job_id,
          },
          metadataMatchSource,
        ), sessionGeneration);
        result.metadata.items.forEach((item) => this.replacePreview(item));
        return result;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async loadMetadataCandidates(
      previewId: number,
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<MetadataMatchResult[]> {
      const sessionGeneration = getSessionGeneration();
      return fetchRenamePreviewMetadataCandidates(previewId, metadataMatchSource);
    },

    async applyMetadataCandidate(previewId: number, match: MetadataMatchResult, selectedFields: string[] = []) {
      const sessionGeneration = getSessionGeneration();
      const updated = await sessionRequest(applyRenamePreviewMetadataCandidate(previewId, match, selectedFields), sessionGeneration);
      this.replacePreview(updated);
      return updated;
    },

    async parseWithAi(
      previewId: number,
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<AiParseResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(parseRenamePreviewWithAi(previewId, metadataMatchSource), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async parseBatchWithAi(
      previewIds: number[],
      metadataMatchSource: MetadataMatchSource = "parsed_title",
    ): Promise<BatchAiParseResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(parseRenamePreviewsWithAi(previewIds, metadataMatchSource), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.errors.unknown;
        throw error;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async applyAiCandidate(previewId: number, candidate: AiParseCandidate) {
      const sessionGeneration = getSessionGeneration();
      const updated = await sessionRequest(applyAiParseCandidate(previewId, candidate), sessionGeneration);
      this.replacePreview(updated);
      return updated;
    },
  },
});
