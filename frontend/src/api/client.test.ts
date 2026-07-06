import { describe, expect, it } from "vitest";

import {
  apiClient,
  applyAiParseCandidate,
  applyRenamePreviewMetadataCandidate,
  bulkDeleteMediaSources,
  clearPendingFiles,
  createMediaSource,
  createRenameDryRun,
  createScanJob,
  deleteMediaSource,
  executeRenameOperation,
  fetchSettings,
  fetchLocalDirectories,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSourceDirectories,
  fetchMediaSources,
  fetchSharedProtocolCapabilities,
  fetchPendingFiles,
  fetchRenameOperation,
  fetchRenamePreviewMetadataCandidates,
  fetchRenamePreviews,
  fetchScanJobs,
  generateRenamePreviews,
  getHealth,
  matchRenamePreviewMetadata,
  movePendingFiles,
  parseRenamePreviewWithAi,
  parseRenamePreviewsWithAi,
  parsePendingFileWithAi,
  removePendingFile,
  setMediaSourceEnabled,
  testMediaSourceConnection,
  testMediaSourceConnectionPayload,
  testTmdbSettings,
  updateSettings,
  updateMediaSource,
  updateRenamePreview,
  type ApiHttpClient,
} from "./client";

describe("getHealth", () => {
  it("uses the API base path for backend requests", () => {
    expect(apiClient.defaults.baseURL).toBe("/api");
  });

  it("loads the backend health status through Axios", async () => {
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        expect(url).toBe("/health");
        return {
          data: {
            app: "MediaAI Renamer",
            version: "0.5.1",
            status: "ok",
          } as T,
        };
      },
    };

    await expect(getHealth(httpClient)).resolves.toEqual({
      app: "MediaAI Renamer",
      version: "0.5.1",
      status: "ok",
    });
  });

  it("raises a readable error when the health request fails", async () => {
    const httpClient: ApiHttpClient = {
      get: async () => {
        throw new Error("network down");
      },
    };

    await expect(getHealth(httpClient)).rejects.toThrow("network down");
  });
});

describe("media source API client", () => {
  it("uses media source endpoints", async () => {
    const calls: string[] = [];
    const cleanup_summary = {
      rename_operation_items: 0,
      rename_operations: 0,
      rename_previews: 0,
      media_files: 0,
      scan_jobs: 0,
    };
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: [] as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { id: 1, name: "movie", path: "D:/media", enabled: true } as T };
      },
      put: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`PUT ${url}:${JSON.stringify(body)}`);
        return {
          data: {
            source: { id: 1, name: "movie", path: "D:/new", enabled: true },
            cleanup_summary,
          } as T,
        };
      },
      patch: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`PATCH ${url}:${JSON.stringify(body)}`);
        return { data: { id: 1, name: "movie", path: "D:/media", enabled: false } as T };
      },
      delete: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`DELETE ${url}`);
        return { data: { deleted_ids: [1], cleanup_summary } as T };
      },
    };

    await fetchMediaSources(httpClient);
    await createMediaSource(
      {
        name: "movie",
        path: "\\\\nas\\media",
        enabled: true,
        path_type: "unc",
        username: "admin",
        secret: "password",
      },
      httpClient,
    );
    await updateMediaSource(
      1,
      {
        name: "movie",
        path: "D:/new",
        enabled: true,
        clear_history_on_path_change: true,
      },
      httpClient,
    );
    await setMediaSourceEnabled(1, false, httpClient);
    await deleteMediaSource(1, httpClient);
    await bulkDeleteMediaSources([1, 2], httpClient);
    await fetchLocalDirectories("D:/media", httpClient);
    await fetchSharedProtocolCapabilities(httpClient);
    await testMediaSourceConnection(1, httpClient);
    await testMediaSourceConnectionPayload({ path: "D:/new", path_type: "local" }, httpClient);
    await fetchMediaSourceDirectories(1, "D:/media", httpClient);

    expect(calls).toEqual([
      "GET /media-sources",
      'POST /media-sources:{"name":"movie","path":"\\\\\\\\nas\\\\media","enabled":true,"path_type":"unc","username":"admin","secret":"password"}',
      'PUT /media-sources/1:{"name":"movie","path":"D:/new","enabled":true,"clear_history_on_path_change":true}',
      'PATCH /media-sources/1/enabled:{"enabled":false}',
      "DELETE /media-sources/1",
      'POST /media-sources/bulk-delete:{"ids":[1,2]}',
      "GET /media-sources/local-directories?path=D%3A%2Fmedia",
      "GET /shared-protocols",
      "POST /media-sources/1/test-connection:{}",
      'POST /media-sources/test-connection:{"path":"D:/new","path_type":"local"}',
      "GET /media-sources/1/directories?path=D%3A%2Fmedia",
    ]);
  });

  it("uses scan and log endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: (url === "/logs" ? { items: [] } : []) as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { id: 1, status: "completed" } as T };
      },
    };

    await createScanJob(1, httpClient);
    await fetchScanJobs({ media_source_id: 1 }, httpClient);
    await fetchMediaFiles({ media_source_id: 1, scan_job_id: 2 }, httpClient);
    await fetchLogs(httpClient);

    expect(calls).toEqual([
      'POST /scan-jobs:{"media_source_id":1,"scan_mode":"full"}',
      "GET /scan-jobs?media_source_id=1",
      "GET /media-files?media_source_id=1&scan_job_id=2",
      "GET /logs",
    ]);
  });
});

describe("rename preview API client", () => {
  it("uses rename preview endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: [] as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { generated_count: 1, needs_review_count: 0, edited_kept_count: 0 } as T };
      },
      put: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`PUT ${url}:${JSON.stringify(body)}`);
        return { data: { id: 1, current_target_name: "Matrix.Custom.mkv" } as T };
      },
    };

    await generateRenamePreviews({ scan_job_id: 1 }, httpClient);
    await fetchRenamePreviews({ status: "generated", keyword: "Matrix" }, httpClient);
    await updateRenamePreview(1, "Matrix.Custom", httpClient);
    await matchRenamePreviewMetadata(1, "parsed_title", httpClient);
    await fetchRenamePreviewMetadataCandidates(1, "parsed_title", httpClient);
    await applyRenamePreviewMetadataCandidate(
      1,
      {
        score: 91,
        status: "high_confidence",
        candidate: {
          provider: "TMDB",
          provider_id: "603",
          media_type: "movie",
          title: "黑客帝国",
          original_title: "The Matrix",
          year: 1999,
          season: null,
          episode: null,
          overview: "",
        },
      },
      httpClient,
    );
    await parseRenamePreviewWithAi(1, httpClient);
    await parseRenamePreviewsWithAi([1, 2], httpClient);
    await applyAiParseCandidate(
      1,
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
      httpClient,
    );

    expect(calls).toEqual([
      'POST /rename-previews/generate:{"scan_job_id":1}',
      "GET /rename-previews?status=generated&keyword=Matrix",
      'PUT /rename-previews/1:{"target_name":"Matrix.Custom"}',
      "POST /rename-previews/1/metadata-match?metadata_match_source=parsed_title:{}",
      "GET /rename-previews/1/metadata-candidates?metadata_match_source=parsed_title",
      'POST /rename-previews/1/metadata-candidate:{"candidate":{"provider":"TMDB","provider_id":"603","media_type":"movie","title":"黑客帝国","original_title":"The Matrix","year":1999,"season":null,"episode":null,"overview":""},"score":91}',
      "POST /rename-previews/1/ai-parse:{}",
      'POST /rename-previews/ai-parse/batch:{"rename_preview_ids":[1,2]}',
      'POST /rename-previews/1/ai-candidate:{"candidate":{"title":"黑客帝国","media_type":"movie","year":1999,"season":null,"episode":null,"confidence":90,"reason":"AI 识别到中文标题和年份","raw_data":{"source":"ai"}}}',
    ]);
  });
});

describe("rename operation API client", () => {
  it("uses rename operation endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: { id: 10, items: [] } as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { id: 10, items: [] } as T };
      },
    };

    await createRenameDryRun([1, 2], httpClient);
    await fetchRenameOperation(10, httpClient);
    await executeRenameOperation(10, httpClient);

    expect(calls).toEqual([
      'POST /rename-operations/dry-run:{"rename_preview_ids":[1,2]}',
      "GET /rename-operations/10",
      "POST /rename-operations/10/execute:{}",
    ]);
  });
});

describe("settings API client", () => {
  it("uses settings endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: [] as T };
      },
      put: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`PUT ${url}:${JSON.stringify(body)}`);
        return { data: [] as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { success: true, message: "连接成功！信息有效！" } as T };
      },
    };

    await fetchSettings(httpClient);
    await updateSettings({ "tmdb.timeout_ms": 12000 }, httpClient);
    await updateSettings({ "privacy.custom_sensitive_words": ["自定义"] }, httpClient);
    await testTmdbSettings(httpClient);

    expect(calls).toEqual([
      "GET /settings",
      'PUT /settings:{"values":{"tmdb.timeout_ms":12000}}',
      'PUT /settings:{"values":{"privacy.custom_sensitive_words":["自定义"]}}',
      "POST /settings/tmdb/test:{}",
    ]);
  });

  it("uses backend Chinese detail for TMDB connection failures", async () => {
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(): Promise<{ data: T }> => ({ data: [] as T }),
      post: async () => {
        throw {
          response: {
            data: {
              detail: "TMDB 连接失败：未配置 API Key。请先填写并保存 TMDB API Key。",
            },
          },
        };
      },
    };

    await expect(testTmdbSettings(httpClient)).rejects.toThrow(
      "TMDB 连接失败：未配置 API Key。请先填写并保存 TMDB API Key。",
    );
  });
});

describe("pending file API client", () => {
  it("uses pending file endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: [] as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: (url.startsWith("/pending-files/clear") ? { removed_count: 2 } : []) as T };
      },
      delete: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`DELETE ${url}`);
        return { data: { id: 1 } as T };
      },
    };

    await fetchPendingFiles({ scan_job_id: 2 }, httpClient);
    await removePendingFile(1, httpClient);
    await parsePendingFileWithAi(1, httpClient);
    await clearPendingFiles({ scan_job_id: 2 }, httpClient);
    await movePendingFiles([1, 2], "D:/pending", httpClient);

    expect(calls).toEqual([
      "GET /pending-files?scan_job_id=2",
      "DELETE /pending-files/1",
      "POST /pending-files/1/ai-parse:{}",
      "POST /pending-files/clear?scan_job_id=2:{}",
      'POST /pending-files/move:{"ids":[1,2],"target_directory":"D:/pending"}',
    ]);
  });
});
