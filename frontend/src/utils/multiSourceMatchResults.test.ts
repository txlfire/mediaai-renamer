import { describe, expect, it } from "vitest";

import type { BatchMultiSourceMatchResult } from "../api/client";
import { buildMultiSourceMatchRows } from "./multiSourceMatchResults";

describe("多源匹配结果汇总", () => {
  it("保留预览、候选数量和来源执行结果，供用户继续查看候选", () => {
    const result = {
      total_count: 1,
      success_count: 1,
      failed_count: 0,
      blocked_count: 0,
      skipped_count: 0,
      provider_success_count: 2,
      provider_failed_count: 0,
      provider_skipped_count: 0,
      failed_items: [],
      items: [
        {
          preview: {
            id: 8,
            file_name: "葬送的芙莉莲.S01E01.mkv",
            current_target_name: "葬送的芙莉莲.S01E01.mkv",
            metadata_match_status: "low_confidence",
            metadata_candidate_count: 3,
          },
          provider_results: [
            {
              provider: "tmdb",
              label: "TMDB",
              status: "success",
              message: "找到候选",
              candidate_count: 1,
            },
            {
              provider: "bangumi",
              label: "Bangumi",
              status: "success",
              message: "找到候选",
              candidate_count: 2,
            },
          ],
        },
      ],
    } as unknown as BatchMultiSourceMatchResult;

    const rows = buildMultiSourceMatchRows(result);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      previewId: 8,
      fileName: "葬送的芙莉莲.S01E01.mkv",
      candidateCount: 3,
      canViewCandidates: true,
    });
    expect(rows[0].providerResults.map((item) => item.label)).toEqual(["TMDB", "Bangumi"]);
  });
});
