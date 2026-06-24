import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
}));

describe("preview store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("loads previews and computes status stats", async () => {
    const store = usePreviewStore();

    await store.loadPreviews();

    expect(store.previews).toHaveLength(2);
    expect(store.stats.total).toBe(2);
    expect(store.stats.generated).toBe(1);
    expect(store.stats.needsReview).toBe(1);
    expect(store.stats.edited).toBe(0);
  });

  it("generates previews then refreshes list", async () => {
    const store = usePreviewStore();

    await store.generatePreviews();

    expect(store.generationSummary?.generated_count).toBe(2);
    expect(store.previews).toHaveLength(2);
  });
});
