import { describe, expect, it } from "vitest";

import {
  apiClient,
  bulkDeleteMediaSources,
  createMediaSource,
  createRenameDryRun,
  createScanJob,
  deleteMediaSource,
  executeRenameOperation,
  fetchLocalDirectories,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchRenameOperation,
  fetchRenamePreviews,
  fetchScanJobs,
  generateRenamePreviews,
  getHealth,
  setMediaSourceEnabled,
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
            version: "0.3.0",
            status: "ok",
          } as T,
        };
      },
    };

    await expect(getHealth(httpClient)).resolves.toEqual({
      app: "MediaAI Renamer",
      version: "0.3.0",
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
    await createMediaSource({ name: "movie", path: "D:/media", enabled: true }, httpClient);
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

    expect(calls).toEqual([
      "GET /media-sources",
      'POST /media-sources:{"name":"movie","path":"D:/media","enabled":true}',
      'PUT /media-sources/1:{"name":"movie","path":"D:/new","enabled":true,"clear_history_on_path_change":true}',
      'PATCH /media-sources/1/enabled:{"enabled":false}',
      "DELETE /media-sources/1",
      'POST /media-sources/bulk-delete:{"ids":[1,2]}',
      "GET /media-sources/local-directories?path=D%3A%2Fmedia",
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
      'POST /scan-jobs:{"media_source_id":1}',
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

    expect(calls).toEqual([
      'POST /rename-previews/generate:{"scan_job_id":1}',
      "GET /rename-previews?status=generated&keyword=Matrix",
      'PUT /rename-previews/1:{"target_name":"Matrix.Custom"}',
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
