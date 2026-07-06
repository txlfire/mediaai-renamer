import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { parseRenamePreviewsWithAi } from "../api/client";
import { usePreviewStore } from "./preview";

vi.mock("../api/client", () => ({
  fetchRenamePreviews: vi.fn(async () => [
    {
      id: 1,
      media_file_id: 1,
      file_path: "D:/media/The.Matrix.1999.1080p.mkv",
      file_name: "The.Matrix.1999.1080p.mkv",
      media_type: "movie",
      parsed_title: "The Matrix",
      parsed_year: 1999,
      season: null,
      episode: null,
      suggested_name: "The.Matrix.1999.mkv",
      edited_name: null,
      current_target_name: "The.Matrix.1999.mkv",
      status: "generated",
      message: null,
      updated_at: "2026-06-24T00:00:00Z",
    },
    {
      id: 2,
      media_file_id: 2,
      file_path: "D:/media/Loose.Title.mkv",
      file_name: "Loose.Title.mkv",
      media_type: "unknown",
      parsed_title: "Loose Title",
      parsed_year: null,
      season: null,
      episode: null,
      suggested_name: "Loose.Title.mkv",
      edited_name: null,
      current_target_name: "Loose.Title.mkv",
      status: "needs_review",
      message: "缺少年份或季集信息",
      updated_at: "2026-06-24T00:00:00Z",
    },
    {
      id: 3,
      media_file_id: 3,
      file_path: "D:/media/Renamed.Movie.mkv",
      file_name: "Renamed.Movie.mkv",
      media_type: "movie",
      parsed_title: "Renamed Movie",
      parsed_year: null,
      season: null,
      episode: null,
      suggested_name: "Renamed.Movie.mkv",
      edited_name: null,
      current_target_name: "Renamed.Movie.mkv",
      status: "renamed",
      message: null,
      updated_at: "2026-06-24T00:00:00Z",
    },
  ]),
  generateRenamePreviews: vi.fn(async () => ({
    generated_count: 2,
    needs_review_count: 1,
    edited_kept_count: 0,
  })),
  updateRenamePreview: vi.fn(async () => ({
    id: 1,
    current_target_name: "Matrix.Custom.mkv",
    status: "edited",
  })),
  parseRenamePreviewsWithAi: vi.fn(async () => ({
    total_count: 2,
    success_count: 1,
    failed_count: 0,
    blocked_count: 1,
    skipped_count: 0,
    usage: { total_tokens: 10 },
    items: [
      {
        id: 1,
        result: {
          status: "success",
          message: "ok",
          candidates: [
            {
              title: "黑客帝国",
              media_type: "movie",
              year: 1999,
              season: null,
              episode: null,
              confidence: 90,
              reason: "AI 识别到中文标题和年份",
              raw_data: { source: "ai" },
            },
          ],
          usage: { total_tokens: 10 },
        },
      },
    ],
    failed_items: [],
  })),
}));

describe("preview store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("loads previews and computes status stats", async () => {
    const store = usePreviewStore();

    await store.loadPreviews();

    expect(store.previews).toHaveLength(3);
    expect(store.stats.total).toBe(3);
    expect(store.stats.generated).toBe(1);
    expect(store.stats.needsReview).toBe(1);
    expect(store.stats.edited).toBe(0);
    expect(store.stats.renamed).toBe(1);
  });

  it("generates previews then refreshes list", async () => {
    const store = usePreviewStore();

    await store.generatePreviews();

    expect(store.generationSummary?.generated_count).toBe(2);
    expect(store.previews).toHaveLength(3);
  });

  it("runs batch AI parse through the backend client", async () => {
    const store = usePreviewStore();

    const result = await store.parseBatchWithAi([1, 2]);

    expect(parseRenamePreviewsWithAi).toHaveBeenCalledWith([1, 2]);
    expect(result.total_count).toBe(2);
    expect(result.success_count).toBe(1);
    expect(result.blocked_count).toBe(1);
    expect(store.loading).toBe(false);
  });
});
