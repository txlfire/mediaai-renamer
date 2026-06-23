/**
 * API 客户端测试。
 *
 * 锁定前端必须通过 Axios 相对路径访问后端，避免后续误改成硬编码地址。
 */

import { describe, expect, it } from "vitest";

import { apiClient, getHealth, type ApiHttpClient } from "./client";

describe("getHealth", () => {
  it("uses the API base path for backend requests", () => {
    expect(apiClient.defaults.baseURL).toBe("/api");
  });

  it("loads the backend health status through Axios", async () => {
    const httpClient: ApiHttpClient = {
      get: async (url: string) => {
        expect(url).toBe("/health");

        return {
          data: {
            app: "MediaAI Renamer",
            version: "0.1.0",
            status: "ok",
          },
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
