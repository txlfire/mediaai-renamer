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
  fetchLogs,
  fetchMediaFiles,
  fetchMediaSources,
  fetchScanJobs,
  getHealth,
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
            version: "0.1.0",
            status: "ok",
          } as T,
        };
      },
    };

    await expect(getHealth(httpClient)).resolves.toEqual({
      app: "MediaAI Renamer",
      version: "0.1.0",
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

    expect(calls).toEqual([
      "GET /media-sources",
      'POST /media-sources:{"name":"电影","path":"D:/media","enabled":true}',
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
