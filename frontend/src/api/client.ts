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
  put?<T = unknown>(url: string, body: unknown): Promise<{ data: T }>;
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

export type LocalDirectoryEntry = {
  name: string;
  path: string;
  is_directory: boolean;
};

export type LocalDirectoryListing = {
  current_path: string | null;
  parent_path: string | null;
  entries: LocalDirectoryEntry[];
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

export type RenamePreview = {
  id: number;
  media_file_id: number;
  file_path: string;
  file_name: string;
  media_type: "movie" | "episode" | "unknown" | string;
  parsed_title: string;
  parsed_year: number | null;
  season: number | null;
  episode: number | null;
  suggested_name: string;
  edited_name: string | null;
  current_target_name: string;
  status: "generated" | "edited" | "needs_review" | string;
  message: string | null;
  updated_at: string;
};

export type RenamePreviewFilters = {
  media_source_id?: number;
  scan_job_id?: number;
  status?: string;
  media_type?: string;
  keyword?: string;
};

export type GenerateRenamePreviewsPayload = {
  media_source_id?: number;
  scan_job_id?: number;
  media_file_ids?: number[];
  overwrite_edited?: boolean;
};

export type PreviewGenerationSummary = {
  generated_count: number;
  needs_review_count: number;
  edited_kept_count: number;
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

function requirePut(httpClient: ApiHttpClient) {
  if (!httpClient.put) {
    throw new Error("HTTP 瀹㈡埛绔己灏?PUT 鑳藉姏");
  }
  return httpClient.put.bind(httpClient);
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

export async function fetchLocalDirectories(
  path = "",
  httpClient: ApiHttpClient = apiClient,
): Promise<LocalDirectoryListing> {
  const params = new URLSearchParams();
  if (path.trim()) {
    params.set("path", path);
  }
  const query = params.toString();
  const response = await httpClient.get<LocalDirectoryListing>(
    query ? `/media-sources/local-directories?${query}` : "/media-sources/local-directories",
  );
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

export async function generateRenamePreviews(
  payload: GenerateRenamePreviewsPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<PreviewGenerationSummary> {
  const post = requirePost(httpClient);
  const response = await post<PreviewGenerationSummary>("/rename-previews/generate", payload);
  return response.data;
}

export async function fetchRenamePreviews(
  filters: RenamePreviewFilters = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview[]> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const query = params.toString();
  const response = await httpClient.get<RenamePreview[]>(
    query ? `/rename-previews?${query}` : "/rename-previews",
  );
  return response.data;
}

export async function updateRenamePreview(
  previewId: number,
  targetName: string,
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview> {
  const put = requirePut(httpClient);
  const response = await put<RenamePreview>(`/rename-previews/${previewId}`, {
    target_name: targetName,
  });
  return response.data;
}
