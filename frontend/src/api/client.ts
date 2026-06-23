/**
 * 后端 API 客户端模块。
 *
 * 统一维护 Axios 实例和基础接口封装，页面和 store 不直接访问裸 Axios。
 */

import axios from "axios";

/**
 * 后端健康检查响应。
 */
export type HealthStatus = {
  app: string;
  version: string;
  status: "ok" | string;
};

/**
 * 当前健康检查接口需要的最小 HTTP 客户端能力。
 *
 * 保持窄接口可以降低测试 mock 的复杂度，后续业务接口可按模块扩展自己的客户端类型。
 */
export type ApiHttpClient = {
  get(url: string): Promise<{ data: HealthStatus }>;
};

// API 使用相对路径，确保 Docker、NAS 反向代理和本地开发环境都能复用同一套前端代码。
export const apiClient = axios.create({
  baseURL: "/api",
  timeout: 15000,
});

/**
 * 获取后端健康状态，用于判断前端是否已经连通 API 服务。
 */
export async function getHealth(httpClient: ApiHttpClient = apiClient): Promise<HealthStatus> {
  try {
    const response = await httpClient.get("/health");

    return response.data;
  } catch (error) {
    // 对外抛出中文错误，方便页面直接展示给用户。
    const reason = error instanceof Error ? error.message : "未知错误";
    throw new Error(`后端健康检查失败：${reason}`);
  }
}
