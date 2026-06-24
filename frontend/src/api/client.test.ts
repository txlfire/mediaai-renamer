/**
 * API 客户端测试。
 *
 * 锁定前端必须通过 Axios 相对路径访问后端，避免后续误改成硬编码地址。
 */

import { describe, expect, it } from "vitest";

import {
  apiClient,
  createMediaSource,
  createScanJob,
  fetchLocalDirectories,
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchRenamePreviews,
  fetchScanJobs,
  generateRenamePreviews,
  getHealth,
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
            version: "0.2.0",
            status: "ok",
          } as T,
        };
      },
    };

    await expect(getHealth(httpClient)).resolves.toEqual({
      app: "MediaAI Renamer",
      version: "0.2.0",
      status: "ok",
    });
  });

  it("raises a readable error when the health request fails", async () => {
    const httpClient: ApiHttpClient = {
      get: async () => {
        throw new Error("network down");
      },
    };

    await expect(getHealth(httpClient)).rejects.toThrow("后端健康检查失败");
  });
});

describe("M1 API client", () => {
  it("uses media source endpoints", async () => {
    const calls: string[] = [];
    const httpClient: ApiHttpClient = {
      get: async <T = unknown>(url: string): Promise<{ data: T }> => {
        calls.push(`GET ${url}`);
        return { data: [] as T };
      },
      post: async <T = unknown>(url: string, body: unknown): Promise<{ data: T }> => {
        calls.push(`POST ${url}:${JSON.stringify(body)}`);
        return { data: { id: 1, name: "电影", path: "D:/media", enabled: true } as T };
      },
    };

    await fetchMediaSources(httpClient);
    await createMediaSource({ name: "电影", path: "D:/media", enabled: true }, httpClient);
    await fetchLocalDirectories("D:/media", httpClient);

    expect(calls).toEqual([
      "GET /media-sources",
      'POST /media-sources:{"name":"电影","path":"D:/media","enabled":true}',
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
    await fetchScanJobs(httpClient);
    await fetchMediaFiles(httpClient);
    await fetchLogs(httpClient);

    expect(calls).toEqual([
      'POST /scan-jobs:{"media_source_id":1}',
      "GET /scan-jobs",
      "GET /media-files",
      "GET /logs",
    ]);
  });
});

describe("M2 rename preview API client", () => {
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
