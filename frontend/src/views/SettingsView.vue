<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";

import type {
  ImdbConnectionTestResult,
  ImdbStoredConnectionTestResult,
  TmdbChannelTestResult,
  TmdbConnectionTestResult,
} from "../api/client";
import { cleanupLogs } from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
import NamingTemplateBuilder from "../components/NamingTemplateBuilder.vue";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { useSettingsStore } from "../stores/settings";
import {
  detectNamingSemanticWarnings,
  parseNamingTemplate,
  serializeNamingElements,
  type NamingTemplateElement,
  validateNamingSeparator,
} from "../utils/namingBuilder";

const settingsStore = useSettingsStore();
const activeCategory = ref("tmdb");
const pageText = messages.settings;
const movieNamingElements = ref<NamingTemplateElement[]>([]);
const episodeNamingElements = ref<NamingTemplateElement[]>([]);
const imdbCardCollapsed = ref(localStorage.getItem("settings.imdb.cardCollapsed") !== "false");

const form = reactive({
  v4Token: "",
  apiKey: "",
  language: "zh-CN",
  region: "CN",
  timeoutSeconds: "15",
  enabled: false,
  priority: 1,
  imdbEnabled: false,
  imdbPriority: "tmdb_first",
  imdbTimeoutSeconds: "10",
  minimumFileSizeMb: "0",
  scanBatchSize: "100",
  scanBatchIntervalSeconds: "1",
  scanSkipHiddenFiles: true,
  scanRecursive: true,
  scanValidatePathBeforeScan: true,
  movieTemplate: "{title}.{year}",
  episodeTemplate: "{title}.{year}.S{season:02d}E{episode:02d}",
  namingSeparator: ".",
  keepYear: true,
  cleanIllegalChars: true,
  textTruncateBytes: "50",
  pathTruncateBytes: "80",
  logRetentionDays: "30",
  logLevel: "INFO",
  logPath: "logs",
  logArchiveAfterDays: "7",
  logDefaultLimit: "200",
  forceDryRun: true,
  requireSecondConfirmation: true,
  persistFailureDetail: true,
  batchLimit: "200",
  sharedDefaultPathType: "local",
  sharedConnectionTimeoutSeconds: "5",
  sharedDirectoryBrowseLimit: "500",
  forceScanConnectionTest: true,
  forceRenameWriteTest: true,
  nfsOperationTimeoutSeconds: "30",
  nfsRetryCount: "3",
  preferNfsv4: true,
  mountCheckIntervalSeconds: "60",
});

const categories = computed(() => [
  { key: "tmdb", label: pageText.categories.tmdb },
  { key: "naming", label: pageText.categories.naming },
  { key: "scan", label: pageText.categories.scan },
  { key: "operations", label: pageText.categories.operations },
  { key: "shared", label: pageText.categories.shared },
]);

function settingValue<T>(key: string, fallback: T): T {
  return (settingsStore.settingMap[key]?.value ?? fallback) as T;
}

function isMaskedSecret(value: unknown) {
  return typeof value === "string" && value.startsWith("********");
}

const savedV4TokenDisplay = computed(() => {
  const value = settingsStore.settingMap["tmdb.v4_token"]?.value;
  return isMaskedSecret(value) ? String(value) : "";
});

const savedApiKeyDisplay = computed(() => {
  const value = settingsStore.settingMap["tmdb.api_key"]?.value;
  return isMaskedSecret(value) ? String(value) : "";
});

const v4TokenPlaceholder = computed(() => savedV4TokenDisplay.value || pageText.tmdb.v4TokenPlaceholder);
const apiKeyPlaceholder = computed(() => savedApiKeyDisplay.value || pageText.tmdb.apiKeyPlaceholder);

function syncForm() {
  const v4Token = settingValue("tmdb.v4_token", "");
  form.v4Token = isMaskedSecret(v4Token) ? "" : String(v4Token);
  form.apiKey = "";
  form.language = settingValue("tmdb.language", "zh-CN");
  form.region = settingValue("tmdb.region", "CN");
  form.timeoutSeconds = String(Math.max(10, Math.round(Number(settingValue("tmdb.timeout_ms", 15000)) / 1000)));
  form.enabled = Boolean(settingValue("tmdb.enabled", false));
  form.priority = Number(settingValue("tmdb.priority", 1));
  form.imdbEnabled = Boolean(settingValue("imdb.enabled", false));
  form.imdbPriority = String(settingValue("imdb.priority", "tmdb_first"));
  form.imdbTimeoutSeconds = String(Math.max(5, Math.round(Number(settingValue("imdb.timeout_ms", 10000)) / 1000)));
  form.minimumFileSizeMb = String(Math.round(Number(settingValue("scan.minimum_file_size", 0)) / 1024 / 1024));
  form.scanBatchSize = String(settingValue("scan.batch_size", 100));
  form.scanBatchIntervalSeconds = String(settingValue("scan.batch_interval_seconds", 1));
  form.scanSkipHiddenFiles = Boolean(settingValue("scan.skip_hidden_files", true));
  form.scanRecursive = Boolean(settingValue("scan.recursive", true));
  form.scanValidatePathBeforeScan = Boolean(settingValue("scan.validate_path_before_scan", true));
  form.movieTemplate = String(settingValue("naming.movie_template", "{title}.{year}"));
  form.episodeTemplate = String(settingValue("naming.episode_template", "{title}.{year}.S{season:02d}E{episode:02d}"));
  movieNamingElements.value = parseNamingTemplate(form.movieTemplate, "movie", pageText.naming);
  episodeNamingElements.value = parseNamingTemplate(form.episodeTemplate, "episode", pageText.naming);
  form.namingSeparator = String(settingValue("naming.separator", "."));
  form.keepYear = Boolean(settingValue("naming.keep_year", true));
  form.cleanIllegalChars = Boolean(settingValue("naming.clean_illegal_chars", true));
  form.textTruncateBytes = String(settingValue("naming.text_truncate_bytes", 50));
  form.pathTruncateBytes = String(settingValue("naming.path_truncate_bytes", 80));
  form.logRetentionDays = String(settingValue("operations.log_retention_days", 30));
  form.logRetentionDays = String(settingValue("logging.retention_days", Number(form.logRetentionDays || 30)));
  form.logLevel = String(settingValue("logging.level", "INFO"));
  form.logPath = String(settingValue("logging.path", "logs"));
  form.logArchiveAfterDays = String(settingValue("logging.archive_after_days", 7));
  form.logDefaultLimit = String(settingValue("operations.log_default_limit", 200));
  form.forceDryRun = Boolean(settingValue("operations.force_dry_run", true));
  form.requireSecondConfirmation = Boolean(settingValue("operations.require_second_confirmation", true));
  form.persistFailureDetail = Boolean(settingValue("operations.persist_failure_detail", true));
  form.batchLimit = String(settingValue("operations.batch_limit", 200));
  form.sharedDefaultPathType = String(settingValue("shared.default_path_type", "local"));
  form.sharedConnectionTimeoutSeconds = String(settingValue("shared.connection_timeout_seconds", 5));
  form.sharedDirectoryBrowseLimit = String(settingValue("shared.directory_browse_limit", 500));
  form.forceScanConnectionTest = Boolean(settingValue("shared.force_scan_connection_test", true));
  form.forceRenameWriteTest = Boolean(settingValue("shared.force_rename_write_test", true));
  form.nfsOperationTimeoutSeconds = String(settingValue("shared.nfs_operation_timeout_seconds", 30));
  form.nfsRetryCount = String(settingValue("shared.nfs_retry_count", 3));
  form.preferNfsv4 = Boolean(settingValue("shared.prefer_nfsv4", true));
  form.mountCheckIntervalSeconds = String(settingValue("shared.mount_check_interval_seconds", 60));
}

function onlyDigits(
  key:
    | "timeoutSeconds"
    | "imdbTimeoutSeconds"
    | "minimumFileSizeMb"
    | "scanBatchSize"
    | "scanBatchIntervalSeconds"
    | "textTruncateBytes"
    | "pathTruncateBytes"
    | "logRetentionDays"
    | "logArchiveAfterDays"
    | "logDefaultLimit"
    | "batchLimit"
    | "sharedConnectionTimeoutSeconds"
    | "sharedDirectoryBrowseLimit"
    | "nfsOperationTimeoutSeconds"
    | "nfsRetryCount"
    | "mountCheckIntervalSeconds",
) {
  form[key] = form[key].replace(/\D/g, "");
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || messages.errors.unknown);
}

async function notifySaveFailed(error: unknown) {
  await ElMessageBox.alert(`${pageText.saveFailedPrefix}${errorText(error)}`, pageText.saveFailedTitle, {
    confirmButtonText: messages.common.confirm,
    type: "error",
  });
}

async function saveTmdbSettings(options: { rethrow?: boolean } = {}) {
  const timeoutSeconds = validateTmdbTimeout();
  if (timeoutSeconds === null) {
    return;
  }
  try {
    const priority = Math.min(100, Math.max(0, Number(form.priority || 0)));
    const minimumFileSize = Math.max(0, Number(form.minimumFileSizeMb || 0)) * 1024 * 1024;
    form.timeoutSeconds = String(timeoutSeconds);
    form.priority = priority;
    form.minimumFileSizeMb = String(Math.round(minimumFileSize / 1024 / 1024));
    const values: Record<string, string | number | boolean> = {
      "tmdb.language": form.language,
      "tmdb.region": form.region,
      "tmdb.timeout_ms": timeoutSeconds * 1000,
      "tmdb.enabled": form.enabled,
      "tmdb.priority": priority,
      "scan.minimum_file_size": minimumFileSize,
    };
    if (form.v4Token.trim()) {
      values["tmdb.v4_token"] = form.v4Token.trim();
    }
    if (form.apiKey.trim()) {
      values["tmdb.api_key"] = form.apiKey.trim();
    }

    await settingsStore.saveSettings(values);
    syncForm();
    resetTmdbTestState();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
    if (options.rethrow) {
      throw error;
    }
  }
}

function validateImdbTimeout(): number | null {
  const value = Number(form.imdbTimeoutSeconds || 10);
  if (!Number.isFinite(value)) {
    ElMessage.warning(pageText.imdb.timeoutInvalid);
    form.imdbTimeoutSeconds = "10";
    return null;
  }
  if (value < 5) {
    ElMessage.warning(pageText.imdb.timeoutTooShort);
    form.imdbTimeoutSeconds = "5";
    return null;
  }
  if (value > 30) {
    ElMessage.warning(pageText.imdb.timeoutTooLong);
    form.imdbTimeoutSeconds = "30";
    return null;
  }
  form.imdbTimeoutSeconds = String(Math.round(value));
  return Math.round(value);
}

async function saveImdbSettings(options: { rethrow?: boolean } = {}) {
  const timeoutSeconds = validateImdbTimeout();
  if (timeoutSeconds === null) {
    return;
  }
  try {
    await settingsStore.saveSettings({
      "imdb.enabled": form.imdbEnabled,
      "imdb.priority": form.imdbPriority,
      "imdb.timeout_ms": timeoutSeconds * 1000,
    });
    syncForm();
    resetImdbTestState();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
    if (options.rethrow) {
      throw error;
    }
  }
}

async function saveScanSettings() {
  try {
    const minimumFileSize = Math.max(0, Number(form.minimumFileSizeMb || 0)) * 1024 * 1024;
    await settingsStore.saveSettings({
      "scan.batch_size": Number(form.scanBatchSize || 100),
      "scan.batch_interval_seconds": Number(form.scanBatchIntervalSeconds || 0),
      "scan.minimum_file_size": minimumFileSize,
      "scan.skip_hidden_files": form.scanSkipHiddenFiles,
      "scan.recursive": form.scanRecursive,
      "scan.validate_path_before_scan": form.scanValidatePathBeforeScan,
    });
    syncForm();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function saveNamingSettings() {
  const separatorError = validateNamingSeparator(form.namingSeparator, pageText.naming);
  if (separatorError) {
    ElMessage.error(separatorError);
    return;
  }
  const semanticWarnings = [
    ...detectNamingSemanticWarnings(movieNamingElements.value, pageText.naming),
    ...detectNamingSemanticWarnings(episodeNamingElements.value, pageText.naming),
  ];
  try {
    const movieTemplate = serializeNamingElements(movieNamingElements.value);
    const episodeTemplate = serializeNamingElements(episodeNamingElements.value);
    await settingsStore.saveSettings({
      "naming.movie_template": movieTemplate,
      "naming.episode_template": episodeTemplate,
      "naming.separator": form.namingSeparator,
      "naming.keep_year": form.keepYear,
      "naming.clean_illegal_chars": form.cleanIllegalChars,
      "naming.text_truncate_bytes": Number(form.textTruncateBytes || 50),
      "naming.path_truncate_bytes": Number(form.pathTruncateBytes || 80),
    });
    form.movieTemplate = movieTemplate;
    form.episodeTemplate = episodeTemplate;
    syncForm();
    ElMessage.success(pageText.saved);
    if (semanticWarnings.length) {
      ElMessage.warning(semanticWarnings[0]);
    }
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function refreshNamingSettings() {
  await settingsStore.loadSettings();
  syncForm();
}

async function saveOperationSettings() {
  try {
    await settingsStore.saveSettings({
      "operations.log_retention_days": Number(form.logRetentionDays || 30),
      "logging.retention_days": Number(form.logRetentionDays || 30),
      "logging.level": form.logLevel,
      "logging.path": form.logPath.trim() || "logs",
      "logging.archive_after_days": Number(form.logArchiveAfterDays || 7),
      "operations.log_default_limit": Number(form.logDefaultLimit || 200),
      "operations.force_dry_run": true,
      "operations.require_second_confirmation": true,
      "operations.persist_failure_detail": form.persistFailureDetail,
      "operations.batch_limit": Number(form.batchLimit || 200),
    });
    syncForm();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function cleanupLogFiles() {
  try {
    const result = await cleanupLogs();
    ElMessage.success(
      formatMessage(pageText.operations.logCleanupSummary, {
        archived: result.archived_count,
        deleted: result.deleted_count,
      }),
    );
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function saveSharedSettings() {
  try {
    await settingsStore.saveSettings({
      "shared.default_path_type": form.sharedDefaultPathType,
      "shared.connection_timeout_seconds": Number(form.sharedConnectionTimeoutSeconds || 5),
      "shared.directory_browse_limit": Number(form.sharedDirectoryBrowseLimit || 500),
      "shared.force_scan_connection_test": form.forceScanConnectionTest,
      "shared.force_rename_write_test": form.forceRenameWriteTest,
      "shared.nfs_operation_timeout_seconds": Number(form.nfsOperationTimeoutSeconds || 30),
      "shared.nfs_retry_count": Number(form.nfsRetryCount || 3),
      "shared.prefer_nfsv4": form.preferNfsv4,
      "shared.mount_check_interval_seconds": Number(form.mountCheckIntervalSeconds || 60),
    });
    syncForm();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

const testResult = ref<TmdbConnectionTestResult | null>(null);
const testResultDialogVisible = ref(false);
const lastTestTime = ref("");
const savedTmdbSnapshot = ref<Record<string, unknown> | null>(null);
const testResultSnapshot = ref<Record<string, unknown> | null>(null);
const testingChannel = ref<"v4" | "v3" | null>(null);
const testingElapsedSeconds = ref(0);
const testResultModalAutoOpen = ref(false);
let testingTimer: ReturnType<typeof window.setInterval> | undefined;
let tmdbStatusPanelScrollTop: number | null = null;
let tmdbStatusPanelElement: HTMLElement | null = null;

type TmdbChannelResult = TmdbConnectionTestResult["v4"];

const imdbTestResult = ref<ImdbStoredConnectionTestResult | null>(null);
const imdbTestResultDialogVisible = ref(false);
const imdbLastTestTime = ref("");
const savedImdbSnapshot = ref<Record<string, unknown> | null>(null);
const testingImdb = ref(false);
const imdbTestingElapsedSeconds = ref(0);
const imdbTestResultModalAutoOpen = ref(false);
let imdbTestingTimer: ReturnType<typeof window.setInterval> | undefined;
let imdbStatusPanelScrollTop: number | null = null;
let imdbStatusPanelElement: HTMLElement | null = null;

function validateTmdbTimeout(): number | null {
  const value = Number(form.timeoutSeconds || 15);
  if (!Number.isFinite(value)) {
    ElMessage.warning(pageText.tmdb.timeoutInvalid);
    form.timeoutSeconds = "15";
    return null;
  }
  if (value < 10) {
    ElMessage.warning(pageText.tmdb.timeoutTooShort);
    form.timeoutSeconds = "10";
    return null;
  }
  if (value > 30) {
    ElMessage.warning(pageText.tmdb.timeoutTooLong);
    form.timeoutSeconds = "30";
    return null;
  }
  form.timeoutSeconds = String(Math.round(value));
  return Math.round(value);
}

function normalizeTmdbTestResult(result: unknown): TmdbConnectionTestResult {
  const payload = result as Partial<TmdbConnectionTestResult> & { success?: boolean; message?: string };
  if (payload.v4 && payload.v3 && typeof payload.effective === "string") {
    return payload as TmdbConnectionTestResult;
  }

  const status = payload.success ? "success" : "failed";
  return {
    v4: { status, message: payload.message || channelStatusText(status) },
    v3: { status: "skipped", message: pageText.tmdb.statusNotConfigured },
    effective: payload.success ? "v4" : "none",
  };
}

function channelStatusText(status: string): string {
  if (status === "success") return pageText.tmdb.statusSuccess;
  if (status === "failed") return pageText.tmdb.statusFailed;
  return pageText.tmdb.statusNotConfigured;
}

function channelStatusType(status: string): "success" | "danger" | "info" {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "info";
}

function effectiveChannelLabel(effective: string): string {
  if (effective === "v4") return "V4";
  if (effective === "v3") return "V3";
  return pageText.tmdb.noAvailableChannel;
}

const testChannels = computed(() => {
  if (!testResult.value) {
    return [];
  }
  return [
    { key: "v4", label: "V4", result: testResult.value.v4 },
    { key: "v3", label: "V3", result: testResult.value.v3 },
  ];
});

function normalizeSnapshot(snapshot: Record<string, unknown>) {
  return Object.keys(snapshot)
    .sort()
    .reduce<Record<string, unknown>>((acc, key) => {
      acc[key] = snapshot[key];
      return acc;
    }, {});
}

function snapshotsEqual(left: Record<string, unknown> | null, right: Record<string, unknown> | null) {
  if (!left || !right) {
    return false;
  }
  return JSON.stringify(normalizeSnapshot(left)) === JSON.stringify(normalizeSnapshot(right));
}

function buildTmdbFormSnapshot(): Record<string, unknown> {
  const timeoutSeconds = Math.min(30, Math.max(10, Number(form.timeoutSeconds || 15)));
  const priority = Math.min(100, Math.max(0, Number(form.priority || 0)));
  const minimumFileSize = Math.max(0, Number(form.minimumFileSizeMb || 0)) * 1024 * 1024;
  return {
    "tmdb.v4_token": form.v4Token.trim() || savedTmdbSnapshot.value?.["tmdb.v4_token"] || "",
    "tmdb.api_key": form.apiKey.trim() || savedTmdbSnapshot.value?.["tmdb.api_key"] || "",
    "tmdb.language": form.language,
    "tmdb.region": form.region,
    "tmdb.timeout_ms": timeoutSeconds * 1000,
    "tmdb.enabled": form.enabled,
    "tmdb.priority": priority,
    "scan.minimum_file_size": minimumFileSize,
  };
}

watch(imdbCardCollapsed, (value) => {
  localStorage.setItem("settings.imdb.cardCollapsed", value ? "true" : "false");
});

function resetTmdbTestState() {
  testResult.value = null;
  testResultSnapshot.value = null;
  lastTestTime.value = "";
  testResultDialogVisible.value = false;
}

function buildImdbFormSnapshot(): Record<string, unknown> {
  const timeoutSeconds = Math.min(30, Math.max(5, Number(form.imdbTimeoutSeconds || 10)));
  return {
    "imdb.enabled": form.imdbEnabled,
    "imdb.priority": form.imdbPriority,
    "imdb.timeout_ms": timeoutSeconds * 1000,
  };
}

function resetImdbTestState() {
  imdbTestResult.value = null;
  imdbLastTestTime.value = "";
  imdbTestResultDialogVisible.value = false;
}

const imdbStatusBar = computed(() => {
  if (testingImdb.value) {
    return {
      icon: "⏳",
      text: `IMDb ${pageText.imdb.testing} ${pageText.imdb.waited} ${imdbTestingElapsedSeconds.value} ${pageText.imdb.seconds}`,
      time: "",
      tone: "testing",
    };
  }

  if (!imdbTestResult.value) {
    return {
      icon: "⚪",
      text: pageText.imdb.testStatusUntested,
      time: "",
      tone: "unknown",
    };
  }

  const success = imdbTestResult.value.connection_status === "success";
  return {
    icon: success ? "🟢" : "🔴",
    text: success ? pageText.imdb.testSuccess : pageText.imdb.testFailed,
    time: imdbLastTestTime.value || pageText.imdb.noTestRecord,
    tone: success ? "success" : "danger",
  };
});

function imdbResponseTimeText(result: ImdbStoredConnectionTestResult | null) {
  if (typeof result?.response_time === "number") {
    return `${Math.round(result.response_time)} ms`;
  }
  return pageText.imdb.responseTimeUnavailable;
}

const testStatusBar = computed(() => {
  if (testingChannel.value) {
    return {
      icon: "○",
      text: `${testingChannel.value.toUpperCase()} ${pageText.tmdb.testing} ${pageText.tmdb.waited} ${testingElapsedSeconds.value} ${pageText.tmdb.seconds}`,
      channel: "",
      time: "",
      tone: "testing",
    };
  }

  if (!testResult.value) {
    return {
      icon: "○",
      text: pageText.tmdb.testStatusUntested,
      channel: pageText.tmdb.noTestChannel,
      time: pageText.tmdb.noTestRecord,
      tone: "unknown",
    };
  }

  const healthy = testResult.value.v4.status === "success" || testResult.value.v3.status === "success";
  const failureMessage = tmdbFailureStatusMessage(testResult.value);
  if (!healthy && failureMessage) {
    return {
      icon: "●",
      text: failureMessage,
      channel: "",
      time: "",
      tone: "danger",
    };
  }
  return {
    icon: healthy ? "●" : "●",
    text: healthy ? pageText.tmdb.testStatusNormal : pageText.tmdb.testStatusAbnormal,
    channel: effectiveChannelLabel(testResult.value.effective),
    time: lastTestTime.value || pageText.tmdb.noTestRecord,
    tone: healthy ? "success" : "danger",
  };
});

const testSummaryText = computed(() => {
  if (!testResult.value) {
    return pageText.tmdb.noTestRecord;
  }
  if (testResult.value.effective === "v4") {
    return formatMessage(pageText.tmdb.summaryV4, { time: responseTimeText(testResult.value.v4) });
  }
  if (testResult.value.effective === "v3") {
    return formatMessage(pageText.tmdb.summaryV3, { reason: channelReasonText(testResult.value.v4) });
  }
  return formatMessage(pageText.tmdb.summaryNone, {
    v4Reason: channelReasonText(testResult.value.v4),
    v3Reason: channelReasonText(testResult.value.v3),
  });
});

function responseTimeText(result: TmdbChannelResult) {
  if (typeof result.response_ms === "number") {
    return `${Math.round(result.response_ms)} ms`;
  }
  return pageText.tmdb.responseTimeUnavailable;
}

function channelReasonText(result: TmdbChannelResult) {
  if (result.error_type === "network") {
    return pageText.tmdb.networkUnavailable;
  }
  if (result.error_type === "server" || result.error_type === "timeout") {
    return pageText.tmdb.tmdbTimeoutOrBusy;
  }
  if (result.error_type === "client" && result.http_status) {
    return pageText.tmdb.clientError.replace("{status}", String(result.http_status));
  }
  return result.message || channelStatusText(result.status);
}

function channelDetailText(channel: { label: string; result: TmdbChannelResult }) {
  if (channel.result.status === "success") {
    return formatMessage(pageText.tmdb.channelSuccessDetail, {
      channel: channel.label,
      time: responseTimeText(channel.result),
    });
  }
  if (channel.result.status === "failed") {
    return formatMessage(pageText.tmdb.channelFailedDetail, {
      channel: channel.label,
      reason: channelReasonText(channel.result),
    });
  }
  return formatMessage(pageText.tmdb.channelSkippedDetail, {
    channel: channel.label,
    reason: channelReasonText(channel.result),
  });
}

function tmdbFailureStatusMessage(result: TmdbConnectionTestResult) {
  const failures = [result.v4, result.v3].filter((item) => item.status === "failed");
  const first = failures[0];
  if (!first) {
    return "";
  }
  if (first.error_type === "network") {
    return pageText.tmdb.networkUnavailable;
  }
  if (first.error_type === "server" || first.error_type === "timeout") {
    return pageText.tmdb.tmdbTimeoutOrBusy;
  }
  if (first.error_type === "client" && first.http_status) {
    return pageText.tmdb.clientError.replace("{status}", String(first.http_status));
  }
  return first.message || pageText.tmdb.summaryNone;
}

function formatClock(date: Date) {
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatTestedAt(value?: string) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return formatClock(date);
}

function startTestingProgress(channel: "v4" | "v3") {
  stopTestingProgress();
  testingChannel.value = channel;
  testingElapsedSeconds.value = 0;
  testingTimer = window.setInterval(() => {
    testingElapsedSeconds.value += 1;
  }, 1000);
}

function stopTestingProgress() {
  if (testingTimer !== undefined) {
    window.clearInterval(testingTimer);
    testingTimer = undefined;
  }
  testingChannel.value = null;
  testingElapsedSeconds.value = 0;
}

function startImdbTestingProgress() {
  stopImdbTestingProgress();
  testingImdb.value = true;
  imdbTestingElapsedSeconds.value = 0;
  imdbTestingTimer = window.setInterval(() => {
    imdbTestingElapsedSeconds.value += 1;
  }, 1000);
}

function stopImdbTestingProgress() {
  if (imdbTestingTimer !== undefined) {
    window.clearInterval(imdbTestingTimer);
    imdbTestingTimer = undefined;
  }
  testingImdb.value = false;
  imdbTestingElapsedSeconds.value = 0;
}

async function runTmdbChannelTest(channel: "v4" | "v3"): Promise<TmdbChannelTestResult> {
  startTestingProgress(channel);
  try {
    return await settingsStore.testTmdbSettingsChannel(channel);
  } catch (error) {
    return {
      status: "failed",
      message: error instanceof Error ? error.message : String(error),
      error_type: "unknown",
      response_ms: null,
    };
  } finally {
    stopTestingProgress();
  }
}

async function executeTmdbTest() {
  testResultDialogVisible.value = false;
  testResultModalAutoOpen.value = false;
  try {
    const v4 = await runTmdbChannelTest("v4");
    const v3 = await runTmdbChannelTest("v3");
    const result = await settingsStore.saveTmdbTestResult(v4, v3);
    testResult.value = normalizeTmdbTestResult(result);
    testResultSnapshot.value = testResult.value.config_snapshot ?? buildTmdbFormSnapshot();
    savedTmdbSnapshot.value = testResult.value.config_snapshot ?? savedTmdbSnapshot.value;
    lastTestTime.value = formatTestedAt(testResult.value.tested_at) || formatClock(new Date());
  } catch (error) {
    testResult.value = {
      v4: { status: "failed", message: String(error) },
      v3: { status: "failed", message: String(error) },
      effective: "none",
    };
    testResultSnapshot.value = buildTmdbFormSnapshot();
    lastTestTime.value = formatClock(new Date());
  }
  testResultModalAutoOpen.value = true;
  testResultDialogVisible.value = true;
}

async function executeImdbTest() {
  imdbTestResultDialogVisible.value = false;
  imdbTestResultModalAutoOpen.value = false;
  startImdbTestingProgress();
  try {
    const result = await settingsStore.testImdbSettings();
    const saved = await settingsStore.saveImdbTestResult(result);
    imdbTestResult.value = saved;
    savedImdbSnapshot.value = saved.config_snapshot;
    imdbLastTestTime.value = formatTestedAt(saved.test_time) || formatClock(new Date());
  } catch (error) {
    const failedResult: ImdbConnectionTestResult = {
      status: "failed",
      message: pageText.imdb.testFailed,
      error_message: String(error),
    };
    const saved = await settingsStore.saveImdbTestResult(failedResult);
    imdbTestResult.value = saved;
    savedImdbSnapshot.value = saved.config_snapshot;
    imdbLastTestTime.value = formatTestedAt(saved.test_time) || formatClock(new Date());
  } finally {
    stopImdbTestingProgress();
  }
  imdbTestResultModalAutoOpen.value = true;
  imdbTestResultDialogVisible.value = true;
}

async function testTmdbConnection() {
  if (snapshotsEqual(buildTmdbFormSnapshot(), savedTmdbSnapshot.value)) {
    await executeTmdbTest();
    return;
  }

  try {
    await ElMessageBox.confirm(
      pageText.tmdb.unsavedConfirm,
      pageText.tmdb.unsavedConfirmTitle,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        type: "warning",
      },
    );
    try {
      await saveTmdbSettings({ rethrow: true });
      savedTmdbSnapshot.value = buildTmdbFormSnapshot();
      await executeTmdbTest();
    } catch {
      ElMessage.error(pageText.tmdb.saveBeforeTestFailed);
    }
  } catch {
    await executeTmdbTest();
  }
}

async function testImdbConnection() {
  if (snapshotsEqual(buildImdbFormSnapshot(), savedImdbSnapshot.value)) {
    await executeImdbTest();
    return;
  }

  try {
    await ElMessageBox.confirm(
      pageText.imdb.unsavedConfirm,
      pageText.tmdb.unsavedConfirmTitle,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        type: "warning",
      },
    );
    try {
      await saveImdbSettings({ rethrow: true });
      savedImdbSnapshot.value = buildImdbFormSnapshot();
      await executeImdbTest();
    } catch {
      ElMessage.error(pageText.imdb.saveBeforeTestFailed);
    }
  } catch {
    await executeImdbTest();
  }
}

async function openTmdbTestDetail() {
  if (testingChannel.value || testResultModalAutoOpen.value) {
    return;
  }
  if (!testResult.value) {
    try {
      const history = await settingsStore.loadTmdbTestResult({ silent: true });
      savedTmdbSnapshot.value = history.current_snapshot;
      if (history.result) {
        testResult.value = normalizeTmdbTestResult(history.result);
        testResultSnapshot.value = history.result.config_snapshot;
        lastTestTime.value = formatTestedAt(history.result.tested_at);
      }
    } catch {
      // Keep the click behavior focused on the status bar message.
    }
  }
  if (!testResult.value) {
    ElMessage.info(pageText.tmdb.noTestRecordHint);
    return;
  }
  testResultDialogVisible.value = true;
}

async function openImdbTestDetail() {
  if (testingImdb.value || imdbTestResultModalAutoOpen.value) {
    return;
  }
  if (!imdbTestResult.value) {
    try {
      const history = await settingsStore.loadImdbTestResult({ silent: true });
      savedImdbSnapshot.value = history.current_snapshot;
      if (history.result) {
        imdbTestResult.value = history.result;
        imdbLastTestTime.value = formatTestedAt(history.result.test_time);
      }
    } catch {
      // Keep the click behavior focused on the status bar message.
    }
  }
  if (!imdbTestResult.value) {
    ElMessage.info(pageText.imdb.noTestRecordHint);
    return;
  }
  imdbTestResultDialogVisible.value = true;
}

async function handleTmdbStatusClick() {
  try {
    await openTmdbTestDetail();
  } finally {
    restoreTmdbStatusPanelScroll();
  }
}

function handleTmdbTestDialogClosed() {
  testResultModalAutoOpen.value = false;
}

async function handleImdbStatusClick() {
  try {
    await openImdbTestDetail();
  } finally {
    restoreImdbStatusPanelScroll();
  }
}

function handleImdbTestDialogClosed() {
  imdbTestResultModalAutoOpen.value = false;
}

function rememberTmdbStatusPanelScroll(event: Event) {
  const panel = (event.currentTarget as HTMLElement | null)?.closest(".settings-panel") as HTMLElement | null;
  tmdbStatusPanelElement = panel;
  tmdbStatusPanelScrollTop = panel?.scrollTop ?? null;
}

function rememberImdbStatusPanelScroll(event: Event) {
  const panel = (event.currentTarget as HTMLElement | null)?.closest(".settings-panel") as HTMLElement | null;
  imdbStatusPanelElement = panel;
  imdbStatusPanelScrollTop = panel?.scrollTop ?? null;
}

function restoreTmdbStatusPanelScroll() {
  const panel = tmdbStatusPanelElement;
  const scrollTop = tmdbStatusPanelScrollTop;
  if (!panel || scrollTop === null) {
    return;
  }
  window.requestAnimationFrame(() => {
    panel.scrollTop = scrollTop;
    window.requestAnimationFrame(() => {
      panel.scrollTop = scrollTop;
    });
  });
}

function restoreImdbStatusPanelScroll() {
  const panel = imdbStatusPanelElement;
  const scrollTop = imdbStatusPanelScrollTop;
  if (!panel || scrollTop === null) {
    return;
  }
  window.requestAnimationFrame(() => {
    panel.scrollTop = scrollTop;
    window.requestAnimationFrame(() => {
      panel.scrollTop = scrollTop;
    });
  });
}

onMounted(async () => {
  await settingsStore.loadSettings();
  syncForm();
  try {
    const history = await settingsStore.loadTmdbTestResult({ silent: true });
    savedTmdbSnapshot.value = history.current_snapshot;
    if (history.result && history.matches_current) {
      testResult.value = normalizeTmdbTestResult(history.result);
      testResultSnapshot.value = history.result.config_snapshot;
      lastTestTime.value = formatTestedAt(history.result.tested_at);
    }
  } catch {
    // The page can still be edited and saved if historical test loading fails.
  }
  try {
    const history = await settingsStore.loadImdbTestResult({ silent: true });
    savedImdbSnapshot.value = history.current_snapshot;
    if (history.result && history.matches_current) {
      imdbTestResult.value = history.result;
      imdbLastTestTime.value = formatTestedAt(history.result.test_time);
    }
  } catch {
    // The page can still be edited and saved if historical IMDb test loading fails.
  }
});
</script>

<template>
  <ListPageLayout :title="pageText.title" :description="pageText.description">
    <el-alert v-if="settingsStore.errorMessage" type="error" :title="settingsStore.errorMessage" show-icon />

    <div class="settings-shell">
      <aside class="settings-categories">
        <button
          v-for="category in categories"
          :key="category.key"
          type="button"
          class="settings-category-button"
          :class="{ 'is-active': activeCategory === category.key }"
          @click="activeCategory = category.key"
        >
          {{ category.label }}
        </button>
      </aside>

      <section class="settings-panel">
        <el-form v-if="activeCategory === 'tmdb'" label-position="top" class="settings-form">
          <div class="settings-config-card">
            <el-alert
              :title="pageText.tmdb.priorityHint"
              type="info"
              :closable="false"
              show-icon
            />

            <el-form-item :label="pageText.tmdb.v4Token">
              <el-input
                v-model="form.v4Token"
                class="settings-secret-control"
                :placeholder="v4TokenPlaceholder"
                show-password
                clearable
              />
              <span class="setting-source">{{ pageText.tmdb.v4TokenHint }}</span>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.apiKey">
              <el-input
                v-model="form.apiKey"
                class="settings-secret-control"
                :placeholder="apiKeyPlaceholder"
                show-password
                clearable
              />
            </el-form-item>

            <div class="settings-grid">
            <el-form-item :label="pageText.tmdb.language">
              <el-select v-model="form.language" class="settings-code-control">
                <el-option label="zh-CN" value="zh-CN" />
                <el-option label="en-US" value="en-US" />
              </el-select>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.region">
              <el-select v-model="form.region" class="settings-code-control">
                <el-option label="CN" value="CN" />
                <el-option label="US" value="US" />
                <el-option label="HK" value="HK" />
                <el-option label="TW" value="TW" />
                <el-option label="JP" value="JP" />
                <el-option label="KR" value="KR" />
              </el-select>
            </el-form-item>

            <el-form-item>
              <template #label>
                <span class="settings-label-with-help">
                  <span>{{ pageText.tmdb.timeout }}</span>
                  <el-tooltip :content="pageText.tmdb.timeoutHelp" placement="top">
                    <span class="settings-help-icon">?</span>
                  </el-tooltip>
                </span>
              </template>
              <el-input
                v-model="form.timeoutSeconds"
                class="settings-number-control"
                maxlength="2"
                @blur="validateTmdbTimeout"
                @input="onlyDigits('timeoutSeconds')"
              >
                <template #append>{{ pageText.tmdb.seconds }}</template>
              </el-input>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.priority">
              <el-input-number
                v-model="form.priority"
                class="settings-short-control"
                :min="0"
                :max="100"
                :step="1"
                :precision="0"
                controls-position="right"
              />
            </el-form-item>

            <el-form-item :label="pageText.tmdb.minimumFileSize">
              <el-input v-model="form.minimumFileSizeMb" class="settings-threshold-control" maxlength="4" @input="onlyDigits('minimumFileSizeMb')">
                <template #append>{{ pageText.tmdb.megabytes }}</template>
              </el-input>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.enabled">
              <el-switch v-model="form.enabled" />
            </el-form-item>
            </div>

            <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button :loading="settingsStore.loading" @click="testTmdbConnection">
              {{ pageText.tmdb.testConnection }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveTmdbSettings">
              {{ messages.common.save }}
            </el-button>
            </div>

            <div
              role="button"
              class="settings-test-status-bar"
              :class="[`is-${testStatusBar.tone}`, { 'is-disabled': testingChannel || testResultModalAutoOpen }]"
              :aria-disabled="Boolean(testingChannel || testResultModalAutoOpen)"
              @pointerdown.prevent="rememberTmdbStatusPanelScroll"
              @mousedown.prevent="rememberTmdbStatusPanelScroll"
              @click="handleTmdbStatusClick"
            >
            <span class="settings-test-status-state">
              <span class="settings-test-status-icon">{{ testStatusBar.icon }}</span>
              <span class="settings-test-status-text">{{ testStatusBar.text }}</span>
            </span>
            <template v-if="testStatusBar.channel || testStatusBar.time">
              <span class="settings-test-separator">|</span>
              <span class="settings-test-status-meta">{{ pageText.tmdb.channelLabel }}{{ testStatusBar.channel }}</span>
              <span class="settings-test-separator">|</span>
              <span class="settings-test-status-meta">{{ testResult ? pageText.tmdb.updatedAt + testStatusBar.time : testStatusBar.time }}</span>
            </template>
            </div>

            <el-dialog
            v-model="testResultDialogVisible"
            :title="pageText.tmdb.testDetailTitle"
            width="min(540px, calc(100vw - 2rem))"
            align-center
            @closed="handleTmdbTestDialogClosed"
          >
            <div v-if="testResult" class="settings-test-detail">
              <div
                v-for="channel in testChannels"
                :key="channel.key"
                class="settings-test-detail-row"
              >
                <div class="settings-test-detail-heading">
                  <strong>{{ channel.label }}</strong>
                  <el-tag :type="channelStatusType(channel.result.status)" effect="light">
                    {{ channelStatusText(channel.result.status) }}
                  </el-tag>
                </div>
                <div class="settings-test-detail-item">
                  <span>{{ pageText.tmdb.responseTime }}</span>
                  <strong>{{ responseTimeText(channel.result) }}</strong>
                </div>
                <div class="settings-test-detail-item">
                  <span>{{ pageText.tmdb.detailMessage }}</span>
                  <strong>{{ channelDetailText(channel) }}</strong>
                </div>
              </div>
              <div class="settings-test-summary">
                <span>{{ pageText.tmdb.effectiveChannel }}</span>
                <strong>{{ testSummaryText }}</strong>
              </div>
            </div>
            <template #footer>
              <el-button @click="testResultDialogVisible = false">{{ messages.common.close }}</el-button>
            </template>
            </el-dialog>
          </div>

          <div class="settings-config-card" :class="{ 'is-collapsed': imdbCardCollapsed }">
            <div
              class="settings-config-card-title settings-config-card-toggle"
              role="button"
              tabindex="0"
              @click="imdbCardCollapsed = !imdbCardCollapsed"
              @keydown.enter.prevent="imdbCardCollapsed = !imdbCardCollapsed"
              @keydown.space.prevent="imdbCardCollapsed = !imdbCardCollapsed"
            >
              <span>{{ pageText.imdb.title }}</span>
              <el-button text size="small" @click.stop="imdbCardCollapsed = !imdbCardCollapsed">
                {{ imdbCardCollapsed ? messages.common.expand : messages.common.collapse }}
              </el-button>
            </div>
            <template v-if="!imdbCardCollapsed">
            <div class="settings-grid">
              <el-form-item :label="pageText.imdb.enabled">
                <el-switch v-model="form.imdbEnabled" />
              </el-form-item>

              <el-form-item :label="pageText.imdb.priority">
                <el-select v-model="form.imdbPriority" class="settings-code-control">
                  <el-option :label="pageText.imdb.priorityTmdbFirst" value="tmdb_first" />
                  <el-option :label="pageText.imdb.priorityImdbFirst" value="imdb_first" />
                </el-select>
              </el-form-item>

              <el-form-item :label="pageText.imdb.timeout">
                <el-input
                  v-model="form.imdbTimeoutSeconds"
                  class="settings-number-control"
                  maxlength="2"
                  @blur="validateImdbTimeout"
                  @input="onlyDigits('imdbTimeoutSeconds')"
                >
                  <template #append>{{ pageText.imdb.seconds }}</template>
                </el-input>
              </el-form-item>
            </div>

            <div class="settings-actions">
              <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
                {{ messages.common.refresh }}
              </el-button>
              <el-button :loading="settingsStore.loading || testingImdb" @click="testImdbConnection">
                {{ pageText.imdb.testConnection }}
              </el-button>
              <el-button type="primary" :loading="settingsStore.loading" @click="saveImdbSettings">
                {{ messages.common.save }}
              </el-button>
            </div>

            <div
              role="button"
              class="settings-test-status-bar"
              :class="[`is-${imdbStatusBar.tone}`, { 'is-disabled': testingImdb || imdbTestResultModalAutoOpen }]"
              :aria-disabled="Boolean(testingImdb || imdbTestResultModalAutoOpen)"
              @pointerdown.prevent="rememberImdbStatusPanelScroll"
              @mousedown.prevent="rememberImdbStatusPanelScroll"
              @click="handleImdbStatusClick"
            >
              <span class="settings-test-status-state">
                <span class="settings-test-status-icon">{{ imdbStatusBar.icon }}</span>
                <span class="settings-test-status-text">{{ imdbStatusBar.text }}</span>
              </span>
              <template v-if="imdbStatusBar.time">
                <span class="settings-test-separator">·</span>
                <span class="settings-test-status-meta">{{ imdbTestResult ? pageText.imdb.updatedAt + imdbStatusBar.time : imdbStatusBar.time }}</span>
              </template>
            </div>

            <el-dialog
              v-model="imdbTestResultDialogVisible"
              :title="pageText.imdb.testDetailTitle"
              width="min(540px, calc(100vw - 2rem))"
              align-center
              @closed="handleImdbTestDialogClosed"
            >
              <div v-if="imdbTestResult" class="settings-test-detail">
                <div class="settings-test-detail-row">
                  <div class="settings-test-detail-heading">
                    <strong>IMDb</strong>
                    <el-tag :type="channelStatusType(imdbTestResult.connection_status)" effect="light">
                      {{ imdbTestResult.connection_status === "success" ? pageText.imdb.testSuccess : pageText.imdb.testFailed }}
                    </el-tag>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.imdb.responseTime }}</span>
                    <strong>{{ imdbResponseTimeText(imdbTestResult) }}</strong>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.imdb.detailMessage }}</span>
                    <strong>{{ imdbTestResult.error_message || (imdbTestResult.connection_status === "success" ? pageText.imdb.testSuccess : pageText.imdb.testFailed) }}</strong>
                  </div>
                </div>
                <div class="settings-test-summary">
                  <span>{{ pageText.imdb.effectiveStatus }}</span>
                  <strong>{{ imdbTestResult.connection_status === "success" ? pageText.imdb.testSuccess : pageText.imdb.testFailed }}</strong>
                </div>
              </div>
            <template #footer>
              <el-button @click="imdbTestResultDialogVisible = false">{{ messages.common.close }}</el-button>
            </template>
            </el-dialog>
            </template>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'scan'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.scan.batchSize">
              <el-input v-model="form.scanBatchSize" maxlength="5" @input="onlyDigits('scanBatchSize')" />
            </el-form-item>
            <el-form-item :label="pageText.scan.batchInterval">
              <el-input v-model="form.scanBatchIntervalSeconds" maxlength="4" @input="onlyDigits('scanBatchIntervalSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.scan.minimumFileSize">
              <el-input v-model="form.minimumFileSizeMb" maxlength="4" @input="onlyDigits('minimumFileSizeMb')">
                <template #append>{{ pageText.units.megabytes }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.scan.skipHiddenFiles">
              <el-switch v-model="form.scanSkipHiddenFiles" />
            </el-form-item>
            <el-form-item :label="pageText.scan.recursive">
              <el-switch v-model="form.scanRecursive" />
            </el-form-item>
            <el-form-item :label="pageText.scan.validatePathBeforeScan">
              <el-switch v-model="form.scanValidatePathBeforeScan" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveScanSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'naming'" label-position="top" class="settings-form">
          <NamingTemplateBuilder
            v-model:movie-elements="movieNamingElements"
            v-model:episode-elements="episodeNamingElements"
            v-model:separator="form.namingSeparator"
            :loading="settingsStore.loading"
            @refresh="refreshNamingSettings"
            @save="saveNamingSettings"
          />
          <div class="settings-grid naming-settings-grid">
            <el-form-item :label="pageText.naming.textTruncateBytes">
              <el-input
                v-model="form.textTruncateBytes"
                class="settings-short-number"
                maxlength="4"
                @input="onlyDigits('textTruncateBytes')"
              />
            </el-form-item>
            <el-form-item :label="pageText.naming.pathTruncateBytes">
              <el-input
                v-model="form.pathTruncateBytes"
                class="settings-short-number"
                maxlength="4"
                @input="onlyDigits('pathTruncateBytes')"
              />
            </el-form-item>
            <el-form-item :label="pageText.naming.keepYear">
              <el-switch v-model="form.keepYear" />
            </el-form-item>
            <el-form-item :label="pageText.naming.cleanIllegalChars">
              <el-switch v-model="form.cleanIllegalChars" disabled />
            </el-form-item>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'operations'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.operations.logRetentionDays">
              <el-input v-model="form.logRetentionDays" maxlength="4" @input="onlyDigits('logRetentionDays')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.logLevel">
              <el-select v-model="form.logLevel">
                <el-option :label="pageText.operations.logLevelDebug" value="DEBUG" />
                <el-option :label="pageText.operations.logLevelInfo" value="INFO" />
                <el-option :label="pageText.operations.logLevelWarning" value="WARNING" />
                <el-option :label="pageText.operations.logLevelError" value="ERROR" />
              </el-select>
            </el-form-item>
            <el-form-item :label="pageText.operations.logPath">
              <el-input v-model="form.logPath" />
            </el-form-item>
            <el-form-item :label="pageText.operations.logArchiveAfterDays">
              <el-input v-model="form.logArchiveAfterDays" maxlength="4" @input="onlyDigits('logArchiveAfterDays')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.logDefaultLimit">
              <el-input v-model="form.logDefaultLimit" maxlength="5" @input="onlyDigits('logDefaultLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.batchLimit">
              <el-input v-model="form.batchLimit" maxlength="5" @input="onlyDigits('batchLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.forceDryRun">
              <el-switch v-model="form.forceDryRun" disabled />
            </el-form-item>
            <el-form-item :label="pageText.operations.requireSecondConfirmation">
              <el-switch v-model="form.requireSecondConfirmation" disabled />
            </el-form-item>
            <el-form-item :label="pageText.operations.persistFailureDetail">
              <el-switch v-model="form.persistFailureDetail" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button :loading="settingsStore.loading" @click="cleanupLogFiles">
              {{ pageText.operations.cleanupLogs }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveOperationSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'shared'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.shared.defaultPathType">
              <el-select v-model="form.sharedDefaultPathType">
                <el-option :label="messages.mediaSources.pathTypes.local" value="local" />
                <el-option :label="messages.mediaSources.pathTypes.unc" value="unc" />
                <el-option :label="messages.mediaSources.pathTypes.mountedNfs" value="mounted_nfs" />
              </el-select>
            </el-form-item>
            <el-form-item :label="pageText.shared.connectionTimeout">
              <el-input v-model="form.sharedConnectionTimeoutSeconds" maxlength="4" @input="onlyDigits('sharedConnectionTimeoutSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.directoryBrowseLimit">
              <el-input v-model="form.sharedDirectoryBrowseLimit" maxlength="5" @input="onlyDigits('sharedDirectoryBrowseLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.shared.nfsOperationTimeout">
              <el-input v-model="form.nfsOperationTimeoutSeconds" maxlength="4" @input="onlyDigits('nfsOperationTimeoutSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.nfsRetryCount">
              <el-input v-model="form.nfsRetryCount" maxlength="2" @input="onlyDigits('nfsRetryCount')" />
            </el-form-item>
            <el-form-item :label="pageText.shared.mountCheckInterval">
              <el-input v-model="form.mountCheckIntervalSeconds" maxlength="5" @input="onlyDigits('mountCheckIntervalSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.forceScanConnectionTest">
              <el-switch v-model="form.forceScanConnectionTest" />
            </el-form-item>
            <el-form-item :label="pageText.shared.forceRenameWriteTest">
              <el-switch v-model="form.forceRenameWriteTest" />
            </el-form-item>
            <el-form-item :label="pageText.shared.preferNfsv4">
              <el-switch v-model="form.preferNfsv4" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveSharedSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>
      </section>
    </div>
  </ListPageLayout>
</template>
