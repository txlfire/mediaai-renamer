<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";

import type {
  AiConnectionTestResult,
  AiProviderProfile,
  ExternalSubmissionBlockRecord,
  ExternalSubmissionBlockStatus,
  ImdbConnectionTestResult,
  ImdbStoredConnectionTestResult,
  NamingTemplateDiffResult,
  NamingTemplatePreviewResult,
  TmdbChannelTestResult,
  TmdbConnectionTestResult,
} from "../api/client";
import {
  cleanupLogs,
  diffNamingTemplate,
  fetchExternalSubmissionBlocks,
  testNamingTemplate,
  updateExternalSubmissionBlock,
} from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
import NamingTemplateBuilder from "../components/NamingTemplateBuilder.vue";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { useSettingsStore } from "../stores/settings";
import { formatDateTime } from "../utils/displayFormat";
import {
  detectNamingSemanticWarnings,
  parseNamingTemplate,
  serializeNamingElements,
  type NamingTemplateElement,
  validateNamingSeparator,
} from "../utils/namingBuilder";
import { exportNamingTemplateBundle, importNamingTemplateBundle } from "../utils/namingTemplateWorkbench";
import { formatSensitiveWordsInput, parseSensitiveWordsInput } from "../utils/sensitiveWords";

const settingsStore = useSettingsStore();
const activeCategory = ref("tmdb");
const pageText = messages.settings;
const movieNamingElements = ref<NamingTemplateElement[]>([]);
const episodeNamingElements = ref<NamingTemplateElement[]>([]);
const imdbCardCollapsed = ref(localStorage.getItem("settings.imdb.cardCollapsed") !== "false");
const customSensitiveWordsCollapsed = ref(localStorage.getItem("settings.privacy.customWordsCollapsed") !== "false");
const defaultSensitiveWordsDialogVisible = ref(false);
const externalSubmissionBlocks = ref<ExternalSubmissionBlockRecord[]>([]);
const externalSubmissionBlockTotal = ref(0);
const externalSubmissionBlocksLoading = ref(false);
const namingWorkbenchDialogVisible = ref(false);
const namingWorkbenchMode = ref<"test" | "diff">("test");
const namingWorkbenchLoading = ref(false);
const namingWorkbenchResult = ref<NamingTemplatePreviewResult | null>(null);
const namingWorkbenchDiffResult = ref<NamingTemplateDiffResult | null>(null);
const namingImportInput = ref<HTMLInputElement | null>(null);

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
  aiEnabled: false,
  aiProfileId: "",
  aiProfileName: "",
  aiProvider: "deepseek",
  aiModel: "deepseek-chat",
  aiApiKey: "",
  aiBaseUrl: "https://api.deepseek.com",
  aiTimeoutSeconds: "30",
  aiMaxRetries: "2",
  minimumFileSizeMb: "0",
  scanBatchSize: "100",
  scanBatchIntervalSeconds: "1",
  scanSkipHiddenFiles: true,
  scanRecursive: true,
  scanValidatePathBeforeScan: true,
  movieTemplate: "{title}.{year}",
  episodeTemplate: "{title}.{year}.S{season:02d}E{episode:02d}",
  titleRecognitionMode: "parent_folder_fallback",
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
  defaultSensitiveWordsEnabled: true,
  defaultSensitiveWordsText: "",
  customSensitiveWordsText: "",
});
const namingWorkbenchForm = reactive({
  mediaType: "movie" as "movie" | "episode",
  title: pageText.naming.sample.title,
  chineseTitle: pageText.naming.sample.chineseTitle,
  englishTitle: pageText.naming.sample.englishTitle,
  originalTitle: pageText.naming.sample.originalTitle,
  year: pageText.naming.sample.year,
  season: pageText.naming.sample.season,
  episode: pageText.naming.sample.episode,
  extension: pageText.naming.sample.extension,
  resolution: pageText.naming.sample.resolution,
  source: pageText.naming.sample.source,
});

const categories = computed(() => [
  { key: "tmdb", label: pageText.categories.tmdb },
  { key: "privacy", label: pageText.categories.privacy },
  { key: "ai", label: pageText.categories.ai },
  { key: "naming", label: pageText.categories.naming },
  { key: "scan", label: pageText.categories.scan },
  { key: "shared", label: pageText.categories.shared },
  // 通用设置固定在最下方；后续新增设置分类应插入到它上方。
  { key: "operations", label: pageText.categories.operations },
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
const savedAiApiKeyDisplay = computed(() => {
  const value = settingsStore.settingMap["ai.api_key"]?.value;
  return isMaskedSecret(value) ? String(value) : "";
});
function isAiProviderProfile(value: unknown): value is AiProviderProfile {
  return typeof value === "object" && value !== null && "id" in value && "provider" in value && "model" in value;
}

const savedAiProfiles = computed<AiProviderProfile[]>(() => {
  const value = settingsStore.settingMap["ai.provider_profiles"]?.value;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isAiProviderProfile).map((profile) => ({
    ...profile,
    api_key: typeof profile.api_key === "string" ? profile.api_key : "",
    has_secret: Boolean(profile.has_secret),
  }));
});

const selectedSavedAiProfile = computed(() =>
  savedAiProfiles.value.find((profile) => profile.id === form.aiProfileId) ?? null,
);

const aiApiKeyPlaceholder = computed(() =>
  selectedSavedAiProfile.value?.api_key || savedAiApiKeyDisplay.value || pageText.ai.apiKeyPlaceholder,
);
const defaultSensitiveWordPreview = computed(() => parseSensitiveWordsInput(form.defaultSensitiveWordsText));
const customSensitiveWordPreview = computed(() => parseSensitiveWordsInput(form.customSensitiveWordsText));
const defaultSensitiveWordPreviewLimit = 30;
const visibleDefaultSensitiveWords = computed(() => defaultSensitiveWordPreview.value.slice(0, defaultSensitiveWordPreviewLimit));
const defaultSensitiveWordOverflowCount = computed(() =>
  Math.max(0, defaultSensitiveWordPreview.value.length - defaultSensitiveWordPreviewLimit),
);
const sensitiveWordTotal = computed(() =>
  new Set([
    ...(form.defaultSensitiveWordsEnabled ? defaultSensitiveWordPreview.value : []),
    ...customSensitiveWordPreview.value,
  ].map((word) => word.toLocaleLowerCase())).size,
);
const namingTemplateMetaList = computed(() => [
  {
    key: "movie",
    label: pageText.naming.movieTemplateVersion,
    version: Number(settingValue("naming.movie_template_version", 1)),
    updatedAt: String(settingValue("naming.movie_template_updated_at", "")),
  },
  {
    key: "episode",
    label: pageText.naming.episodeTemplateVersion,
    version: Number(settingValue("naming.episode_template_version", 1)),
    updatedAt: String(settingValue("naming.episode_template_updated_at", "")),
  },
]);
const namingWorkbenchDialogTitle = computed(() =>
  namingWorkbenchMode.value === "diff" ? pageText.naming.dialogTitleDiff : pageText.naming.dialogTitleTest,
);

watch(customSensitiveWordsCollapsed, (collapsed) => {
  localStorage.setItem("settings.privacy.customWordsCollapsed", String(collapsed));
});

watch(activeCategory, (category) => {
  if (category === "privacy") {
    void loadExternalSubmissionBlocks();
  }
});

watch(
  () => namingWorkbenchForm.mediaType,
  () => {
    namingWorkbenchResult.value = null;
    namingWorkbenchDiffResult.value = null;
  },
);

function resetDefaultSensitiveWords() {
  form.defaultSensitiveWordsText = formatSensitiveWordsInput(pageText.privacy.defaultWordsInitial);
}

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
  form.aiEnabled = Boolean(settingValue("ai.enabled", false));
  const activeAiProfileId = String(settingValue("ai.active_profile_id", "default"));
  const activeAiProfile = savedAiProfiles.value.find((profile) => profile.id === activeAiProfileId) ?? savedAiProfiles.value[0] ?? null;
  form.aiProfileId = activeAiProfile?.id || activeAiProfileId;
  form.aiProfileName = activeAiProfile?.name || "";
  form.aiProvider = activeAiProfile?.provider || String(settingValue("ai.provider", "deepseek"));
  form.aiModel = activeAiProfile?.model || String(settingValue("ai.model", "deepseek-chat"));
  form.aiApiKey = "";
  form.aiBaseUrl = activeAiProfile?.base_url || String(settingValue("ai.base_url", "https://api.deepseek.com"));
  form.aiTimeoutSeconds = String(Math.max(5, Math.round(Number(activeAiProfile?.timeout_ms ?? settingValue("ai.timeout_ms", 30000)) / 1000)));
  form.aiMaxRetries = String(activeAiProfile?.max_retries ?? settingValue("ai.max_retries", 2));
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
  form.titleRecognitionMode = String(settingValue("naming.title_recognition_mode", "parent_folder_fallback"));
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
  form.defaultSensitiveWordsEnabled = Boolean(settingValue("privacy.default_sensitive_words_enabled", true));
  form.defaultSensitiveWordsText = formatSensitiveWordsInput(settingValue("privacy.default_sensitive_words", []));
  form.customSensitiveWordsText = formatSensitiveWordsInput(settingValue("privacy.custom_sensitive_words", []));
}

function onlyDigits(
  key:
    | "timeoutSeconds"
    | "imdbTimeoutSeconds"
    | "aiTimeoutSeconds"
    | "aiMaxRetries"
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
      "naming.title_recognition_mode": form.titleRecognitionMode,
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

function currentNamingTemplateValue(mediaType: "movie" | "episode") {
  return mediaType === "episode"
    ? serializeNamingElements(episodeNamingElements.value)
    : serializeNamingElements(movieNamingElements.value);
}

function namingTemplateUpdatedText(updatedAt: string) {
  return updatedAt ? formatDateTime(updatedAt) : pageText.naming.notSavedYet;
}

function openNamingWorkbench(mode: "test" | "diff") {
  namingWorkbenchMode.value = mode;
  namingWorkbenchResult.value = null;
  namingWorkbenchDiffResult.value = null;
  namingWorkbenchDialogVisible.value = true;
}

function buildNamingWorkbenchSample() {
  const extra: Record<string, string> = {};
  if (namingWorkbenchForm.chineseTitle.trim()) {
    extra.chinese_title = namingWorkbenchForm.chineseTitle.trim();
  }
  if (namingWorkbenchForm.englishTitle.trim()) {
    extra.english_title = namingWorkbenchForm.englishTitle.trim();
  }
  if (namingWorkbenchForm.originalTitle.trim()) {
    extra.original_title = namingWorkbenchForm.originalTitle.trim();
  }
  if (namingWorkbenchForm.resolution.trim()) {
    extra.resolution = namingWorkbenchForm.resolution.trim();
  }
  if (namingWorkbenchForm.source.trim()) {
    extra.source = namingWorkbenchForm.source.trim();
  }
  return {
    title: namingWorkbenchForm.title.trim(),
    year: toOptionalNumber(namingWorkbenchForm.year),
    season: namingWorkbenchForm.mediaType === "episode" ? toOptionalNumber(namingWorkbenchForm.season) : null,
    episode: namingWorkbenchForm.mediaType === "episode" ? toOptionalNumber(namingWorkbenchForm.episode) : null,
    extension: namingWorkbenchForm.extension.trim() || ".mkv",
    extra,
  };
}

function toOptionalNumber(value: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

async function runNamingWorkbench() {
  namingWorkbenchLoading.value = true;
  namingWorkbenchResult.value = null;
  namingWorkbenchDiffResult.value = null;
  try {
    const payload = {
      media_type: namingWorkbenchForm.mediaType,
      template: currentNamingTemplateValue(namingWorkbenchForm.mediaType),
      separator: form.namingSeparator,
      keep_year: form.keepYear,
      sample: buildNamingWorkbenchSample(),
    };
    if (namingWorkbenchMode.value === "diff") {
      namingWorkbenchDiffResult.value = await diffNamingTemplate(payload);
    } else {
      namingWorkbenchResult.value = await testNamingTemplate(payload);
    }
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    namingWorkbenchLoading.value = false;
  }
}

function exportCurrentNamingTemplateBundle() {
  const content = exportNamingTemplateBundle({
    movieTemplate: currentNamingTemplateValue("movie"),
    episodeTemplate: currentNamingTemplateValue("episode"),
    separator: form.namingSeparator,
    keepYear: form.keepYear,
  });
  const blob = new Blob([content], { type: "application/json;charset=utf-8" });
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `mediaai-naming-template-v1-${Date.now()}.json`;
  link.click();
  window.URL.revokeObjectURL(objectUrl);
  ElMessage.success(pageText.naming.exportSuccess);
}

function triggerNamingTemplateImport() {
  namingImportInput.value?.click();
}

async function importNamingTemplateFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) {
    return;
  }
  try {
    const imported = importNamingTemplateBundle(await file.text());
    form.namingSeparator = imported.separator;
    form.keepYear = imported.keepYear;
    form.movieTemplate = imported.movieTemplate;
    form.episodeTemplate = imported.episodeTemplate;
    movieNamingElements.value = parseNamingTemplate(imported.movieTemplate, "movie", pageText.naming);
    episodeNamingElements.value = parseNamingTemplate(imported.episodeTemplate, "episode", pageText.naming);
    ElMessage.success(pageText.naming.importSuccess);
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    input.value = "";
  }
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

async function savePrivacySettings() {
  try {
    await settingsStore.saveSettings({
      "privacy.default_sensitive_words_enabled": form.defaultSensitiveWordsEnabled,
      "privacy.default_sensitive_words": parseSensitiveWordsInput(form.defaultSensitiveWordsText),
      "privacy.custom_sensitive_words": parseSensitiveWordsInput(form.customSensitiveWordsText),
    });
    syncForm();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function loadExternalSubmissionBlocks() {
  externalSubmissionBlocksLoading.value = true;
  try {
    const result = await fetchExternalSubmissionBlocks({ limit: 200 });
    externalSubmissionBlocks.value = result.items;
    externalSubmissionBlockTotal.value = result.total;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    externalSubmissionBlocksLoading.value = false;
  }
}

function externalSubmissionStatusType(status: ExternalSubmissionBlockStatus) {
  if (status === "blocked") {
    return "danger";
  }
  if (status === "override_submitted") {
    return "warning";
  }
  if (status === "ignored" || status === "archived") {
    return "info";
  }
  return "success";
}

function externalSubmissionStatusText(status: ExternalSubmissionBlockStatus) {
  const statusMap = pageText.privacy.blockStatuses as Record<string, string>;
  return statusMap[status] ?? status;
}

async function updateExternalSubmissionDecision(
  record: ExternalSubmissionBlockRecord,
  status: ExternalSubmissionBlockStatus,
  userDecision: string,
  overrideReason?: string,
) {
  try {
    await updateExternalSubmissionBlock(record.id, {
      status,
      user_decision: userDecision,
      override_reason: overrideReason,
    });
    ElMessage.success(pageText.privacy.blockDecisionSaved);
    await loadExternalSubmissionBlocks();
  } catch (error) {
    ElMessage.error(errorText(error));
  }
}

async function markExternalSubmissionIgnored(record: ExternalSubmissionBlockRecord) {
  await updateExternalSubmissionDecision(record, "ignored", pageText.privacy.blockDecisionIgnored);
}

async function markExternalSubmissionRenamed(record: ExternalSubmissionBlockRecord) {
  await updateExternalSubmissionDecision(record, "renamed", pageText.privacy.blockDecisionRenamed);
}

async function archiveExternalSubmission(record: ExternalSubmissionBlockRecord) {
  await updateExternalSubmissionDecision(record, "archived", pageText.privacy.blockDecisionArchived);
}

async function overrideExternalSubmission(record: ExternalSubmissionBlockRecord) {
  try {
    const reason = await ElMessageBox.prompt(
      pageText.privacy.blockOverridePrompt,
      pageText.privacy.blockOverrideTitle,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        inputPattern: /.+/,
        inputErrorMessage: pageText.privacy.blockOverrideReasonRequired,
        type: "warning",
      },
    );
    await updateExternalSubmissionDecision(
      record,
      "override_submitted",
      pageText.privacy.blockDecisionOverride,
      reason.value,
    );
  } catch {
    // 用户取消确认时不提示错误。
  }
}

function normalizeAiProfileId(raw: string) {
  return raw.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
}

function buildAiProfileDraft(overrides: Partial<AiProviderProfile> = {}): AiProviderProfile {
  const timeoutMs = Math.max(5000, Number(form.aiTimeoutSeconds || 30) * 1000);
  const maxRetries = Math.max(0, Number(form.aiMaxRetries || 0));
  const profileId = normalizeAiProfileId(overrides.id || form.aiProfileId || form.aiProfileName || form.aiModel || form.aiProvider || "default");
  return {
    id: profileId || "default",
    name: (overrides.name || form.aiProfileName || form.aiModel || pageText.ai.defaultProfileName).trim(),
    provider: overrides.provider || form.aiProvider,
    model: (overrides.model || form.aiModel || "deepseek-chat").trim(),
    api_key: overrides.api_key ?? form.aiApiKey.trim() ?? "",
    has_secret: overrides.has_secret,
    base_url: (overrides.base_url || form.aiBaseUrl || "https://api.deepseek.com").trim(),
    timeout_ms: overrides.timeout_ms ?? timeoutMs,
    max_retries: overrides.max_retries ?? maxRetries,
    enabled: overrides.enabled ?? true,
  };
}

function mergeAiProfiles(nextProfile: AiProviderProfile) {
  const profiles = [...savedAiProfiles.value];
  const index = profiles.findIndex((profile) => profile.id === nextProfile.id);
  if (index >= 0) {
    profiles[index] = { ...profiles[index], ...nextProfile };
  } else {
    profiles.push(nextProfile);
  }
  return profiles;
}

function loadAiProfile(profile: AiProviderProfile) {
  form.aiProfileId = profile.id;
  form.aiProfileName = profile.name;
  form.aiProvider = profile.provider;
  form.aiModel = profile.model;
  form.aiApiKey = "";
  form.aiBaseUrl = profile.base_url;
  form.aiTimeoutSeconds = String(Math.max(5, Math.round(profile.timeout_ms / 1000)));
  form.aiMaxRetries = String(profile.max_retries);
}

function createAiProfile() {
  form.aiProfileId = "";
  form.aiProfileName = "";
  form.aiProvider = "deepseek";
  form.aiModel = "deepseek-chat";
  form.aiApiKey = "";
  form.aiBaseUrl = "https://api.deepseek.com";
  form.aiTimeoutSeconds = "30";
  form.aiMaxRetries = "2";
}

async function activateAiProfile(profile: AiProviderProfile) {
  try {
    await settingsStore.saveSettings({
      "ai.enabled": form.aiEnabled,
      "ai.active_profile_id": profile.id,
      "ai.provider_profiles": savedAiProfiles.value,
      "ai.provider": profile.provider,
      "ai.model": profile.model,
      "ai.base_url": profile.base_url,
      "ai.timeout_ms": profile.timeout_ms,
      "ai.max_retries": profile.max_retries,
    });
    syncForm();
    resetAiTestState();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function deleteAiProfile(profile: AiProviderProfile) {
  try {
    await ElMessageBox.confirm(
      formatMessage(pageText.ai.deleteConfirm, { name: profile.name }),
      pageText.ai.deleteTitle,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        type: "warning",
      },
    );
  } catch {
    return;
  }

  try {
    const remaining = savedAiProfiles.value.filter((item) => item.id !== profile.id);
    const nextActive = remaining[0];
    const values: Record<string, unknown> = {
      "ai.enabled": form.aiEnabled,
      "ai.provider_profiles": remaining,
      "ai.active_profile_id": nextActive?.id || "default",
    };
    if (nextActive) {
      values["ai.provider"] = nextActive.provider;
      values["ai.model"] = nextActive.model;
      values["ai.base_url"] = nextActive.base_url;
      values["ai.timeout_ms"] = nextActive.timeout_ms;
      values["ai.max_retries"] = nextActive.max_retries;
    }
    await settingsStore.saveSettings(values);
    syncForm();
    resetAiTestState();
    if (!nextActive) {
      createAiProfile();
    }
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
  }
}

async function saveAiSettings(options: { rethrow?: boolean } = {}) {
  try {
    const timeoutSeconds = Math.max(5, Number(form.aiTimeoutSeconds || 30));
    const maxRetries = Math.max(0, Number(form.aiMaxRetries || 0));
    form.aiTimeoutSeconds = String(timeoutSeconds);
    form.aiMaxRetries = String(maxRetries);
    const nextProfile = buildAiProfileDraft({
      api_key: form.aiApiKey.trim(),
      timeout_ms: timeoutSeconds * 1000,
      max_retries: maxRetries,
    });
    form.aiProfileId = nextProfile.id;
    form.aiProfileName = nextProfile.name;
    const values: Record<string, unknown> = {
      "ai.enabled": form.aiEnabled,
      "ai.active_profile_id": nextProfile.id,
      "ai.provider_profiles": mergeAiProfiles(nextProfile),
      "ai.provider": nextProfile.provider,
      "ai.model": nextProfile.model,
      "ai.base_url": nextProfile.base_url,
      "ai.timeout_ms": nextProfile.timeout_ms,
      "ai.max_retries": nextProfile.max_retries,
    };
    if (nextProfile.api_key) {
      values["ai.api_key"] = nextProfile.api_key;
    }
    await settingsStore.saveSettings(values);
    syncForm();
    resetAiTestState();
    ElMessage.success(pageText.saved);
  } catch (error) {
    await notifySaveFailed(error);
    if (options.rethrow) {
      throw error;
    }
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

const aiTestResult = ref<AiConnectionTestResult | null>(null);
const aiTestResultDialogVisible = ref(false);
const aiLastTestTime = ref("");
const savedAiSnapshot = ref<Record<string, unknown> | null>(null);
const testingAi = ref(false);
const aiTestingElapsedSeconds = ref(0);
const aiTestResultModalAutoOpen = ref(false);
let aiTestingTimer: ReturnType<typeof window.setInterval> | undefined;
let aiStatusPanelScrollTop: number | null = null;
let aiStatusPanelElement: HTMLElement | null = null;

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

function buildAiFormSnapshot(): Record<string, unknown> {
  const timeoutSeconds = Math.min(120, Math.max(5, Number(form.aiTimeoutSeconds || 30)));
  const maxRetries = Math.min(10, Math.max(0, Number(form.aiMaxRetries || 0)));
  const nextProfile = buildAiProfileDraft({
    api_key: form.aiApiKey.trim() || selectedSavedAiProfile.value?.api_key || "",
    timeout_ms: timeoutSeconds * 1000,
    max_retries: maxRetries,
  });
  return {
    "ai.enabled": form.aiEnabled,
    "ai.active_profile_id": nextProfile.id,
    "ai.provider_profiles": mergeAiProfiles(nextProfile),
    "ai.provider": nextProfile.provider,
    "ai.model": nextProfile.model,
    "ai.api_key": form.aiApiKey.trim() || savedAiSnapshot.value?.["ai.api_key"] || { configured: false, fingerprint: "" },
    "ai.base_url": nextProfile.base_url,
    "ai.timeout_ms": nextProfile.timeout_ms,
    "ai.max_retries": nextProfile.max_retries,
  };
}

function resetAiTestState() {
  aiTestResult.value = null;
  aiLastTestTime.value = "";
  aiTestResultDialogVisible.value = false;
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

const aiStatusBar = computed(() => {
  if (testingAi.value) {
    return {
      icon: "⏳",
      text: `AI ${pageText.ai.testing} ${pageText.ai.waited} ${aiTestingElapsedSeconds.value} ${pageText.ai.seconds}`,
      time: "",
      tone: "testing",
    };
  }

  if (!aiTestResult.value) {
    return {
      icon: "⚪",
      text: pageText.ai.testStatusUntested,
      time: pageText.ai.noTestRecord,
      tone: "unknown",
    };
  }

  const success = aiTestResult.value.status === "success";
  return {
    icon: success ? "🟢" : "🔴",
    text: success ? pageText.ai.testSuccess : pageText.ai.testFailed,
    time: aiLastTestTime.value || pageText.ai.noTestRecord,
    tone: success ? "success" : "danger",
  };
});

function aiResponseTimeText(result: AiConnectionTestResult | null) {
  if (typeof result?.response_ms === "number") {
    return `${Math.round(result.response_ms)} ms`;
  }
  return pageText.ai.responseTimeUnavailable;
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
  return formatDateTime(date.toISOString());
}

function formatTestedAt(value?: string) {
  if (!value) {
    return "";
  }
  const formatted = formatDateTime(value);
  return formatted === "-" ? value : formatted;
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

function startAiTestingProgress() {
  stopAiTestingProgress();
  testingAi.value = true;
  aiTestingElapsedSeconds.value = 0;
  aiTestingTimer = window.setInterval(() => {
    aiTestingElapsedSeconds.value += 1;
  }, 1000);
}

function stopAiTestingProgress() {
  if (aiTestingTimer !== undefined) {
    window.clearInterval(aiTestingTimer);
    aiTestingTimer = undefined;
  }
  testingAi.value = false;
  aiTestingElapsedSeconds.value = 0;
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

async function executeAiTest() {
  aiTestResultDialogVisible.value = false;
  aiTestResultModalAutoOpen.value = false;
  startAiTestingProgress();
  try {
    const result = await settingsStore.testAiSettings();
    aiTestResult.value = result;
    savedAiSnapshot.value = result.config_snapshot ?? savedAiSnapshot.value;
    aiLastTestTime.value = formatTestedAt(result.tested_at) || formatClock(new Date());
  } catch (error) {
    aiTestResult.value = {
      status: "failed",
      message: error instanceof Error ? error.message : String(error),
      provider: form.aiProvider,
      model: form.aiModel,
      effective: "none",
      response_ms: null,
    };
    aiLastTestTime.value = formatClock(new Date());
  } finally {
    stopAiTestingProgress();
  }
  aiTestResultModalAutoOpen.value = true;
  aiTestResultDialogVisible.value = true;
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

async function testAiConnection() {
  if (snapshotsEqual(buildAiFormSnapshot(), savedAiSnapshot.value)) {
    await executeAiTest();
    return;
  }

  try {
    await ElMessageBox.confirm(
      pageText.ai.unsavedConfirm,
      pageText.tmdb.unsavedConfirmTitle,
      {
        confirmButtonText: messages.common.confirm,
        cancelButtonText: messages.common.cancel,
        type: "warning",
      },
    );
    try {
      await saveAiSettings({ rethrow: true });
      savedAiSnapshot.value = null;
      const history = await settingsStore.loadAiTestResult({ silent: true });
      savedAiSnapshot.value = history.current_snapshot;
      await executeAiTest();
    } catch {
      ElMessage.error(pageText.ai.saveBeforeTestFailed);
    }
  } catch {
    await executeAiTest();
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

async function openAiTestDetail() {
  if (testingAi.value || aiTestResultModalAutoOpen.value) {
    return;
  }
  if (!aiTestResult.value) {
    try {
      const history = await settingsStore.loadAiTestResult({ silent: true });
      savedAiSnapshot.value = history.current_snapshot;
      if (history.result) {
        aiTestResult.value = history.result;
        aiLastTestTime.value = formatTestedAt(history.result.tested_at);
      }
    } catch {
      // Keep the click behavior focused on the status bar message.
    }
  }
  if (!aiTestResult.value) {
    ElMessage.info(pageText.ai.noTestRecordHint);
    return;
  }
  aiTestResultDialogVisible.value = true;
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

async function handleAiStatusClick() {
  try {
    await openAiTestDetail();
  } finally {
    restoreAiStatusPanelScroll();
  }
}

function handleAiTestDialogClosed() {
  aiTestResultModalAutoOpen.value = false;
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

function rememberAiStatusPanelScroll(event: Event) {
  const panel = (event.currentTarget as HTMLElement | null)?.closest(".settings-panel") as HTMLElement | null;
  aiStatusPanelElement = panel;
  aiStatusPanelScrollTop = panel?.scrollTop ?? null;
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

function restoreAiStatusPanelScroll() {
  const panel = aiStatusPanelElement;
  const scrollTop = aiStatusPanelScrollTop;
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
  try {
    const history = await settingsStore.loadAiTestResult({ silent: true });
    savedAiSnapshot.value = history.current_snapshot;
    if (history.result && history.matches_current) {
      aiTestResult.value = history.result;
      aiLastTestTime.value = formatTestedAt(history.result.tested_at);
    }
  } catch {
    // The page can still be edited and saved if historical AI test loading fails.
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

        <el-form v-else-if="activeCategory === 'privacy'" label-position="top" class="settings-form">
          <div class="settings-config-card">
            <div class="settings-config-card-title">{{ pageText.privacy.title }}</div>
            <el-alert
              :title="pageText.privacy.notice"
              type="info"
              :closable="false"
              show-icon
            />

            <div class="settings-inline-row">
              <span class="settings-inline-label">{{ pageText.privacy.defaultEnabled }}</span>
              <el-switch
                v-model="form.defaultSensitiveWordsEnabled"
                :active-text="messages.status.enabled"
                :inactive-text="messages.status.disabled"
              />
              <span class="settings-inline-label">{{ pageText.privacy.effectiveCount }}</span>
              <el-tag type="warning" effect="light">
                {{ formatMessage(pageText.privacy.effectiveCountValue, { count: sensitiveWordTotal }) }}
              </el-tag>
            </div>

            <el-form-item :label="pageText.privacy.defaultWords" class="settings-sensitive-textarea">
              <el-input
                v-model="form.defaultSensitiveWordsText"
                type="textarea"
                :rows="4"
                :placeholder="pageText.privacy.defaultWordsPlaceholder"
              />
              <span class="setting-source">{{ pageText.privacy.defaultWordsHelp }}</span>
            </el-form-item>

            <div class="settings-word-preview">
              <div class="settings-word-preview-heading">
                <button
                  type="button"
                  class="settings-word-preview-link"
                  @click="defaultSensitiveWordsDialogVisible = true"
                >
                  {{ pageText.privacy.defaultPreview }}
                </button>
                <div class="settings-inline-actions">
                  <el-button type="warning" plain size="small" @click="resetDefaultSensitiveWords">
                    {{ pageText.privacy.defaultWordsReset }}
                  </el-button>
                  <el-button size="small" :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
                    {{ messages.common.refresh }}
                  </el-button>
                  <el-button type="primary" size="small" :loading="settingsStore.loading" @click="savePrivacySettings">
                    {{ messages.common.save }}
                  </el-button>
                </div>
              </div>
              <div class="settings-word-tags">
                <el-tag
                  v-for="word in visibleDefaultSensitiveWords"
                  :key="`default-${word}`"
                  type="warning"
                  effect="light"
                >
                  {{ word }}
                </el-tag>
                <el-tag v-if="defaultSensitiveWordOverflowCount > 0" type="warning" effect="plain">
                  {{ formatMessage(pageText.privacy.defaultWordsTotal, { count: defaultSensitiveWordPreview.length }) }}
                </el-tag>
                <span v-if="defaultSensitiveWordPreview.length === 0" class="settings-word-empty">
                  {{ pageText.privacy.emptyWords }}
                </span>
              </div>
            </div>

            <div class="settings-config-card settings-nested-card" :class="{ 'is-collapsed': customSensitiveWordsCollapsed }">
              <button
                type="button"
                class="settings-config-card-title settings-config-card-toggle"
                @click="customSensitiveWordsCollapsed = !customSensitiveWordsCollapsed"
              >
                <span>{{ pageText.privacy.customWords }}</span>
                <span class="settings-collapse-icon" aria-hidden="true">
                  {{ customSensitiveWordsCollapsed ? "+" : "-" }}
                </span>
              </button>
              <template v-if="!customSensitiveWordsCollapsed">
            <el-form-item :label="pageText.privacy.customWords" class="settings-sensitive-textarea">
              <el-input
                v-model="form.customSensitiveWordsText"
                type="textarea"
                :rows="4"
                :placeholder="pageText.privacy.customWordsPlaceholder"
              />
              <span class="setting-source">{{ pageText.privacy.customWordsHelp }}</span>
            </el-form-item>

            <div class="settings-word-preview">
              <span>{{ pageText.privacy.customPreview }}</span>
              <div class="settings-word-tags">
                <el-tag
                  v-for="word in customSensitiveWordPreview"
                  :key="`custom-${word}`"
                  type="info"
                  effect="light"
                >
                  {{ word }}
                </el-tag>
                <span v-if="customSensitiveWordPreview.length === 0" class="settings-word-empty">
                  {{ pageText.privacy.emptyWords }}
                </span>
              </div>
            </div>
              </template>
            </div>

            <div class="settings-config-card settings-block-record-card">
              <div class="settings-word-preview-heading">
                <div>
                  <div class="settings-config-card-title">{{ pageText.privacy.blockRecords }}</div>
                  <span class="setting-source">
                    {{ formatMessage(pageText.privacy.blockRecordsTotal, { count: externalSubmissionBlockTotal }) }}
                  </span>
                </div>
                <el-button
                  size="small"
                  :loading="externalSubmissionBlocksLoading"
                  @click="loadExternalSubmissionBlocks"
                >
                  {{ messages.common.refresh }}
                </el-button>
              </div>
              <el-table
                v-loading="externalSubmissionBlocksLoading"
                :data="externalSubmissionBlocks"
                size="small"
                class="settings-block-record-table"
                :empty-text="pageText.privacy.blockRecordsEmpty"
              >
                <el-table-column prop="file_name" :label="pageText.privacy.blockFileName" min-width="180" show-overflow-tooltip />
                <el-table-column prop="target_service" :label="pageText.privacy.blockTargetService" width="100" />
                <el-table-column prop="matched_value_masked" :label="pageText.privacy.blockMatchedValue" width="130" />
                <el-table-column :label="pageText.privacy.blockStatus" width="120">
                  <template #default="{ row }">
                    <el-tag :type="externalSubmissionStatusType(row.status)" effect="light">
                      {{ externalSubmissionStatusText(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="created_at" :label="pageText.privacy.blockCreatedAt" width="170">
                  <template #default="{ row }">
                    {{ formatTestedAt(row.created_at) }}
                  </template>
                </el-table-column>
                <el-table-column prop="user_decision" :label="pageText.privacy.blockDecision" min-width="160" show-overflow-tooltip />
                <el-table-column :label="pageText.privacy.blockActions" width="300" fixed="right">
                  <template #default="{ row }">
                    <div class="settings-block-record-actions">
                      <el-button size="small" text type="success" @click="markExternalSubmissionRenamed(row)">
                        {{ pageText.privacy.blockMarkRenamed }}
                      </el-button>
                      <el-button size="small" text type="info" @click="markExternalSubmissionIgnored(row)">
                        {{ pageText.privacy.blockIgnore }}
                      </el-button>
                      <el-button size="small" text type="warning" @click="overrideExternalSubmission(row)">
                        {{ pageText.privacy.blockOverride }}
                      </el-button>
                      <el-button size="small" text @click="archiveExternalSubmission(row)">
                        {{ pageText.privacy.blockArchive }}
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <el-dialog
              v-model="defaultSensitiveWordsDialogVisible"
              :title="pageText.privacy.defaultWordsAllTitle"
              width="min(720px, 92vw)"
            >
              <div class="settings-word-dialog-list">
                <el-tag
                  v-for="word in defaultSensitiveWordPreview"
                  :key="`all-default-${word}`"
                  type="warning"
                  effect="light"
                >
                  {{ word }}
                </el-tag>
                <span v-if="defaultSensitiveWordPreview.length === 0" class="settings-word-empty">
                  {{ pageText.privacy.emptyWords }}
                </span>
              </div>
              <template #footer>
                <el-button @click="defaultSensitiveWordsDialogVisible = false">{{ messages.common.close }}</el-button>
              </template>
            </el-dialog>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'ai'" label-position="top" class="settings-form">
          <div class="settings-config-card">
            <div class="settings-config-card-title">{{ pageText.ai.title }}</div>
            <el-alert
              :title="pageText.ai.notice"
              type="info"
              :closable="false"
              show-icon
            />
            <div class="settings-sub-card">
              <div class="settings-word-preview-heading">
                <div>
                  <div class="settings-config-card-title">{{ pageText.ai.profileList }}</div>
                  <span class="setting-source">{{ pageText.ai.profileHint }}</span>
                </div>
                <el-button size="small" @click="createAiProfile">
                  {{ pageText.ai.newProfile }}
                </el-button>
              </div>
              <el-table
                :data="savedAiProfiles"
                size="small"
                class="settings-block-record-table"
                :empty-text="pageText.ai.profileEmpty"
              >
                <el-table-column prop="name" :label="pageText.ai.profileName" min-width="140" show-overflow-tooltip />
                <el-table-column prop="provider" :label="pageText.ai.provider" width="150" />
                <el-table-column prop="model" :label="pageText.ai.model" min-width="160" show-overflow-tooltip />
                <el-table-column prop="base_url" :label="pageText.ai.baseUrl" min-width="180" show-overflow-tooltip />
                <el-table-column :label="pageText.ai.currentProfile" width="90">
                  <template #default="{ row }">
                    <el-tag v-if="row.id === settingValue('ai.active_profile_id', 'default')" type="success" effect="light">
                      {{ pageText.ai.currentProfileTag }}
                    </el-tag>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column :label="pageText.ai.profileActions" width="220" fixed="right">
                  <template #default="{ row }">
                    <div class="settings-block-record-actions">
                      <el-button size="small" text type="primary" @click="loadAiProfile(row)">
                        {{ pageText.ai.editProfile }}
                      </el-button>
                      <el-button size="small" text type="success" @click="activateAiProfile(row)">
                        {{ pageText.ai.activateProfile }}
                      </el-button>
                      <el-button size="small" text type="danger" @click="deleteAiProfile(row)">
                        {{ pageText.ai.deleteProfile }}
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <div class="settings-grid">
              <el-form-item :label="pageText.ai.profileId">
                <el-input v-model="form.aiProfileId" :placeholder="pageText.ai.profileIdPlaceholder" />
              </el-form-item>
              <el-form-item :label="pageText.ai.profileName">
                <el-input v-model="form.aiProfileName" :placeholder="pageText.ai.profileNamePlaceholder" />
              </el-form-item>
              <el-form-item :label="pageText.ai.enabled">
                <el-switch
                  v-model="form.aiEnabled"
                  :active-text="messages.status.enabled"
                  :inactive-text="messages.status.disabled"
                />
              </el-form-item>
              <el-form-item :label="pageText.ai.provider">
                <el-select v-model="form.aiProvider">
                  <el-option :label="pageText.ai.providerDeepSeek" value="deepseek" />
                  <el-option :label="pageText.ai.providerOpenAiCompatible" value="openai_compatible" />
                  <el-option :label="pageText.ai.providerCustom" value="custom" />
                </el-select>
              </el-form-item>
              <el-form-item :label="pageText.ai.model">
                <el-input v-model="form.aiModel" :placeholder="pageText.ai.modelPlaceholder" />
              </el-form-item>
              <el-form-item :label="pageText.ai.apiKey">
                <el-input
                  v-model="form.aiApiKey"
                  show-password
                  autocomplete="off"
                  :placeholder="aiApiKeyPlaceholder"
                />
              </el-form-item>
              <el-form-item :label="pageText.ai.baseUrl">
                <el-input v-model="form.aiBaseUrl" />
              </el-form-item>
              <el-form-item :label="pageText.ai.timeout">
                <el-input v-model="form.aiTimeoutSeconds" maxlength="4" @input="onlyDigits('aiTimeoutSeconds')">
                  <template #append>{{ pageText.ai.seconds }}</template>
                </el-input>
              </el-form-item>
              <el-form-item :label="pageText.ai.maxRetries">
                <el-input v-model="form.aiMaxRetries" maxlength="2" @input="onlyDigits('aiMaxRetries')">
                  <template #append>{{ pageText.ai.retryUnit }}</template>
                </el-input>
              </el-form-item>
            </div>
            <div class="settings-actions">
              <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
                {{ messages.common.refresh }}
              </el-button>
              <el-button :loading="settingsStore.loading || testingAi" @click="testAiConnection">
                {{ pageText.ai.testConnection }}
              </el-button>
              <el-button type="primary" :loading="settingsStore.loading" @click="saveAiSettings">
                {{ messages.common.save }}
              </el-button>
            </div>
            <div
              role="button"
              class="settings-test-status-bar"
              :class="[`is-${aiStatusBar.tone}`, { 'is-disabled': testingAi || aiTestResultModalAutoOpen }]"
              :aria-disabled="Boolean(testingAi || aiTestResultModalAutoOpen)"
              @pointerdown.prevent="rememberAiStatusPanelScroll"
              @mousedown.prevent="rememberAiStatusPanelScroll"
              @click="handleAiStatusClick"
            >
              <span class="settings-test-status-state">
                <span class="settings-test-status-icon">{{ aiStatusBar.icon }}</span>
                <span class="settings-test-status-text">{{ aiStatusBar.text }}</span>
              </span>
              <template v-if="aiStatusBar.time">
                <span class="settings-test-separator">·</span>
                <span class="settings-test-status-meta">{{ aiTestResult ? pageText.ai.updatedAt + aiStatusBar.time : aiStatusBar.time }}</span>
              </template>
            </div>

            <el-dialog
              v-model="aiTestResultDialogVisible"
              :title="pageText.ai.testDetailTitle"
              width="min(540px, calc(100vw - 2rem))"
              align-center
              @closed="handleAiTestDialogClosed"
            >
              <div v-if="aiTestResult" class="settings-test-detail">
                <div class="settings-test-detail-row">
                  <div class="settings-test-detail-heading">
                    <strong>AI</strong>
                    <el-tag :type="channelStatusType(aiTestResult.status)" effect="light">
                      {{ aiTestResult.status === "success" ? pageText.ai.testSuccess : pageText.ai.testFailed }}
                    </el-tag>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.ai.providerLabel }}</span>
                    <strong>{{ aiTestResult.provider || form.aiProvider }}</strong>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.ai.modelLabel }}</span>
                    <strong>{{ aiTestResult.model || form.aiModel }}</strong>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.ai.responseTime }}</span>
                    <strong>{{ aiResponseTimeText(aiTestResult) }}</strong>
                  </div>
                  <div class="settings-test-detail-item">
                    <span>{{ pageText.ai.detailMessage }}</span>
                    <strong>{{ aiTestResult.message || (aiTestResult.status === "success" ? pageText.ai.testSuccess : pageText.ai.testFailed) }}</strong>
                  </div>
                </div>
                <div class="settings-test-summary">
                  <span>{{ pageText.ai.effectiveStatus }}</span>
                  <strong>{{ aiTestResult.status === "success" ? pageText.ai.testSuccess : pageText.ai.testFailed }}</strong>
                </div>
              </div>
              <template #footer>
                <el-button @click="aiTestResultDialogVisible = false">{{ messages.common.close }}</el-button>
              </template>
            </el-dialog>
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
          <input
            ref="namingImportInput"
            class="naming-import-input"
            type="file"
            accept="application/json,.json"
            @change="importNamingTemplateFile"
          />
          <div class="naming-workbench-panel">
            <div class="naming-workbench-header">
              <div class="naming-version-list">
                <div v-for="item in namingTemplateMetaList" :key="item.key" class="naming-version-item">
                  <span class="naming-version-label">{{ item.label }}</span>
                  <el-tag size="small" effect="plain">v{{ item.version }}</el-tag>
                  <span class="naming-version-time">{{ namingTemplateUpdatedText(item.updatedAt) }}</span>
                </div>
              </div>
              <div class="settings-actions naming-workbench-actions">
                <el-button :loading="settingsStore.loading" @click="openNamingWorkbench('test')">
                  {{ pageText.naming.testRules }}
                </el-button>
                <el-button :loading="settingsStore.loading" @click="openNamingWorkbench('diff')">
                  {{ pageText.naming.diffPreview }}
                </el-button>
                <el-button :loading="settingsStore.loading" @click="triggerNamingTemplateImport">
                  {{ pageText.naming.importTemplate }}
                </el-button>
                <el-button :loading="settingsStore.loading" @click="exportCurrentNamingTemplateBundle">
                  {{ pageText.naming.exportTemplate }}
                </el-button>
              </div>
            </div>
          </div>
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
            <el-form-item :label="pageText.naming.titleRecognitionMode">
              <el-select v-model="form.titleRecognitionMode">
                <el-option :label="pageText.naming.recognitionModes.fileNameFirst" value="file_name_first" />
                <el-option :label="pageText.naming.recognitionModes.parentFolderFallback" value="parent_folder_fallback" />
                <el-option :label="pageText.naming.recognitionModes.parentFolderFirst" value="parent_folder_first" />
                <el-option :label="pageText.naming.recognitionModes.manualOnly" value="manual_only" />
              </el-select>
            </el-form-item>
            <el-form-item :label="pageText.naming.keepYear">
              <el-switch v-model="form.keepYear" />
            </el-form-item>
            <el-form-item :label="pageText.naming.cleanIllegalChars">
              <el-switch v-model="form.cleanIllegalChars" disabled />
            </el-form-item>
          </div>
          <el-dialog
            v-model="namingWorkbenchDialogVisible"
            :title="namingWorkbenchDialogTitle"
            width="min(760px, calc(100vw - 2rem))"
            align-center
          >
            <div class="naming-workbench-dialog">
              <div class="naming-workbench-type-row">
                <span class="naming-workbench-type-label">{{ pageText.naming.applicableType }}</span>
                <el-segmented
                  v-model="namingWorkbenchForm.mediaType"
                  :options="[
                    { label: pageText.naming.movie, value: 'movie' },
                    { label: pageText.naming.episode, value: 'episode' },
                  ]"
                />
              </div>
              <div class="settings-grid naming-workbench-grid">
                <el-form-item :label="pageText.naming.elements.title">
                  <el-input v-model="namingWorkbenchForm.title" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.chineseTitle">
                  <el-input v-model="namingWorkbenchForm.chineseTitle" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.englishTitle">
                  <el-input v-model="namingWorkbenchForm.englishTitle" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.originalTitle">
                  <el-input v-model="namingWorkbenchForm.originalTitle" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.year">
                  <el-input v-model="namingWorkbenchForm.year" maxlength="4" />
                </el-form-item>
                <el-form-item v-if="namingWorkbenchForm.mediaType === 'episode'" :label="pageText.naming.elements.season">
                  <el-input v-model="namingWorkbenchForm.season" maxlength="2" />
                </el-form-item>
                <el-form-item v-if="namingWorkbenchForm.mediaType === 'episode'" :label="pageText.naming.elements.episode">
                  <el-input v-model="namingWorkbenchForm.episode" maxlength="3" />
                </el-form-item>
                <el-form-item :label="pageText.naming.extension">
                  <el-input v-model="namingWorkbenchForm.extension" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.resolution">
                  <el-input v-model="namingWorkbenchForm.resolution" />
                </el-form-item>
                <el-form-item :label="pageText.naming.elements.source">
                  <el-input v-model="namingWorkbenchForm.source" />
                </el-form-item>
              </div>
              <div
                v-if="namingWorkbenchResult || namingWorkbenchDiffResult"
                class="naming-workbench-result"
              >
                <template v-if="namingWorkbenchMode === 'diff' && namingWorkbenchDiffResult">
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.currentGeneratedName }}</span>
                    <strong>{{ namingWorkbenchDiffResult.current_generated_name }}</strong>
                  </div>
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.candidateGeneratedName }}</span>
                    <strong>{{ namingWorkbenchDiffResult.candidate_generated_name }}</strong>
                  </div>
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.changed }}</span>
                    <strong>{{ namingWorkbenchDiffResult.changed ? pageText.naming.changed : pageText.naming.unchanged }}</strong>
                  </div>
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.templateVersion }}</span>
                    <strong>v{{ namingWorkbenchDiffResult.template_version }}</strong>
                  </div>
                </template>
                <template v-else-if="namingWorkbenchResult">
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.generatedName }}</span>
                    <strong>{{ namingWorkbenchResult.generated_name }}</strong>
                  </div>
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.templateVersion }}</span>
                    <strong>v{{ namingWorkbenchResult.template_version }}</strong>
                  </div>
                  <div class="naming-result-row">
                    <span>{{ pageText.naming.templateUpdatedAt }}</span>
                    <strong>{{ namingTemplateUpdatedText(namingWorkbenchResult.template_updated_at) }}</strong>
                  </div>
                </template>
              </div>
              <div v-else class="naming-workbench-empty">
                {{ pageText.naming.noPreviewYet }}
              </div>
            </div>
            <template #footer>
              <el-button @click="namingWorkbenchDialogVisible = false">{{ messages.common.close }}</el-button>
              <el-button type="primary" :loading="namingWorkbenchLoading" @click="runNamingWorkbench">
                {{ namingWorkbenchMode === 'diff' ? pageText.naming.runDiff : pageText.naming.runTest }}
              </el-button>
            </template>
          </el-dialog>
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

<style scoped>
.naming-import-input {
  display: none;
}

.naming-workbench-panel {
  margin-top: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--el-fill-color-blank);
}

.naming-workbench-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.naming-version-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.naming-version-item {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.naming-version-label {
  color: var(--el-text-color-primary);
  font-weight: 600;
}

.naming-version-time {
  color: var(--el-text-color-secondary);
}

.naming-workbench-actions {
  margin-left: auto;
}

.naming-workbench-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.naming-workbench-type-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.naming-workbench-type-label {
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.naming-workbench-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.naming-workbench-result,
.naming-workbench-empty {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--el-fill-color-light);
}

.naming-workbench-empty {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.naming-result-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.naming-result-row + .naming-result-row {
  margin-top: 8px;
}

.naming-result-row span {
  color: var(--el-text-color-regular);
  min-width: 112px;
}

.naming-result-row strong {
  color: var(--el-text-color-primary);
  font-weight: 600;
  word-break: break-all;
}
</style>
