import axios from "axios";

import { zhCnMessages as messages } from "../locales/zh-CN";

export type HealthStatus = {
  app: string;
  version: string;
  status: "ok" | string;
};

export type AuthUser = {
  id: number;
  username: string;
  displayName: string;
  enabled: boolean;
  permissions: string[];
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type BootstrapAdminPayload = {
  username: string;
  displayName: string;
  password: string;
};

export type LoginPayload = {
  username: string;
  password: string;
};

export type LoginResult = {
  accessToken: string;
  tokenType: "bearer" | string;
  expiresAt: string;
  user: AuthUser;
};

export type ApiHttpClient = {
  get<T = unknown>(url: string): Promise<{ data: T }>;
  post?<T = unknown>(url: string, body: unknown): Promise<{ data: T }>;
  put?<T = unknown>(url: string, body: unknown): Promise<{ data: T }>;
  patch?<T = unknown>(url: string, body: unknown): Promise<{ data: T }>;
  delete?<T = unknown>(url: string): Promise<{ data: T }>;
};

export type MediaSource = {
  id: number;
  name: string;
  path: string;
  path_type: "local" | "unc" | "mounted_nfs" | string;
  protocol: string;
  host?: string | null;
  share_name?: string | null;
  domain?: string | null;
  username?: string | null;
  has_secret?: boolean;
  port?: number | null;
  remark?: string | null;
  nfs_host?: string | null;
  nfs_export?: string | null;
  nfs_version?: string | null;
  nfs_options?: string | null;
  local_mount_path?: string | null;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
};

export type MediaSourceCreatePayload = {
  name: string;
  path: string;
  enabled: boolean;
  path_type?: "local" | "unc" | "mounted_nfs";
  host?: string | null;
  share_name?: string | null;
  domain?: string | null;
  username?: string | null;
  secret?: string | null;
  port?: number | null;
  remark?: string | null;
  nfs_host?: string | null;
  nfs_export?: string | null;
  nfs_version?: string | null;
  nfs_options?: string | null;
  local_mount_path?: string | null;
};

export type CleanupSummary = {
  rename_operation_items: number;
  rename_operations: number;
  rename_previews: number;
  media_files: number;
  scan_jobs: number;
};

export type MediaSourceUpdatePayload = MediaSourceCreatePayload & {
  clear_history_on_path_change?: boolean;
};

export type MediaSourceMutationResult = {
  source?: MediaSource;
  deleted_ids?: number[];
  cleanup_summary: CleanupSummary;
};

export type LocalDirectoryEntry = {
  name: string;
  path: string;
  is_directory: boolean;
  readable?: boolean | null;
  writable?: boolean | null;
};

export type LocalDirectoryListing = {
  current_path: string | null;
  parent_path: string | null;
  entries: LocalDirectoryEntry[];
};

export type SharedProtocolCapability = {
  protocol: string;
  display_name: string;
  supports_credentials: boolean;
  supports_directory_browse: boolean;
  supports_scan: boolean;
  supports_rename: boolean;
  requires_system_mount: boolean;
  can_verify_filesystem_type: boolean;
  future_candidate: boolean;
  user_notice: string;
};

export type ConnectionTestResult = {
  success: boolean;
  message: string;
  suggestion?: string | null;
  readable?: boolean | null;
  writable?: boolean | null;
  filesystem_type?: string | null;
};

export type MediaSourceConnectionTestPayload = {
  path: string;
  path_type?: "local" | "unc" | "mounted_nfs";
  host?: string | null;
  share_name?: string | null;
  domain?: string | null;
  username?: string | null;
  secret?: string | null;
  port?: number | null;
  nfs_host?: string | null;
  nfs_export?: string | null;
  nfs_version?: string | null;
  nfs_options?: string | null;
  local_mount_path?: string | null;
};

export type ScanJob = {
  id: number;
  media_source_id: number;
  status: string;
  scan_mode?: "full" | "incremental" | string;
  batch_size?: number;
  batch_interval_seconds?: number;
  scanned_count?: number;
  video_count?: number;
  warning_count?: number;
  new_count?: number;
  changed_count?: number;
  skipped_count?: number;
  missing_count?: number;
  indexed_count?: number;
  error_message?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
};

export type ScanMode = "full" | "incremental";

export type ScanModeSuggestion = {
  media_source_id: number;
  recommended_mode: ScanMode;
  has_index: boolean;
  indexed_count: number;
  last_scan_status: string | null;
  reason: string;
};

export type ScanJobFilters = {
  media_source_id?: number;
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

export type LogCleanupSummary = {
  archived_count: number;
  deleted_count: number;
  skipped_count: number;
  archive_dir: string;
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
  metadata_source: string | null;
  metadata_match_status: string | null;
  metadata_match_score: number;
  metadata_message: string | null;
  metadata_candidate_count: number;
  title_source?: string;
  parent_folder_title?: string | null;
  recognition_mode?: string;
  title_conflict_message?: string | null;
  naming_template_type?: "movie" | "episode" | string | null;
  naming_template_version?: number | null;
  naming_template_updated_at?: string | null;
  current_naming_template_version?: number | null;
  is_naming_template_outdated?: boolean;
  naming_template_status?: "current" | "outdated" | "unknown" | string;
  message: string | null;
  updated_at: string;
};

export type MetadataCandidate = {
  provider: string;
  provider_id: string;
  media_type: string;
  title: string;
  original_title: string;
  year: number | null;
  season: number | null;
  episode: number | null;
  overview: string;
  localized_title?: string;
  chinese_title?: string;
  english_title?: string;
  release_date?: string;
  vote_average?: number | null;
  poster_path?: string;
  original_language?: string;
  genres?: string[];
  cast?: string[];
  directors?: string[];
  tmdb_id?: string;
  imdb_id?: string;
  raw_data?: Record<string, unknown>;
};

export type MetadataMatchResult = {
  candidate: MetadataCandidate;
  score: number;
  status: string;
};

export type MetadataMatchSource = "parsed_title" | "original_file_name" | "parent_folder_title";

export type AiParseCandidate = {
  title: string;
  media_type: "movie" | "tv" | "unknown" | string;
  year: number | null;
  season: number | null;
  episode: number | null;
  confidence: number;
  reason: string;
  raw_data: Record<string, unknown>;
};

export type AiParseResult = {
  status: "success" | "failed" | "blocked" | string;
  message: string;
  candidates: AiParseCandidate[];
  response_ms?: number | null;
  usage?: Record<string, unknown>;
};

export type BatchAiParseResult = {
  total_count: number;
  success_count: number;
  failed_count: number;
  blocked_count: number;
  skipped_count: number;
  usage: Record<string, unknown>;
  items: Array<{ id: number; result: AiParseResult }>;
  failed_items: Array<{ id: number; message: string }>;
};

export type MetadataAiFallbackResult = {
  total_count: number;
  fallback_count: number;
  metadata: BatchMetadataMatchResult;
  ai: BatchAiParseResult;
};

export type BatchMetadataMatchResult = {
  total_count: number;
  success_count: number;
  failed_count: number;
  items: RenamePreview[];
  failed_items: Array<{ id: number; message: string }>;
};

export type BatchRenamePreviewExcludeResult = BatchMetadataMatchResult;

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

export type MediaFileFilters = {
  media_source_id?: number;
  scan_job_id?: number;
};

export type RenameOperationItem = {
  id: number;
  operation_id: number;
  rename_preview_id: number;
  source_path: string;
  target_path: string;
  status: "ready" | "conflict" | "renamed" | "failed" | string;
  message: string | null;
  created_at?: string;
  updated_at?: string;
};

export type RenameOperation = {
  id: number;
  status: "dry_run" | "completed" | "partial_failed" | "failed" | string;
  mode: string;
  total_count: number;
  ready_count: number;
  conflict_count: number;
  renamed_count: number;
  failed_count: number;
  created_at?: string;
  updated_at?: string;
  items: RenameOperationItem[];
};

export type SystemSetting = {
  key: string;
  category: string;
  value: SystemSettingValue;
  description: string;
  sensitive: boolean;
  source: string;
  updated_at: string | null;
};

export type TmdbChannelTestResult = {
  status: string;
  message: string;
  response_ms?: number | null;
  error_type?: "network" | "server" | "timeout" | "client" | "unknown" | string;
  http_status?: number | null;
  raw_error?: string | null;
};

export type TmdbConnectionTestResult = {
  v4: TmdbChannelTestResult;
  v3: TmdbChannelTestResult;
  effective: string;
  tested_at?: string;
  config_snapshot?: Record<string, unknown>;
  config_hash?: string;
};

export type TmdbStoredConnectionTestResult = TmdbConnectionTestResult & {
  page_key: string;
  config_snapshot: Record<string, unknown>;
  config_hash: string;
  tested_at: string;
  updated_at: string;
};

export type TmdbConnectionTestHistory = {
  result: TmdbStoredConnectionTestResult | null;
  current_snapshot: Record<string, unknown>;
  matches_current: boolean;
};

export type ImdbConnectionTestResult = {
  status: "success" | "failed" | string;
  message: string;
  response_ms?: number | null;
  error_message?: string | null;
  tested_at?: string;
  config_snapshot?: Record<string, unknown>;
  is_valid?: boolean;
};

export type ImdbStoredConnectionTestResult = {
  id: number;
  connection_status: "success" | "failed" | string;
  response_time: number | null;
  error_message: string | null;
  config_snapshot: Record<string, unknown>;
  test_time: string;
  is_valid: boolean;
};

export type ImdbConnectionTestHistory = {
  result: ImdbStoredConnectionTestResult | null;
  current_snapshot: Record<string, unknown>;
  matches_current: boolean;
};

export type AiConnectionTestResult = {
  status: "success" | "failed" | string;
  message: string;
  response_ms?: number | null;
  error_type?: "network" | "server" | "timeout" | "client" | "unknown" | string | null;
  http_status?: number | null;
  raw_error?: string | null;
  provider: string;
  model: string;
  base_url?: string;
  active_profile_id?: string;
  active_profile_name?: string;
  effective: string;
  tested_at?: string;
  updated_at?: string;
  config_snapshot?: Record<string, unknown>;
  config_hash?: string;
};

export type AiStoredConnectionTestResult = AiConnectionTestResult & {
  page_key: string;
  config_snapshot: Record<string, unknown>;
  config_hash: string;
  tested_at: string;
  updated_at: string;
};

export type AiConnectionTestHistory = {
  result: AiStoredConnectionTestResult | null;
  current_snapshot: Record<string, unknown>;
  matches_current: boolean;
};

export type NamingTemplateSamplePayload = {
  title: string;
  year?: number | null;
  season?: number | null;
  episode?: number | null;
  extension?: string | null;
  extra?: Record<string, unknown>;
};

export type NamingTemplatePreviewPayload = {
  media_type: "movie" | "episode";
  template?: string;
  separator?: string;
  keep_year?: boolean;
  sample: NamingTemplateSamplePayload;
};

export type NamingTemplatePreviewResult = {
  media_type: "movie" | "episode";
  generated_name: string;
  template_version: number;
  template_updated_at: string;
  field_hits: Record<string, boolean>;
  warnings: string[];
};

export type NamingTemplateDiffResult = {
  media_type: "movie" | "episode";
  current_generated_name: string;
  candidate_generated_name: string;
  changed: boolean;
  template_version: number;
  template_updated_at: string;
};

export type NamingTemplateBundle = {
  schema_version: number;
  movie_template: string;
  episode_template: string;
  separator: string;
  keep_year: boolean;
};

export type AiProviderProfile = {
  id: string;
  name: string;
  provider: "deepseek" | "openai_compatible" | "custom" | string;
  model: string;
  api_key?: string;
  has_secret?: boolean;
  base_url: string;
  timeout_ms: number;
  max_retries: number;
  enabled: boolean;
};

export type SystemSettingValue =
  | string
  | number
  | boolean
  | string[]
  | AiProviderProfile[];

export type ExternalSubmissionBlockStatus =
  | "blocked"
  | "renamed"
  | "ignored"
  | "override_submitted"
  | "archived"
  | string;

export type ExternalSubmissionBlockRecord = {
  id: number;
  source_module: string;
  source_record_id: number;
  file_name: string;
  file_path: string;
  match_title: string | null;
  target_service: string;
  block_rule_type: string;
  block_rule_name: string;
  matched_value_masked: string;
  status: ExternalSubmissionBlockStatus;
  user_decision: string | null;
  override_reason: string | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  operator: string | null;
};

export type ExternalSubmissionBlockList = {
  items: ExternalSubmissionBlockRecord[];
  total: number;
};

export type UpdateExternalSubmissionBlockPayload = {
  status: ExternalSubmissionBlockStatus;
  user_decision?: string;
  override_reason?: string;
};

export type PendingFile = {
  id: number;
  media_source_id: number;
  scan_job_id: number;
  file_path: string;
  file_name: string;
  extension: string;
  file_size: number;
  reason: string;
  status: string;
  created_at: string;
};

export type PendingFileFilters = {
  media_source_id?: number;
  scan_job_id?: number;
};

export const apiClient = axios.create({
  baseURL: "/api",
  timeout: 15000,
});

const AUTH_TOKEN_STORAGE_KEY = "mediaai-auth-token";

function canUseLocalStorage() {
  return typeof globalThis.localStorage !== "undefined";
}

export function getAuthToken(): string | null {
  return canUseLocalStorage() ? globalThis.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) : null;
}

export function setAuthToken(token: string) {
  if (canUseLocalStorage()) {
    globalThis.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  }
}

export function clearAuthToken() {
  if (canUseLocalStorage()) {
    globalThis.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  }
}

apiClient.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearAuthToken();
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("mediaai:auth-expired"));
      }
    }
    return Promise.reject(error);
  },
);

export async function bootstrapAdmin(
  payload: BootstrapAdminPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<AuthUser> {
  const post = requirePost(httpClient);
  try {
    const response = await post<AuthUser>("/auth/bootstrap-admin", payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function login(
  payload: LoginPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<LoginResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<LoginResult>("/auth/login", payload);
    setAuthToken(response.data.accessToken);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function logout(httpClient: ApiHttpClient = apiClient): Promise<void> {
  const post = requirePost(httpClient);
  try {
    await post("/auth/logout", {});
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  } finally {
    clearAuthToken();
  }
}

export async function fetchCurrentUser(httpClient: ApiHttpClient = apiClient): Promise<AuthUser> {
  try {
    const response = await httpClient.get<AuthUser>("/auth/me");
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function getHealth(httpClient: ApiHttpClient = apiClient): Promise<HealthStatus> {
  try {
    const response = await httpClient.get<HealthStatus>("/health");
    return response.data;
  } catch (error) {
    const reason = error instanceof Error ? error.message : messages.errors.unknown;
    throw new Error(`${messages.errors.healthCheckFailed}: ${reason}`);
  }
}

function requirePost(httpClient: ApiHttpClient) {
  if (!httpClient.post) {
    throw new Error(messages.errors.missingPost);
  }
  return httpClient.post.bind(httpClient);
}

function requirePut(httpClient: ApiHttpClient) {
  if (!httpClient.put) {
    throw new Error(messages.errors.missingPut);
  }
  return httpClient.put.bind(httpClient);
}

function requirePatch(httpClient: ApiHttpClient) {
  if (!httpClient.patch) {
    throw new Error(messages.errors.missingPatch);
  }
  return httpClient.patch.bind(httpClient);
}

function requireDelete(httpClient: ApiHttpClient) {
  if (!httpClient.delete) {
    throw new Error(messages.errors.missingDelete);
  }
  return httpClient.delete.bind(httpClient);
}

function apiErrorMessage(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string" && response.data.detail.trim()) {
      return response.data.detail;
    }
  }
  return error instanceof Error ? error.message : messages.errors.unknown;
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
  try {
    const response = await post<MediaSource>("/media-sources", payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function updateMediaSource(
  sourceId: number,
  payload: MediaSourceUpdatePayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSourceMutationResult> {
  const put = requirePut(httpClient);
  try {
    const response = await put<MediaSourceMutationResult>(`/media-sources/${sourceId}`, payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function setMediaSourceEnabled(
  sourceId: number,
  enabled: boolean,
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSource> {
  const patch = requirePatch(httpClient);
  try {
    const response = await patch<MediaSource>(`/media-sources/${sourceId}/enabled`, { enabled });
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function deleteMediaSource(
  sourceId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSourceMutationResult> {
  const remove = requireDelete(httpClient);
  try {
    const response = await remove<MediaSourceMutationResult>(`/media-sources/${sourceId}`);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function bulkDeleteMediaSources(
  sourceIds: number[],
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaSourceMutationResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<MediaSourceMutationResult>("/media-sources/bulk-delete", {
      ids: sourceIds,
    });
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
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
  scanModeOrHttpClient: ScanMode | ApiHttpClient = "full",
  httpClient: ApiHttpClient = apiClient,
): Promise<ScanJob> {
  const scanMode = typeof scanModeOrHttpClient === "string" ? scanModeOrHttpClient : "full";
  const client = typeof scanModeOrHttpClient === "string" ? httpClient : scanModeOrHttpClient;
  const post = requirePost(client);
  const response = await post<ScanJob>("/scan-jobs", {
    media_source_id: mediaSourceId,
    scan_mode: scanMode,
  });
  return response.data;
}

function buildQueryString(filters: Record<string, unknown>) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  return params.toString();
}

export async function fetchScanJobs(
  filters: ScanJobFilters = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<ScanJob[]> {
  const query = buildQueryString(filters);
  const response = await httpClient.get<ScanJob[]>(query ? `/scan-jobs?${query}` : "/scan-jobs");
  return response.data;
}

export async function fetchScanModeSuggestion(
  mediaSourceId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<ScanModeSuggestion> {
  const response = await httpClient.get<ScanModeSuggestion>(
    `/scan-jobs/mode-suggestion?media_source_id=${encodeURIComponent(String(mediaSourceId))}`,
  );
  return response.data;
}

export async function fetchMediaFiles(
  filters: MediaFileFilters = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<MediaFile[]> {
  const query = buildQueryString(filters);
  const response = await httpClient.get<MediaFile[]>(query ? `/media-files?${query}` : "/media-files");
  return response.data;
}

export async function fetchLogs(httpClient: ApiHttpClient = apiClient): Promise<LogItem[]> {
  const response = await httpClient.get<{ items: LogItem[] }>("/logs");
  return response.data.items;
}

export async function cleanupLogs(httpClient: ApiHttpClient = apiClient): Promise<LogCleanupSummary> {
  const post = requirePost(httpClient);
  const response = await post<LogCleanupSummary>("/logs/cleanup", {});
  return response.data;
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
  const query = buildQueryString(filters);
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

export async function excludeRenamePreview(
  previewId: number,
  reason = "manual_excluded",
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview> {
  const post = requirePost(httpClient);
  const response = await post<RenamePreview>(`/rename-previews/${previewId}/exclude`, { reason });
  return response.data;
}

export async function excludeRenamePreviews(
  renamePreviewIds: number[],
  reason = "manual_excluded",
  httpClient: ApiHttpClient = apiClient,
): Promise<BatchRenamePreviewExcludeResult> {
  const post = requirePost(httpClient);
  const response = await post<BatchRenamePreviewExcludeResult>("/rename-previews/exclude", {
    rename_preview_ids: renamePreviewIds,
    reason,
  });
  return response.data;
}

export async function createRenameDryRun(
  renamePreviewIds: number[],
  httpClient: ApiHttpClient = apiClient,
): Promise<RenameOperation> {
  const post = requirePost(httpClient);
  const response = await post<RenameOperation>("/rename-operations/dry-run", {
    rename_preview_ids: renamePreviewIds,
  });
  return response.data;
}

export async function fetchRenameOperation(
  operationId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<RenameOperation> {
  const response = await httpClient.get<RenameOperation>(`/rename-operations/${operationId}`);
  return response.data;
}

export async function executeRenameOperation(
  operationId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<RenameOperation> {
  const post = requirePost(httpClient);
  const response = await post<RenameOperation>(`/rename-operations/${operationId}/execute`, {});
  return response.data;
}

export async function fetchSharedProtocolCapabilities(
  httpClient: ApiHttpClient = apiClient,
): Promise<SharedProtocolCapability[]> {
  const response = await httpClient.get<SharedProtocolCapability[]>("/shared-protocols");
  return response.data;
}

export async function testMediaSourceConnection(
  sourceId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<ConnectionTestResult> {
  const post = requirePost(httpClient);
  const response = await post<ConnectionTestResult>(
    `/media-sources/${sourceId}/test-connection`,
    {},
  );
  return response.data;
}

export async function testMediaSourceConnectionPayload(
  payload: MediaSourceConnectionTestPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<ConnectionTestResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<ConnectionTestResult>("/media-sources/test-connection", payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function fetchMediaSourceDirectories(
  sourceId: number,
  path = "",
  httpClient: ApiHttpClient = apiClient,
): Promise<LocalDirectoryListing> {
  const params = new URLSearchParams();
  if (path.trim()) {
    params.set("path", path);
  }
  const query = params.toString();
  const response = await httpClient.get<LocalDirectoryListing>(
    query
      ? `/media-sources/${sourceId}/directories?${query}`
      : `/media-sources/${sourceId}/directories`,
  );
  return response.data;
}

export async function matchRenamePreviewMetadata(
  previewId: number,
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview> {
  const post = requirePost(httpClient);
  const response = await post<RenamePreview>(
    `/rename-previews/${previewId}/metadata-match?metadata_match_source=${encodeURIComponent(metadataMatchSource)}`,
    {},
  );
  return response.data;
}

export async function matchRenamePreviewsMetadata(
  renamePreviewIds: number[],
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<BatchMetadataMatchResult> {
  const post = requirePost(httpClient);
  const response = await post<BatchMetadataMatchResult>("/rename-previews/metadata-match", {
    rename_preview_ids: renamePreviewIds,
    metadata_match_source: metadataMatchSource,
  });
  return response.data;
}

export async function matchAllUnmatchedMetadata(
  filters: Pick<RenamePreviewFilters, "media_source_id" | "scan_job_id"> = {},
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<BatchMetadataMatchResult> {
  const post = requirePost(httpClient);
  const response = await post<BatchMetadataMatchResult>("/rename-previews/metadata-match/all", {
    media_source_id: filters.media_source_id,
    scan_job_id: filters.scan_job_id,
    metadata_match_source: metadataMatchSource,
  });
  return response.data;
}

export async function matchRenamePreviewsMetadataWithAiFallback(
  renamePreviewIds: number[],
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<MetadataAiFallbackResult> {
  const post = requirePost(httpClient);
  const response = await post<MetadataAiFallbackResult>("/rename-previews/metadata-match/ai-fallback", {
    rename_preview_ids: renamePreviewIds,
    metadata_match_source: metadataMatchSource,
  });
  return response.data;
}

export async function matchAllUnmatchedMetadataWithAiFallback(
  filters: Pick<RenamePreviewFilters, "media_source_id" | "scan_job_id"> = {},
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<MetadataAiFallbackResult> {
  const post = requirePost(httpClient);
  const response = await post<MetadataAiFallbackResult>("/rename-previews/metadata-match/all/ai-fallback", {
    media_source_id: filters.media_source_id,
    scan_job_id: filters.scan_job_id,
    metadata_match_source: metadataMatchSource,
  });
  return response.data;
}

export async function fetchRenamePreviewMetadataCandidates(
  previewId: number,
  metadataMatchSource: MetadataMatchSource = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<MetadataMatchResult[]> {
  const response = await httpClient.get<MetadataMatchResult[]>(
    `/rename-previews/${previewId}/metadata-candidates?metadata_match_source=${encodeURIComponent(metadataMatchSource)}`,
  );
  return response.data;
}

export async function applyRenamePreviewMetadataCandidate(
  previewId: number,
  match: MetadataMatchResult,
  selectedFieldsOrHttpClient: string[] | ApiHttpClient = [],
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview> {
  const selectedFields = Array.isArray(selectedFieldsOrHttpClient) ? selectedFieldsOrHttpClient : [];
  const client = Array.isArray(selectedFieldsOrHttpClient) ? httpClient : selectedFieldsOrHttpClient;
  const post = requirePost(client);
  const response = await post<RenamePreview>(`/rename-previews/${previewId}/metadata-candidate`, {
    candidate: match.candidate,
    score: match.score,
    selected_fields: selectedFields.length ? selectedFields : undefined,
  });
  return response.data;
}

export async function parseRenamePreviewWithAi(
  previewId: number,
  metadataMatchSourceOrHttpClient: MetadataMatchSource | ApiHttpClient = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<AiParseResult> {
  const metadataMatchSource = typeof metadataMatchSourceOrHttpClient === "string"
    ? metadataMatchSourceOrHttpClient
    : "parsed_title";
  const client = typeof metadataMatchSourceOrHttpClient === "string"
    ? httpClient
    : metadataMatchSourceOrHttpClient;
  const post = requirePost(client);
  try {
    const response = await post<AiParseResult>(
      `/rename-previews/${previewId}/ai-parse?metadata_match_source=${encodeURIComponent(metadataMatchSource)}`,
      {},
    );
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function parseRenamePreviewsWithAi(
  previewIds: number[],
  metadataMatchSourceOrHttpClient: MetadataMatchSource | ApiHttpClient = "parsed_title",
  httpClient: ApiHttpClient = apiClient,
): Promise<BatchAiParseResult> {
  const metadataMatchSource = typeof metadataMatchSourceOrHttpClient === "string"
    ? metadataMatchSourceOrHttpClient
    : "parsed_title";
  const client = typeof metadataMatchSourceOrHttpClient === "string"
    ? httpClient
    : metadataMatchSourceOrHttpClient;
  const post = requirePost(client);
  try {
    const response = await post<BatchAiParseResult>("/rename-previews/ai-parse/batch", {
      rename_preview_ids: previewIds,
      metadata_match_source: metadataMatchSource,
    });
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function applyAiParseCandidate(
  previewId: number,
  candidate: AiParseCandidate,
  httpClient: ApiHttpClient = apiClient,
): Promise<RenamePreview> {
  const post = requirePost(httpClient);
  try {
    const response = await post<RenamePreview>(`/rename-previews/${previewId}/ai-candidate`, {
      candidate,
    });
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function fetchSettings(
  httpClient: ApiHttpClient = apiClient,
): Promise<SystemSetting[]> {
  const response = await httpClient.get<SystemSetting[]>("/settings");
  return response.data;
}

export async function updateSettings(
  values: Record<string, unknown>,
  httpClient: ApiHttpClient = apiClient,
): Promise<SystemSetting[]> {
  const put = requirePut(httpClient);
  const response = await put<SystemSetting[]>("/settings", { values });
  return response.data;
}

export async function testNamingTemplate(
  payload: NamingTemplatePreviewPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<NamingTemplatePreviewResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<NamingTemplatePreviewResult>("/settings/naming/test", payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function diffNamingTemplate(
  payload: NamingTemplatePreviewPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<NamingTemplateDiffResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<NamingTemplateDiffResult>("/settings/naming/diff", payload);
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function fetchNamingTemplateBundle(
  httpClient: ApiHttpClient = apiClient,
): Promise<NamingTemplateBundle> {
  const response = await httpClient.get<NamingTemplateBundle>("/settings/naming/export");
  return response.data;
}

export async function importNamingTemplateBundle(
  rawText: string,
  httpClient: ApiHttpClient = apiClient,
): Promise<NamingTemplateBundle> {
  const post = requirePost(httpClient);
  try {
    const response = await post<NamingTemplateBundle>("/settings/naming/import", { raw_text: rawText });
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function fetchTmdbTestResult(
  httpClient: ApiHttpClient = apiClient,
): Promise<TmdbConnectionTestHistory> {
  const response = await httpClient.get<TmdbConnectionTestHistory>("/settings/tmdb/test-result");
  return response.data;
}

export async function fetchImdbTestResult(
  httpClient: ApiHttpClient = apiClient,
): Promise<ImdbConnectionTestHistory> {
  const response = await httpClient.get<ImdbConnectionTestHistory>("/settings/imdb/test-result");
  return response.data;
}

export async function fetchAiTestResult(
  httpClient: ApiHttpClient = apiClient,
): Promise<AiConnectionTestHistory> {
  const response = await httpClient.get<AiConnectionTestHistory>("/settings/ai/test-result");
  return response.data;
}

export async function fetchExternalSubmissionBlocks(
  filters: { status?: string; target_service?: string; limit?: number } = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<ExternalSubmissionBlockList> {
  const query = buildQueryString(filters);
  const response = await httpClient.get<ExternalSubmissionBlockList>(
    query ? `/external-submission-blocks?${query}` : "/external-submission-blocks",
  );
  return response.data;
}

export async function updateExternalSubmissionBlock(
  blockId: number,
  payload: UpdateExternalSubmissionBlockPayload,
  httpClient: ApiHttpClient = apiClient,
): Promise<ExternalSubmissionBlockRecord> {
  const patch = requirePatch(httpClient);
  const response = await patch<ExternalSubmissionBlockRecord>(`/external-submission-blocks/${blockId}`, payload);
  return response.data;
}

export async function testTmdbSettings(
  httpClient: ApiHttpClient = apiClient,
): Promise<TmdbConnectionTestResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<TmdbConnectionTestResult>("/settings/tmdb/test", {});
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function testImdbSettings(
  httpClient: ApiHttpClient = apiClient,
): Promise<ImdbConnectionTestResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<ImdbConnectionTestResult>("/settings/imdb/test", {});
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function testAiSettings(
  httpClient: ApiHttpClient = apiClient,
): Promise<AiConnectionTestResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<AiConnectionTestResult>("/settings/ai/test", {});
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function testTmdbSettingsChannel(
  channel: "v4" | "v3",
  httpClient: ApiHttpClient = apiClient,
): Promise<TmdbChannelTestResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<TmdbChannelTestResult>(`/settings/tmdb/test/${channel}`, {});
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function saveTmdbTestResult(
  v4: TmdbChannelTestResult,
  v3: TmdbChannelTestResult,
  httpClient: ApiHttpClient = apiClient,
): Promise<TmdbConnectionTestResult> {
  const post = requirePost(httpClient);
  const response = await post<TmdbConnectionTestResult>("/settings/tmdb/test-result", { v4, v3 });
  return response.data;
}

export async function saveImdbTestResult(
  result: ImdbConnectionTestResult,
  httpClient: ApiHttpClient = apiClient,
): Promise<ImdbStoredConnectionTestResult> {
  const post = requirePost(httpClient);
  const response = await post<ImdbStoredConnectionTestResult>("/settings/imdb/test-result", result);
  return response.data;
}

export async function fetchPendingFiles(
  filters: PendingFileFilters = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<PendingFile[]> {
  const query = buildQueryString(filters);
  const response = await httpClient.get<PendingFile[]>(
    query ? `/pending-files?${query}` : "/pending-files",
  );
  return response.data;
}

export async function removePendingFile(
  pendingFileId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<PendingFile> {
  const remove = requireDelete(httpClient);
  const response = await remove<PendingFile>(`/pending-files/${pendingFileId}`);
  return response.data;
}

export async function parsePendingFileWithAi(
  pendingFileId: number,
  httpClient: ApiHttpClient = apiClient,
): Promise<AiParseResult> {
  const post = requirePost(httpClient);
  try {
    const response = await post<AiParseResult>(`/pending-files/${pendingFileId}/ai-parse`, {});
    return response.data;
  } catch (error) {
    throw new Error(apiErrorMessage(error));
  }
}

export async function clearPendingFiles(
  filters: PendingFileFilters = {},
  httpClient: ApiHttpClient = apiClient,
): Promise<{ removed_count: number }> {
  const post = requirePost(httpClient);
  const query = buildQueryString(filters);
  const response = await post<{ removed_count: number }>(
    query ? `/pending-files/clear?${query}` : "/pending-files/clear",
    {},
  );
  return response.data;
}

export async function movePendingFiles(
  ids: number[],
  targetDirectory: string,
  httpClient: ApiHttpClient = apiClient,
): Promise<PendingFile[]> {
  const post = requirePost(httpClient);
  const response = await post<PendingFile[]>("/pending-files/move", {
    ids,
    target_directory: targetDirectory,
  });
  return response.data;
}
