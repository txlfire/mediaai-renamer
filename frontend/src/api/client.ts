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
  get<T = unknown>(url: string): Promise<{ data: T }>;
  post?<T = unknown>(url: string, body: unknown): Promise<{ data: T }>;
};

export type MediaSource = {
  id: number;
  name: string;
  path: string;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
};

export type MediaSourceCreatePayload = {
  name: string;
  path: string;
  enabled: boolean;
};

export type ScanJob = {
  id: number;
  media_source_id: number;
  status: string;
  batch_size?: number;
  batch_interval_seconds?: number;
  scanned_count?: number;
  video_count?: number;
  warning_count?: number;
  error_message?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
};

export type MediaFile = {
  id: number;
  media_source_id: number;
  scan_job_id: number;
  file_path: string;
  file_name: string;
  extension: string;
  file_size: number;
  modified_at: string;
};

export type LogItem = {
  file: string;
  message: string;
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
    const response = await httpClient.get<HealthStatus>("/health");

    return response.data;
  } catch (error) {
    // 对外抛出中文错误，方便页面直接展示给用户。
    const reason = error instanceof Error ? error.message : "未知错误";
    throw new Error(`后端健康检查失败：${reason}`);
  }
}

function requirePost(httpClient: ApiHttpClient) {
  if (!httpClient.post) {
    throw new Error("HTTP 客户端缺少 POST 能力");
  }
  return httpClient.post.bind(httpClient);
}

export async function fetchMediaSources(
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSource[]> {
  const response = await httpClient.get<MediaSource[]>("/media-sources");
  return response.data;
}

export async function createMediaSource(
  payload: MediaSourceCreatePayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSource> {
  const post = requirePost(httpClient);
  const response = await post<MediaSource>("/media-sources", payload);
  return response.data;
}

export async function createScanJob(
  mediaSourceId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<ScanJob> {
  const post = requirePost(httpClient);
  const response = await post<ScanJob>("/scan-jobs", { media_source_id: mediaSourceId });
  return response.data;
}

export async function fetchScanJobs(httpClient: ApiHttpClient = apiClient): Promise<ScanJob[]> {
  const response = await httpClient.get<ScanJob[]>("/scan-jobs");
  return response.data;
}

export async function fetchMediaFiles(httpClient: ApiHttpClient = apiClient): Promise<MediaFile[]> {
  const response = await httpClient.get<MediaFile[]>("/media-files");
  return response.data;
}

export async function fetchLogs(httpClient: ApiHttpClient = apiClient): Promise<LogItem[]> {
  const response = await httpClient.get<{ items: LogItem[] }>("/logs");
  return response.data.items;
}
