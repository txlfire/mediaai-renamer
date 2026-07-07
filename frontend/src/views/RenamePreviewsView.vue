<script setup lang="ts">
import { ArrowDown, CloseBold, Connection, Delete, Edit, MagicStick, Search, Select } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import type {
  AiConnectionTestHistory,
  AiParseCandidate,
  AiParseResult,
  BatchAiParseResult,
  MetadataMatchResult,
  MetadataMatchSource,
  RenamePreview,
} from "../api/client";
import FullscreenTablePanel from "../components/FullscreenTablePanel.vue";
import ListPageLayout from "../components/ListPageLayout.vue";
import ListStatItem from "../components/ListStatItem.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { usePendingFileStore } from "../stores/pendingFiles";
import { usePreviewStore } from "../stores/preview";
import { useRenameOperationStore } from "../stores/renameOperation";
import { useSettingsStore } from "../stores/settings";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime } from "../utils/displayFormat";
import { formatFileSize } from "../utils/displayFormat";
import {
  canPrepareRename,
  findEmptyTargetNamePreviews,
  getRenameablePreviewIds,
  removeEmptyTargetNamePreviews,
} from "../utils/renameSelection";

const mediaStore = useMediaStore();
const previewStore = usePreviewStore();
const pendingFileStore = usePendingFileStore();
const renameOperationStore = useRenameOperationStore();
const settingsStore = useSettingsStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();

const editDialogVisible = ref(false);
const operationDialogVisible = ref(false);
const emptyTargetDialogVisible = ref(false);
const detailDialogVisible = ref(false);
const metadataDialogVisible = ref(false);
const aiParseDialogVisible = ref(false);
const pendingMoveDialogVisible = ref(false);
const editingPreviewId = ref<number | null>(null);
const selectedDetailRow = ref<RenamePreview | null>(null);
const metadataPreviewId = ref<number | null>(null);
const aiParsePreviewId = ref<number | null>(null);
const editingTargetName = ref("");
const selectedPreviewIds = ref<number[]>([]);
const selectedPreviewRows = ref<RenamePreview[]>([]);
const selectedPendingFileIds = ref<number[]>([]);
const pendingRenamePreviews = ref<RenamePreview[]>([]);
const metadataCandidates = ref<MetadataMatchResult[]>([]);
const aiParseResult = ref<AiParseResult | null>(null);
const aiParsingPreviewId = ref<number | null>(null);
const aiBatchResults = ref<Record<number, AiParseResult>>({});
const selectedMetadataFields = ref<string[]>(["title", "english_title", "year", "tmdb_id", "imdb_id"]);
const pendingMoveTargetDirectory = ref("");
const activePreviewTab = ref("previews");
const operationProgressVisible = ref(false);
const operationProgressText = ref("");
const operationProgressPercent = ref(0);
const operationProgressLogs = ref<string[]>([]);
const activeOperationName = ref("");
const operationResultVisible = ref(false);
const operationResultExpanded = ref(false);
const operationResultTitle = ref("");
const operationResultSuccess = ref(0);
const operationResultFailed = ref(0);
const operationResultSkipped = ref(0);
const operationResultLogs = ref<string[]>([]);
const metadataMatchSource = ref<MetadataMatchSource>("parsed_title");
const aiConnectionHistory = ref<AiConnectionTestHistory | null>(null);
const defaultSort = { prop: "id", order: "ascending" as const };
const pagedPreviews = computed(() =>
  paginationStore.paginate(
    "rename-previews",
    tableSortStore.applySort("rename-previews", previewStore.previews),
  ),
);
const pagedMetadataCandidates = computed(() =>
  paginationStore.paginate("metadata-candidates", metadataCandidates.value),
);
const metadataFieldOptions = computed(() => {
  const labels = messages.renamePreviews.metadataDialog.fields;
  return [
    "title",
    "chinese_title",
    "english_title",
    "original_title",
    "year",
    "tmdb_id",
    "imdb_id",
    "rating",
    "overview",
    "poster_path",
    "language",
    "genres",
    "cast",
    "director",
  ].map((value) => ({ value, label: labels[value as keyof typeof labels] || value }));
});
const previewStatItems = computed(() => [
  { label: messages.common.total, value: previewStore.stats.total, tone: "default" as const },
  { label: messages.status.generated, value: previewStore.stats.generated, tone: "success" as const },
  { label: messages.status.needsReview, value: previewStore.stats.needsReview, tone: "warning" as const },
  { label: messages.status.edited, value: previewStore.stats.edited, tone: "edited" as const },
  { label: messages.status.renamed, value: previewStore.stats.renamed, tone: "renamed" as const },
  { label: messages.status.noRename, value: previewStore.stats.noRename, tone: "success" as const },
  { label: messages.status.unableRename, value: previewStore.stats.unableRename, tone: "danger" as const },
]);
const visibleOperationResultLogs = computed(() =>
  operationResultExpanded.value ? operationResultLogs.value : operationResultLogs.value.slice(0, 8),
);
const hasPreviewFilter = computed(() =>
  Boolean(
    previewStore.filters.status ||
      previewStore.filters.media_type ||
      previewStore.filters.keyword?.trim(),
  ),
);
const shouldShowGeneratePreviewEmpty = computed(() =>
  Boolean(
    previewStore.filters.scan_job_id &&
      !hasPreviewFilter.value &&
      !previewStore.loading &&
      previewStore.previews.length === 0 &&
      mediaStore.mediaFiles.length > 0,
  ),
);

const sourceOptions = computed(() =>
  mediaStore.mediaSources.map((source) => ({
    label: source.name,
    value: source.id,
  })),
);

const scanJobOptions = computed(() =>
  mediaStore.scanJobs.map((job) => ({
    label: `${messages.common.taskLabel} ${job.id}`,
    value: job.id,
  })),
);

function mediaTypeLabel(value: string) {
  const labels: Record<string, string> = {
    movie: messages.renamePreviews.mediaTypes.movie,
    episode: messages.renamePreviews.mediaTypes.episode,
    unknown: messages.renamePreviews.mediaTypes.unknown,
  };
  return labels[value] ?? value;
}

function titleSourceLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.renamePreviews.titleSources;
  return value ? (labels[value] ?? value) : "-";
}

function recognitionModeLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.renamePreviews.recognitionModes;
  return value ? (labels[value] ?? value) : "-";
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    generated: messages.status.generated,
    edited: messages.status.edited,
    needs_review: messages.status.needsReview,
    renamed: messages.status.renamed,
    no_rename: messages.status.noRename,
    unable_rename: messages.status.unableRename,
    excluded: messages.status.excluded,
  };
  return labels[value] ?? value;
}

function statusTagType(value: string) {
  if (value === "generated" || value === "renamed" || value === "no_rename") {
    return "success";
  }
  if (value === "edited") {
    return "primary";
  }
  if (value === "needs_review") {
    return "warning";
  }
  if (value === "unable_rename") {
    return "danger";
  }
  if (value === "excluded") {
    return "info";
  }
  return "info";
}

function operationStatusLabel(value: string) {
  const labels: Record<string, string> = {
    ready: messages.status.ready,
    conflict: messages.status.conflict,
    renamed: messages.status.renamed,
    failed: messages.status.failed,
  };
  return labels[value] ?? value;
}

function operationStatusTagType(value: string) {
  if (value === "ready" || value === "renamed") {
    return "success";
  }
  if (value === "conflict") {
    return "warning";
  }
  if (value === "failed") {
    return "danger";
  }
  return "info";
}

function pendingReasonLabel(value: string) {
  const labels: Record<string, string> = messages.renamePreviews.pendingFiles.reasons;
  return labels[value] ?? value;
}

function metadataStatusLabel(value: string | null) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = messages.renamePreviews.metadataStatuses;
  return labels[value] ?? value;
}

function metadataStatusTagType(value: string | null) {
  if (value === "high_confidence" || value === "manual_selected") {
    return "success";
  }
  if (value === "low_confidence") {
    return "warning";
  }
  if (value === "failed") {
    return "danger";
  }
  return "info";
}

function aiParseStatusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.renamePreviews.aiParse.statuses;
  return value ? (labels[value] ?? value) : "-";
}

function aiParseStatusTagType(value: string | null | undefined) {
  if (value === "success") {
    return "success";
  }
  if (value === "blocked") {
    return "warning";
  }
  if (value === "failed") {
    return "danger";
  }
  return "info";
}

function metadataReason(row: RenamePreview) {
  return row.metadata_message || metadataStatusLabel(row.metadata_match_status);
}

function joinCandidateValues(values?: string[]) {
  return values?.length ? values.join(" / ") : "-";
}

function candidateValue(value: unknown) {
  if (Array.isArray(value)) {
    return joinCandidateValues(value.filter((item): item is string => typeof item === "string"));
  }
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function seasonEpisode(row: { season: number | null; episode: number | null }) {
  if (!row.season && !row.episode) {
    return "-";
  }
  return `S${String(row.season ?? 1).padStart(2, "0")}E${String(row.episode ?? 0).padStart(2, "0")}`;
}

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("rename-previews", event.prop, event.order);
}

function openDetailDialog(row: RenamePreview) {
  selectedDetailRow.value = row;
  detailDialogVisible.value = true;
}

const detailRows = computed(() => {
  const row = selectedDetailRow.value;
  if (!row) {
    return [];
  }
  return [
    { label: messages.common.status, value: statusLabel(row.status) },
    { label: messages.renamePreviews.columns.originalName, value: row.file_name },
    { label: messages.renamePreviews.columns.targetName, value: row.current_target_name },
    { label: messages.renamePreviews.columns.parsedTitle, value: row.parsed_title },
    { label: messages.renamePreviews.columns.titleSource, value: titleSourceLabel(row.title_source) },
    { label: messages.renamePreviews.columns.parentFolderTitle, value: row.parent_folder_title ?? "-" },
    { label: messages.renamePreviews.columns.recognitionMode, value: recognitionModeLabel(row.recognition_mode) },
    { label: messages.renamePreviews.columns.titleConflict, value: row.title_conflict_message ?? "-" },
    { label: messages.renamePreviews.columns.metadataSource, value: row.metadata_source ?? "-" },
    { label: messages.renamePreviews.columns.metadataScore, value: `${row.metadata_match_score ?? 0}%` },
    { label: messages.renamePreviews.columns.metadata, value: metadataStatusLabel(row.metadata_match_status) },
    { label: messages.renamePreviews.columns.reason, value: row.metadata_message ?? "-" },
    { label: messages.renamePreviews.type, value: mediaTypeLabel(row.media_type) },
    { label: messages.renamePreviews.columns.year, value: row.parsed_year ?? "-" },
    { label: messages.renamePreviews.columns.seasonEpisode, value: seasonEpisode(row) },
    { label: messages.renamePreviews.columns.updatedAt, value: formatDateTime(row.updated_at) },
    { label: messages.renamePreviews.columns.sourcePath, value: row.file_path },
  ];
});

const operationHasExecuted = computed(() => {
  const status = renameOperationStore.currentOperation?.status;
  return Boolean(status && status !== "dry_run");
});

const operationDialogTitle = computed(() =>
  operationHasExecuted.value
    ? messages.renamePreviews.operationDialog.resultTitle
    : messages.renamePreviews.operationDialog.title,
);

const operationResultAlert = computed(() => {
  const operation = renameOperationStore.currentOperation;
  if (!operation || !operationHasExecuted.value) {
    return null;
  }

  const summary = formatMessage(messages.renamePreviews.operationDialog.summary, {
    total: operation.total_count,
    renamed: operation.renamed_count,
    failed: operation.failed_count,
    conflict: operation.conflict_count,
  });

  if (operation.failed_count > 0 && operation.renamed_count > 0) {
    return {
      type: "warning" as const,
      title: messages.renamePreviews.operationDialog.partialFailed,
      description: `${summary} ${messages.renamePreviews.operationDialog.fixSuggestion}`,
    };
  }

  if (operation.failed_count > 0) {
    return {
      type: "error" as const,
      title: messages.renamePreviews.operationDialog.failed,
      description: `${summary} ${messages.renamePreviews.operationDialog.fixSuggestion}`,
    };
  }

  return {
    type: "success" as const,
    title: messages.renamePreviews.operationDialog.success,
    description: summary,
  };
});

async function refreshPreviews() {
  if (!previewStore.filters.scan_job_id) {
    previewStore.previews = [];
    mediaStore.mediaFiles = [];
    pendingFileStore.pendingFiles = [];
    return;
  }
  await previewStore.loadPreviews(previewStore.filters);
  await mediaStore.loadMediaFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  await pendingFileStore.loadPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
}

async function generatePreviews() {
  if (!previewStore.filters.scan_job_id) {
    return;
  }
  const count = Math.max(previewStore.previews.length, mediaStore.mediaFiles.length, 1);
  await confirmResourceOperation(messages.renamePreviews.generate, count);
  resetOperationProgress(count, messages.renamePreviews.generate);
  await previewStore.generatePreviews({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  const summary = previewStore.generationSummary;
  operationProgressLogs.value.push(
    formatMessage(messages.renamePreviews.operationSummary, {
      success: (summary?.generated_count ?? 0) + (summary?.needs_review_count ?? 0),
      failed: 0,
    }),
  );
  finishOperationProgress(
    count,
    (summary?.generated_count ?? 0) + (summary?.needs_review_count ?? 0),
    0,
  );
}

async function loadScanJobsForSelectedSource() {
  previewStore.filters.scan_job_id = undefined;
  previewStore.previews = [];
  mediaStore.mediaFiles = [];
  pendingFileStore.pendingFiles = [];
  if (!previewStore.filters.media_source_id) {
    mediaStore.scanJobs = [];
    return;
  }
  await mediaStore.loadScanJobs({ media_source_id: previewStore.filters.media_source_id });
}

async function resetRenamePreviews() {
  previewStore.filters = {};
  previewStore.previews = [];
  mediaStore.mediaFiles = [];
  pendingFileStore.pendingFiles = [];
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
  await mediaStore.loadMediaSources();
}

function handleSelectionChange(rows: RenamePreview[]) {
  selectedPreviewRows.value = rows;
  selectedPreviewIds.value = rows.map((row) => row.id);
}

function handlePendingSelectionChange(rows: Array<{ id: number }>) {
  selectedPendingFileIds.value = rows.map((row) => row.id);
}

function resetOperationProgress(total = 0, operation = "") {
  operationProgressVisible.value = total > 0;
  operationProgressPercent.value = total > 0 ? 5 : 0;
  activeOperationName.value = operation;
  operationProgressText.value = total > 0
    ? formatMessage(messages.renamePreviews.processing, { current: 0, total })
    : operation;
  operationProgressLogs.value = [];
}

function finishOperationProgress(total: number, success: number, failed: number, skipped = 0) {
  operationProgressPercent.value = 100;
  operationProgressText.value = formatMessage(messages.renamePreviews.processing, { current: total, total });
  operationResultTitle.value = formatMessage(messages.renamePreviews.operationResultDialog.completedTitle, {
    operation: activeOperationName.value,
  });
  operationResultSuccess.value = success;
  operationResultFailed.value = failed;
  operationResultSkipped.value = Math.max(skipped, total - success - failed);
  operationResultLogs.value = [...operationProgressLogs.value];
  operationResultExpanded.value = false;
  operationResultVisible.value = true;
  operationProgressVisible.value = false;
}

function operationButtonLabel(label: string, runningLabel: string) {
  if (operationProgressVisible.value && activeOperationName.value === label) {
    return formatMessage(messages.renamePreviews.operationResultDialog.runningButton, {
      operation: runningLabel,
      percent: operationProgressPercent.value,
    });
  }
  return label;
}

const aiBatchReady = computed(() =>
  Boolean(
    aiConnectionHistory.value?.result &&
      aiConnectionHistory.value.matches_current &&
      aiConnectionHistory.value.result.status === "success",
  ),
);

const aiBatchBlockMessage = computed(() => {
  if (!aiConnectionHistory.value?.result) {
    return messages.renamePreviews.aiParse.guardUntested;
  }
  if (!aiConnectionHistory.value.matches_current) {
    return messages.renamePreviews.aiParse.guardStale;
  }
  if (aiConnectionHistory.value.result.status !== "success") {
    return aiConnectionHistory.value.result.message || messages.renamePreviews.aiParse.guardFailed;
  }
  return "";
});

const aiBatchStatusLabel = computed(() => {
  if (aiBatchReady.value) {
    return "";
  }
  if (!aiConnectionHistory.value?.result) {
    return messages.renamePreviews.aiParse.statusUntested;
  }
  if (!aiConnectionHistory.value.matches_current) {
    return messages.renamePreviews.aiParse.statusStale;
  }
  return messages.renamePreviews.aiParse.statusFailed;
});

function aiProviderSummary() {
  const result = aiConnectionHistory.value?.result;
  if (!result) {
    return messages.renamePreviews.aiParse.providerUnavailable;
  }
  return formatMessage(messages.renamePreviews.aiParse.providerSummary, {
    profile: result.active_profile_name || result.active_profile_id || result.provider,
    provider: result.provider,
    model: result.model,
    baseUrl: result.base_url || "-",
  });
}

function ensureAiBatchReady() {
  if (aiBatchReady.value) {
    return true;
  }
  ElMessage.warning(aiBatchBlockMessage.value);
  return false;
}

async function handleOperationResultClosed() {
  operationResultExpanded.value = false;
  if (previewStore.filters.scan_job_id) {
    await refreshPreviews();
  }
}

async function confirmResourceOperation(operation: string, count: number) {
  await ElMessageBox.confirm(
    formatMessage(messages.renamePreviews.confirmOperation, { count, operation }),
    messages.renamePreviews.confirmOperationTitle,
    {
      type: "warning",
      confirmButtonText: messages.common.confirm,
      cancelButtonText: messages.common.cancel,
    },
  );
}

async function confirmAiBatchOperation(operation: string, count: number) {
  await ElMessageBox.confirm(
    formatMessage(messages.renamePreviews.aiParse.batchConfirm, {
      count,
      operation,
      providerSummary: aiProviderSummary(),
    }),
    messages.renamePreviews.aiParse.batchConfirmTitle,
    {
      type: "warning",
      confirmButtonText: messages.common.confirm,
      cancelButtonText: messages.common.cancel,
      dangerouslyUseHTMLString: false,
    },
  );
}

async function confirmAiFallbackOperation(count: number) {
  await ElMessageBox.confirm(
    formatMessage(messages.renamePreviews.aiParse.fallbackConfirm, {
      count,
      providerSummary: aiProviderSummary(),
    }),
    messages.renamePreviews.aiParse.batchConfirmTitle,
    {
      type: "warning",
      confirmButtonText: messages.common.confirm,
      cancelButtonText: messages.common.cancel,
      dangerouslyUseHTMLString: false,
    },
  );
}

async function runRenameDryRun() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await renameOperationStore.runDryRun(selectedPreviewIds.value);
  operationDialogVisible.value = true;
}

async function runRenameDryRunForPreviews(previews: RenamePreview[]) {
  const renameablePreviews = previews.filter(canPrepareRename);
  if (renameablePreviews.length === 0) {
    ElMessage.warning(messages.renamePreviews.noExecutable);
    return;
  }

  pendingRenamePreviews.value = renameablePreviews;
  if (findEmptyTargetNamePreviews(renameablePreviews).length > 0) {
    emptyTargetDialogVisible.value = true;
    return;
  }

  selectedPreviewIds.value = renameablePreviews.map((preview) => preview.id);
  await runRenameDryRun();
}

async function executeSelectedPreviews() {
  if (selectedPreviewRows.value.length > 0) {
    await confirmResourceOperation(messages.renamePreviews.rename, selectedPreviewRows.value.length);
  }
  await runRenameDryRunForPreviews(selectedPreviewRows.value);
}

async function executeAllPreviews() {
  const renameableIds = new Set(getRenameablePreviewIds(previewStore.previews));
  const renameablePreviews = previewStore.previews.filter((preview) => renameableIds.has(preview.id));
  if (renameablePreviews.length > 0) {
    await confirmResourceOperation(messages.renamePreviews.executeAll, renameablePreviews.length);
  }
  await runRenameDryRunForPreviews(renameablePreviews);
}

async function executeSinglePreview(row: RenamePreview) {
  await runRenameDryRunForPreviews([row]);
}

async function matchMetadata(row: RenamePreview) {
  await confirmResourceOperation(messages.renamePreviews.tmdbMatch, 1);
  metadataPreviewId.value = row.id;
  const updated = await previewStore.matchMetadata(row.id, metadataMatchSource.value);
  metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id, metadataMatchSource.value);
  if (updated.metadata_match_status && updated.metadata_match_status !== "failed") {
    ElMessage.success(messages.renamePreviews.metadataDialog.matched);
  }
  if (!metadataCandidates.value.length) {
    ElMessage.warning(updated.metadata_message || messages.renamePreviews.metadataDialog.empty);
  }
}

async function openMetadataBackfill(row: RenamePreview) {
  metadataPreviewId.value = row.id;
  metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id, metadataMatchSource.value);
  if (metadataCandidates.value.length > 0) {
    metadataDialogVisible.value = true;
    return;
  }
  ElMessage.warning(row.metadata_message || messages.renamePreviews.metadataDialog.empty);
}

function appendMetadataLogs(result: { items: RenamePreview[]; failed_items: Array<{ id: number; message: string }> }) {
  result.items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.metadataDialog.matchSuccessLog, {
        name: item.current_target_name,
        score: item.metadata_match_score ?? 0,
      }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.metadataDialog.matchFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
}

function shouldRunAiBatch(preview: RenamePreview) {
  return (
    preview.status !== "renamed" &&
    preview.status !== "excluded" &&
    (!preview.metadata_match_status ||
      preview.metadata_match_status === "failed" ||
      preview.metadata_match_status === "low_confidence")
  );
}

function aiBatchTargetPreviews() {
  return previewStore.previews.filter(shouldRunAiBatch);
}

function cacheAiBatchResult(result: BatchAiParseResult) {
  const next = { ...aiBatchResults.value };
  result.items.forEach((item) => {
    next[item.id] = item.result;
  });
  aiBatchResults.value = next;
}

function appendAiBatchLogs(result: BatchAiParseResult) {
  result.items.forEach((item) => {
    const bestCandidate = item.result.candidates[0];
    const title = bestCandidate?.title || messages.renamePreviews.aiParse.noCandidateTitle;
    const confidence = bestCandidate?.confidence ?? 0;
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.aiParse.batchSuccessLog, {
        id: item.id,
        status: aiParseStatusLabel(item.result.status),
        title,
        confidence,
      }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.aiParse.batchFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
  if (result.usage && Object.keys(result.usage).length > 0) {
    operationProgressLogs.value.push(
      `${messages.renamePreviews.aiParse.usage}：${JSON.stringify(result.usage)}`,
    );
  }
}

function appendMetadataAiFallbackLogs(result: { total_count: number; fallback_count: number; metadata: { items: RenamePreview[]; failed_items: Array<{ id: number; message: string }> }; ai: BatchAiParseResult }) {
  operationProgressLogs.value.push(
    formatMessage(messages.renamePreviews.aiParse.fallbackSummaryLog, {
      metadataTotal: result.total_count,
      fallbackCount: result.fallback_count,
    }),
  );
  appendMetadataLogs(result.metadata);
  appendAiBatchLogs(result.ai);
}

async function matchSelectedTmdbOnly() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await confirmResourceOperation(messages.renamePreviews.tmdbMatch, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, messages.renamePreviews.tmdbMatch);
  const result = await previewStore.matchMetadataBatch(selectedPreviewIds.value, metadataMatchSource.value);
  appendMetadataLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count);
}

async function matchSelectedTmdbWithAiFallback() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiFallbackOperation(selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, messages.renamePreviews.matchModeTmdbAiFallback);
  const result = await previewStore.matchMetadataBatchWithAiFallback(selectedPreviewIds.value, metadataMatchSource.value);
  cacheAiBatchResult(result.ai);
  appendMetadataAiFallbackLogs(result);
  finishOperationProgress(
    result.total_count,
    result.metadata.success_count,
    result.metadata.failed_count + result.ai.failed_count,
    result.ai.blocked_count + result.ai.skipped_count,
  );
}

async function parseSelectedWithAi() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiBatchOperation(messages.renamePreviews.aiParse.batchSelected, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, messages.renamePreviews.aiParse.batchSelected);
  const result = await previewStore.parseBatchWithAi(selectedPreviewIds.value, metadataMatchSource.value);
  cacheAiBatchResult(result);
  appendAiBatchLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count, result.blocked_count + result.skipped_count);
}

async function parseSelectedWithAiFromToolbar() {
  await parseSelectedWithAi();
}

async function parseAllWithAi() {
  const targets = aiBatchTargetPreviews();
  if (targets.length === 0) {
    ElMessage.warning(messages.renamePreviews.aiParse.noBatchTargets);
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiBatchOperation(messages.renamePreviews.aiParse.batchAll, targets.length);
  resetOperationProgress(targets.length, messages.renamePreviews.aiParse.batchAll);
  const result = await previewStore.parseBatchWithAi(targets.map((item) => item.id), metadataMatchSource.value);
  cacheAiBatchResult(result);
  appendAiBatchLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count, result.blocked_count + result.skipped_count);
}

async function matchAllTmdbOnly() {
  const unmatchedCount = previewStore.previews.filter((preview) => !preview.metadata_match_status).length;
  if (unmatchedCount === 0) {
    ElMessage.warning(messages.renamePreviews.metadataDialog.empty);
    return;
  }
  await confirmResourceOperation(messages.renamePreviews.tmdbMatchAll, unmatchedCount);
  resetOperationProgress(unmatchedCount, messages.renamePreviews.tmdbMatchAll);
  const result = await previewStore.matchAllUnmatched(metadataMatchSource.value);
  appendMetadataLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count);
}

async function matchAllTmdbWithAiFallback() {
  const unmatchedCount = previewStore.previews.filter((preview) => !preview.metadata_match_status).length;
  if (unmatchedCount === 0) {
    ElMessage.warning(messages.renamePreviews.metadataDialog.empty);
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiFallbackOperation(unmatchedCount);
  resetOperationProgress(unmatchedCount, messages.renamePreviews.matchModeTmdbAiFallback);
  const result = await previewStore.matchAllUnmatchedWithAiFallback(metadataMatchSource.value);
  cacheAiBatchResult(result.ai);
  appendMetadataAiFallbackLogs(result);
  finishOperationProgress(
    result.total_count,
    result.metadata.success_count,
    result.metadata.failed_count + result.ai.failed_count,
    result.ai.blocked_count + result.ai.skipped_count,
  );
}

async function handleTmdbDropdownCommand(command: string) {
  if (command === "all_tmdb") {
    await matchAllTmdbOnly();
    return;
  }
  if (command === "selected_tmdb_ai") {
    await matchSelectedTmdbWithAiFallback();
    return;
  }
  if (command === "all_tmdb_ai") {
    await matchAllTmdbWithAiFallback();
  }
}

async function handleAiDropdownCommand(command: string) {
  if (command === "all_ai") {
    await parseAllWithAi();
  }
}

async function handleRenameDropdownCommand(command: string) {
  if (command === "all_rename") {
    await executeAllPreviews();
  }
}

async function excludeSelectedPreviews() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await confirmResourceOperation(messages.renamePreviews.excludeSelected, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, messages.renamePreviews.excludeSelected);
  const result = await previewStore.excludePreviews(selectedPreviewIds.value);
  result.items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.excludeSuccessLog, { name: item.file_name }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.excludeFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
  await previewStore.loadPreviews(previewStore.filters);
  await pendingFileStore.loadPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  finishOperationProgress(result.total_count, result.success_count, result.failed_count);
}

async function excludeSinglePreview(row: RenamePreview) {
  await confirmResourceOperation(messages.renamePreviews.excludeSingle, 1);
  await previewStore.excludePreview(row.id);
  await previewStore.loadPreviews(previewStore.filters);
  await pendingFileStore.loadPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  ElMessage.success(messages.renamePreviews.excludeSingleSuccess);
}

async function applyMetadataCandidate(match: MetadataMatchResult) {
  if (!metadataPreviewId.value) {
    return;
  }
  await previewStore.applyMetadataCandidate(metadataPreviewId.value, match, selectedMetadataFields.value);
  metadataDialogVisible.value = false;
  metadataCandidates.value = [];
  ElMessage.success(messages.renamePreviews.metadataDialog.selectSuccess);
}

async function parseSingleWithAi(row: RenamePreview) {
  const cachedResult = aiBatchResults.value[row.id];
  if (cachedResult) {
    aiParsePreviewId.value = row.id;
    aiParseResult.value = cachedResult;
    aiParseDialogVisible.value = true;
    return;
  }
  aiParsingPreviewId.value = row.id;
  aiParsePreviewId.value = row.id;
  aiParseResult.value = null;
  try {
    const result = await previewStore.parseWithAi(row.id, metadataMatchSource.value);
    aiParseResult.value = result;
    aiParseDialogVisible.value = true;
    if (result.status === "success") {
      ElMessage.success(messages.renamePreviews.aiParse.success);
    } else if (result.status === "blocked") {
      ElMessage.warning(result.message || messages.renamePreviews.aiParse.blocked);
    } else {
      ElMessage.error(result.message || messages.renamePreviews.aiParse.failed);
    }
  } finally {
    aiParsingPreviewId.value = null;
  }
}

async function parsePendingWithAi(row: { id: number }) {
  aiParsingPreviewId.value = row.id;
  aiParsePreviewId.value = null;
  aiParseResult.value = null;
  try {
    const result = await pendingFileStore.parseWithAi(row.id);
    aiParseResult.value = result;
    aiParseDialogVisible.value = true;
    if (result.status === "success") {
      ElMessage.success(messages.renamePreviews.aiParse.success);
    } else if (result.status === "blocked") {
      ElMessage.warning(result.message || messages.renamePreviews.aiParse.blocked);
    } else {
      ElMessage.error(result.message || messages.renamePreviews.aiParse.failed);
    }
  } finally {
    aiParsingPreviewId.value = null;
  }
}

async function applyAiCandidate(candidate: AiParseCandidate) {
  if (!aiParsePreviewId.value) {
    return;
  }
  await previewStore.applyAiCandidate(aiParsePreviewId.value, candidate);
  const next = { ...aiBatchResults.value };
  delete next[aiParsePreviewId.value];
  aiBatchResults.value = next;
  aiParseDialogVisible.value = false;
  aiParseResult.value = null;
  ElMessage.success(messages.renamePreviews.aiParse.selectSuccess);
}

async function removePendingFile(row: { id: number }) {
  await pendingFileStore.removePendingFile(row.id);
  ElMessage.success(messages.renamePreviews.pendingFiles.removed);
}

async function clearPendingFiles() {
  const count = pendingFileStore.pendingFiles.length;
  if (count > 0) {
    await confirmResourceOperation(messages.renamePreviews.pendingFiles.clear, count);
    resetOperationProgress(count, messages.renamePreviews.pendingFiles.clear);
  }
  await pendingFileStore.clearPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  if (count > 0) {
    operationProgressLogs.value.push(
      formatMessage(messages.renamePreviews.operationSummary, { success: count, failed: 0 }),
    );
    finishOperationProgress(count, count, 0);
  }
  ElMessage.success(messages.renamePreviews.pendingFiles.cleared);
}

function openPendingMoveDialog() {
  if (selectedPendingFileIds.value.length === 0) {
    ElMessage.warning(messages.renamePreviews.pendingFiles.selectFirst);
    return;
  }
  pendingMoveTargetDirectory.value = "";
  pendingMoveDialogVisible.value = true;
}

async function moveSelectedPendingFiles() {
  if (!pendingMoveTargetDirectory.value.trim()) {
    ElMessage.warning(messages.renamePreviews.pendingFiles.targetDirectoryPlaceholder);
    return;
  }
  await confirmResourceOperation(messages.renamePreviews.pendingFiles.move, selectedPendingFileIds.value.length);
  resetOperationProgress(selectedPendingFileIds.value.length, messages.renamePreviews.pendingFiles.move);
  await pendingFileStore.movePendingFiles(selectedPendingFileIds.value, pendingMoveTargetDirectory.value.trim());
  operationProgressLogs.value.push(
    formatMessage(messages.renamePreviews.operationSummary, {
      success: selectedPendingFileIds.value.length,
      failed: 0,
    }),
  );
  finishOperationProgress(selectedPendingFileIds.value.length, selectedPendingFileIds.value.length, 0);
  selectedPendingFileIds.value = [];
  pendingMoveDialogVisible.value = false;
  ElMessage.success(messages.renamePreviews.pendingFiles.moved);
}

async function continueWithoutEmptyTargets() {
  const filteredPreviews = removeEmptyTargetNamePreviews(pendingRenamePreviews.value);
  emptyTargetDialogVisible.value = false;
  pendingRenamePreviews.value = [];

  if (filteredPreviews.length === 0) {
    ElMessage.warning(messages.renamePreviews.noExecutableAfterRemove);
    return;
  }

  selectedPreviewIds.value = filteredPreviews.map((preview) => preview.id);
  await runRenameDryRun();
}

async function executeRenameOperation() {
  const total = renameOperationStore.currentOperation?.ready_count ?? 0;
  resetOperationProgress(total, messages.renamePreviews.rename);
  await renameOperationStore.executeCurrentOperation();
  const operation = renameOperationStore.currentOperation;
  if (renameOperationStore.errorMessage) {
    ElMessage.error(renameOperationStore.errorMessage);
    return;
  }
  if (operation?.failed_count && operation.renamed_count > 0) {
    ElMessage.warning(messages.renamePreviews.operationDialog.partialFailed);
  } else if (operation?.failed_count) {
    ElMessage.error(messages.renamePreviews.operationDialog.failed);
  } else if (operation) {
    ElMessage.success(messages.renamePreviews.operationDialog.success);
  }
  if (operation) {
    operation.items.forEach((item) => {
      operationProgressLogs.value.push(`${operationStatusLabel(item.status)}：${item.target_path}${item.message ? `，${item.message}` : ""}`);
    });
    finishOperationProgress(operation.total_count, operation.renamed_count, operation.failed_count);
  }
  await refreshPreviews();
}

function openEditDialog(row: { id: number; current_target_name: string }) {
  editingPreviewId.value = row.id;
  editingTargetName.value = row.current_target_name;
  editDialogVisible.value = true;
}

async function saveEdit() {
  if (!editingPreviewId.value) {
    return;
  }
  await previewStore.updatePreview(editingPreviewId.value, editingTargetName.value);
  editDialogVisible.value = false;
}

onMounted(async () => {
  try {
    aiConnectionHistory.value = await settingsStore.loadAiTestResult({ silent: true });
  } catch {
    aiConnectionHistory.value = null;
  }
  await mediaStore.loadMediaSources();
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    previewStore.filters.media_source_id = routeSourceId;
    await mediaStore.loadScanJobs({ media_source_id: routeSourceId });
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    previewStore.filters.scan_job_id = routeScanJobId;
    await refreshPreviews();
  }
});
</script>

<template>
  <ListPageLayout class="rename-preview-page" :title="messages.renamePreviews.title" :description="messages.renamePreviews.description">
    <template #filters>
      <el-select
        v-model="previewStore.filters.media_source_id"
        class="rename-filter-select"
        :placeholder="messages.renamePreviews.mediaSource"
        clearable
        @change="loadScanJobsForSelectedSource"
        @clear="loadScanJobsForSelectedSource"
      >
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="previewStore.filters.scan_job_id" class="rename-filter-select" :placeholder="messages.renamePreviews.scanJob" clearable>
        <el-option v-for="item in scanJobOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="previewStore.filters.status" class="rename-filter-select" :placeholder="messages.renamePreviews.status" clearable>
        <el-option :label="messages.common.all" value="" />
        <el-option :label="messages.status.generated" value="generated" />
        <el-option :label="messages.status.needsReview" value="needs_review" />
        <el-option :label="messages.status.edited" value="edited" />
        <el-option :label="messages.status.renamed" value="renamed" />
        <el-option :label="messages.status.noRename" value="no_rename" />
        <el-option :label="messages.status.unableRename" value="unable_rename" />
      </el-select>
      <el-select v-model="previewStore.filters.media_type" class="rename-filter-select" :placeholder="messages.renamePreviews.type" clearable>
        <el-option :label="messages.renamePreviews.mediaTypes.movie" value="movie" />
        <el-option :label="messages.renamePreviews.mediaTypes.episode" value="episode" />
        <el-option :label="messages.renamePreviews.mediaTypes.unknown" value="unknown" />
      </el-select>
      <el-input v-model="previewStore.filters.keyword" class="rename-keyword-input" :placeholder="messages.renamePreviews.keywordPlaceholder" clearable />
    </template>

    <template #queryAction>
      <el-button class="query-action-button" :icon="Search" :disabled="!previewStore.filters.scan_job_id" @click="refreshPreviews">
        {{ messages.common.query }}
      </el-button>
      <el-button @click="resetRenamePreviews">{{ messages.common.reset }}</el-button>
    </template>

    <template #actions>
      <div v-if="activePreviewTab === 'previews'" class="rename-action-stack">
        <div class="rename-action-groups">
          <div class="rename-action-group">
            <el-button
              type="primary"
              :icon="MagicStick"
              :loading="operationProgressVisible && activeOperationName === messages.renamePreviews.generate"
              :disabled="previewStore.loading"
              @click="generatePreviews"
            >
              {{ operationButtonLabel(messages.renamePreviews.generate, messages.renamePreviews.operationResultDialog.runningGenerate) }}
            </el-button>
            <el-select
              v-model="metadataMatchSource"
              class="metadata-source-select"
              :placeholder="messages.renamePreviews.metadataMatchSource"
              :disabled="previewStore.loading"
            >
              <el-option :label="messages.renamePreviews.metadataSourceParsedTitle" value="parsed_title" />
              <el-option :label="messages.renamePreviews.metadataSourceOriginalFileName" value="original_file_name" />
              <el-option :label="messages.renamePreviews.metadataSourceParentFolderTitle" value="parent_folder_title" />
            </el-select>
            <div class="action-split-button">
              <el-button
                :icon="Connection"
                :disabled="selectedPreviewIds.length === 0 || previewStore.loading"
                :loading="operationProgressVisible && activeOperationName === messages.renamePreviews.tmdbMatch"
                class="action-split-main"
                @click="matchSelectedTmdbOnly"
              >
                {{ operationButtonLabel(messages.renamePreviews.tmdbMatch, messages.renamePreviews.operationResultDialog.runningMatch) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="previewStore.loading"
                @command="handleTmdbDropdownCommand"
              >
                <el-button class="action-split-caret">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_tmdb">{{ messages.renamePreviews.tmdbMatchAll }}</el-dropdown-item>
                    <el-dropdown-item command="selected_tmdb_ai" :disabled="!aiBatchReady">{{ messages.renamePreviews.tmdbAiFallbackSelected }}</el-dropdown-item>
                    <el-dropdown-item command="all_tmdb_ai" :disabled="!aiBatchReady">{{ messages.renamePreviews.tmdbAiFallbackAll }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div class="action-split-button">
              <el-button
                :icon="MagicStick"
                :disabled="selectedPreviewIds.length === 0 || previewStore.loading || !aiBatchReady"
                :loading="operationProgressVisible && activeOperationName === messages.renamePreviews.aiParse.batchSelected"
                class="action-split-main"
                @click="parseSelectedWithAiFromToolbar"
              >
                {{ operationButtonLabel(messages.renamePreviews.aiMatch, messages.renamePreviews.operationResultDialog.runningAi) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="previewStore.loading"
                @command="handleAiDropdownCommand"
              >
                <el-button class="action-split-caret">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_ai" :disabled="!aiBatchReady || previewStore.previews.length === 0">
                      {{ messages.renamePreviews.aiParse.batchAll }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div class="action-split-button">
              <el-button
                type="success"
                :icon="Select"
                :disabled="selectedPreviewIds.length === 0"
                :loading="renameOperationStore.loading && !operationProgressVisible"
                class="action-split-main"
                @click="executeSelectedPreviews"
              >
                {{ operationButtonLabel(messages.renamePreviews.rename, messages.renamePreviews.operationResultDialog.runningRename) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="previewStore.previews.length === 0"
                @command="handleRenameDropdownCommand"
              >
                <el-button class="action-split-caret">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_rename">{{ messages.renamePreviews.executeAll }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <el-button
              :icon="CloseBold"
              :disabled="selectedPreviewIds.length === 0 || previewStore.loading"
              :loading="operationProgressVisible && activeOperationName === messages.renamePreviews.excludeSelected"
              @click="excludeSelectedPreviews"
            >
              {{ operationButtonLabel(messages.renamePreviews.excludeSelected, messages.renamePreviews.operationResultDialog.runningExclude) }}
            </el-button>
          </div>
        </div>
        <div v-if="!aiBatchReady" class="rename-toolbar-status-row">
          <el-tooltip :content="aiBatchBlockMessage" placement="top">
            <div class="rename-toolbar-status-chip" role="status" aria-live="polite">
              <span class="rename-toolbar-status-dot" aria-hidden="true"></span>
              <span>{{ aiBatchStatusLabel }}</span>
            </div>
          </el-tooltip>
        </div>
        <div v-if="operationProgressVisible" class="rename-operation-compact-progress">
          <el-progress :percentage="operationProgressPercent" :show-text="false" :stroke-width="5" />
          <span>{{ operationProgressText }}</span>
        </div>
      </div>
      <div v-else class="subsection-actions pending-files-actions">
        <el-button
          :icon="Select"
          :disabled="selectedPendingFileIds.length === 0"
          @click="openPendingMoveDialog"
        >
          {{ messages.renamePreviews.pendingFiles.move }}
        </el-button>
        <el-button
          :icon="Delete"
          :disabled="pendingFileStore.pendingFiles.length === 0"
          @click="clearPendingFiles"
        >
          {{ messages.renamePreviews.pendingFiles.clear }}
        </el-button>
      </div>
    </template>

    <el-alert v-if="previewStore.errorMessage" type="error" :title="previewStore.errorMessage" show-icon />

    <template #table>
      <FullscreenTablePanel title="" variant="tab-header">
      <el-tabs v-model="activePreviewTab" class="rename-preview-tabs">
        <el-tab-pane :label="messages.renamePreviews.tabs.previews" name="previews">
          <el-table
          :data="pagedPreviews"
          class="data-table rename-previews-table"
          height="100%"
          table-layout="fixed"
          :default-sort="defaultSort"
          @row-click="openDetailDialog"
          @selection-change="handleSelectionChange"
          @sort-change="handleSortChange"
          >
          <template #empty>
            <div v-if="shouldShowGeneratePreviewEmpty" class="rename-empty-preview-action">
              <strong>{{ messages.renamePreviews.emptyPreviewAction.title }}</strong>
              <span>
                {{
                  formatMessage(messages.renamePreviews.emptyPreviewAction.description, {
                    count: mediaStore.mediaFiles.length,
                  })
                }}
              </span>
              <el-button type="primary" :loading="previewStore.loading" @click.stop="generatePreviews">
                {{ messages.renamePreviews.emptyPreviewAction.button }}
              </el-button>
            </div>
            <el-empty v-else :description="messages.common.emptyTable" />
          </template>
          <el-table-column type="selection" width="44" align="center" fixed="left" />
          <el-table-column
          prop="status"
          :label="messages.common.status"
          width="70"
          class-name="preview-compact-column preview-status-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
          </el-table-column>
          <el-table-column prop="file_name" :label="messages.renamePreviews.columns.originalName" min-width="220" align="left" header-align="left" sortable="custom">
          <template #default="{ row }">
          <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
          </el-table-column>
          <el-table-column
          prop="current_target_name"
          :label="messages.renamePreviews.columns.targetName"
          min-width="220"
          align="left"
          header-align="left"
          sortable="custom"
          >
          <template #default="{ row }">
          <TextCell :value="row.current_target_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.parsedTitle" min-width="132" align="left" header-align="left">
          <template #default="{ row }">
          <el-tooltip
            :content="`${row.parsed_title || '-'} · ${messages.renamePreviews.columns.titleSource}: ${titleSourceLabel(row.title_source)}${row.parent_folder_title ? ` · ${messages.renamePreviews.columns.parentFolderTitle}: ${row.parent_folder_title}` : ''}`"
            placement="top"
          >
            <TextCell :value="row.parsed_title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </el-tooltip>
          </template>
          </el-table-column>
          <el-table-column
          :label="messages.renamePreviews.columns.metadata"
          width="80"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          >
          <template #default="{ row }">
          <el-tooltip :content="metadataReason(row)" placement="top" :disabled="!metadataReason(row) || metadataReason(row) === '-'">
            <el-tag :type="metadataStatusTagType(row.metadata_match_status)" effect="light">
            {{ metadataStatusLabel(row.metadata_match_status) }}
            </el-tag>
          </el-tooltip>
          </template>
          </el-table-column>
          <el-table-column
          prop="metadata_match_score"
          :label="messages.renamePreviews.columns.metadataScore"
          width="70"
          class-name="metadata-score-column"
          header-class-name="metadata-score-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">
            <el-tooltip :content="metadataReason(row)" placement="top" :disabled="!metadataReason(row) || metadataReason(row) === '-'">
              <span>{{ row.metadata_match_score ?? 0 }}%</span>
            </el-tooltip>
          </template>
          </el-table-column>
          <el-table-column
          prop="media_type"
          :label="messages.renamePreviews.type"
          width="60"
          class-name="preview-compact-column preview-type-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">{{ mediaTypeLabel(row.media_type) }}</template>
          </el-table-column>
          <el-table-column
          prop="parsed_year"
          :label="messages.renamePreviews.columns.year"
          width="60"
          class-name="preview-year-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">{{ row.parsed_year ?? "-" }}</template>
          </el-table-column>
          <el-table-column
          :label="messages.renamePreviews.columns.seasonEpisode"
          width="70"
          class-name="preview-season-episode-column"
          align="center"
          header-align="center"
          >
          <template #default="{ row }">{{ seasonEpisode(row) }}</template>
          </el-table-column>
          <el-table-column
          prop="updated_at"
          :label="messages.renamePreviews.columns.updatedAt"
          width="168"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column :label="messages.common.actions" width="164" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
          <div class="table-actions">
          <el-tooltip :content="messages.renamePreviews.actions.tmdbMatch" placement="top">
          <el-button
          class="table-action-button action-sync"
          :icon="Connection"
          text
          circle
          @click.stop="matchMetadata(row)"
          />
          </el-tooltip>
          <el-tooltip :content="messages.renamePreviews.actions.aiParse" placement="top">
          <el-button
          class="table-action-button action-ai"
          :icon="MagicStick"
          text
          circle
          :loading="aiParsingPreviewId === row.id"
          :disabled="previewStore.loading && aiParsingPreviewId !== row.id"
          @click.stop="parseSingleWithAi(row)"
          />
          </el-tooltip>
          <el-tooltip v-if="row.metadata_candidate_count > 0" :content="messages.renamePreviews.actions.metadataBackfill" placement="top">
          <el-button
          class="table-action-button action-sync"
          :icon="MagicStick"
          text
          circle
          @click.stop="openMetadataBackfill(row)"
          />
          </el-tooltip>
          <el-tooltip :content="messages.renamePreviews.actions.edit" placement="top">
          <el-button class="table-action-button action-edit" :icon="Edit" text circle @click.stop="openEditDialog(row)" />
          </el-tooltip>
          <el-tooltip :content="messages.renamePreviews.actions.exclude" placement="top">
          <el-button
          class="table-action-button action-delete"
          :icon="CloseBold"
          text
          circle
          @click.stop="excludeSinglePreview(row)"
          />
          </el-tooltip>
          <el-tooltip :content="messages.renamePreviews.actions.execute" placement="top">
          <el-button
          class="table-action-button action-run"
          :icon="Select"
          text
          circle
          @click.stop="executeSinglePreview(row)"
          />
          </el-tooltip>
          </div>
          </template>
          </el-table-column>
          </el-table>
          <div class="rename-table-footer">
            <div class="rename-footer-stats">
              <el-popover placement="top-start" :trigger="['hover', 'click']" width="220" popper-class="rename-stats-popover">
                <template #reference>
                  <el-button class="rename-stats-button" size="small">
                    <span class="rename-stats-icon" aria-hidden="true">📊</span>
                    <span>{{ messages.common.statistics }}</span>
                  </el-button>
                </template>
                <div class="rename-stats-popover-list">
                  <ListStatItem
                    v-for="item in previewStatItems"
                    :key="item.label"
                    :label="item.label"
                    :value="item.value"
                    :tone="item.tone"
                  />
                </div>
              </el-popover>
            </div>
            <TablePagination pagination-key="rename-previews" :total="previewStore.previews.length" :pager-count="3" />
          </div>
        </el-tab-pane>

        <el-tab-pane name="pending">
          <template #label>
            <el-tooltip :content="messages.renamePreviews.pendingFiles.description" placement="top">
              <span class="tab-label-with-tooltip">{{ messages.renamePreviews.tabs.pendingFiles }}</span>
            </el-tooltip>
          </template>
          <el-table
          :data="pendingFileStore.pendingFiles"
          class="data-table"
          height="100%"
          table-layout="auto"
          @selection-change="handlePendingSelectionChange"
          >
          <el-table-column type="selection" width="44" align="center" fixed="left" />
          <el-table-column :label="messages.renamePreviews.pendingFiles.columns.reason" width="108" align="center" header-align="center">
          <template #default="{ row }">
          <el-tag type="warning" effect="light">{{ pendingReasonLabel(row.reason) }}</el-tag>
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.pendingFiles.columns.fileName" min-width="220" align="left" header-align="left">
          <template #default="{ row }">
          <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.pendingFiles.columns.path" min-width="280" align="left" header-align="left">
          <template #default="{ row }">
          <TextCell :value="row.file_path" :max-length="tableDisplayConfig.pathMaxLength" />
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.pendingFiles.columns.size" width="104" align="center" header-align="center">
          <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.pendingFiles.columns.scanTime" width="168" align="center" header-align="center">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column :label="messages.common.actions" width="144" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
          <el-tooltip :content="messages.renamePreviews.actions.aiParse" placement="top">
          <el-button
          class="table-action-button action-ai"
          :icon="MagicStick"
          text
          circle
          :loading="aiParsingPreviewId === row.id"
          :disabled="pendingFileStore.loading && aiParsingPreviewId !== row.id"
          @click="parsePendingWithAi(row)"
          />
          </el-tooltip>
          <el-tooltip :content="messages.renamePreviews.pendingFiles.remove" placement="top">
          <el-button
          class="table-action-button action-delete"
          :icon="Delete"
          text
          circle
          @click="removePendingFile(row)"
          />
          </el-tooltip>
          </template>
          </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
      </FullscreenTablePanel>
    </template>

    <el-dialog
      v-model="operationResultVisible"
      :title="operationResultTitle"
      width="680px"
      class="operation-result-dialog"
      @closed="handleOperationResultClosed"
    >
      <div class="operation-result-body">
        <div class="operation-result-summary">
          <el-tag type="success" effect="light">
            {{ messages.renamePreviews.operationResultDialog.success }} {{ operationResultSuccess }}
          </el-tag>
          <el-tag type="danger" effect="light">
            {{ messages.renamePreviews.operationResultDialog.failed }} {{ operationResultFailed }}
          </el-tag>
          <el-tag type="info" effect="light">
            {{ messages.renamePreviews.operationResultDialog.skipped }} {{ operationResultSkipped }}
          </el-tag>
        </div>
        <div class="operation-result-log-list">
          <el-empty
            v-if="operationResultLogs.length === 0"
            :description="messages.renamePreviews.operationResultDialog.emptyLogs"
          />
          <p v-for="(item, index) in visibleOperationResultLogs" v-else :key="index">{{ item }}</p>
        </div>
      </div>
      <template #footer>
        <el-button
          v-if="operationResultLogs.length > 8"
          @click="operationResultExpanded = !operationResultExpanded"
        >
          {{
            operationResultExpanded
              ? messages.renamePreviews.operationResultDialog.collapseLog
              : messages.renamePreviews.operationResultDialog.viewFullLog
          }}
        </el-button>
        <el-button type="primary" @click="operationResultVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pendingMoveDialogVisible" :title="messages.renamePreviews.pendingFiles.moveDialog.title" width="560px">
      <el-form label-position="top">
        <el-form-item :label="messages.renamePreviews.pendingFiles.targetDirectory">
          <el-input
            v-model="pendingMoveTargetDirectory"
            :placeholder="messages.renamePreviews.pendingFiles.targetDirectoryPlaceholder"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pendingMoveDialogVisible = false">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" @click="moveSelectedPendingFiles">
          {{ messages.renamePreviews.pendingFiles.moveDialog.confirm }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" :title="messages.renamePreviews.detailTitle" width="760px">
      <div class="detail-panel">
        <div v-for="item in detailRows" :key="item.label" class="detail-item">
          <span>{{ item.label }}</span>
          <strong>{{ item.value ?? "-" }}</strong>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDialogVisible" :title="messages.renamePreviews.editDialog.title" width="520px">
      <el-input v-model="editingTargetName" :placeholder="messages.renamePreviews.editDialog.placeholder" />
      <template #footer>
        <el-button @click="editDialogVisible = false">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" @click="saveEdit">{{ messages.common.save }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="aiParseDialogVisible" :title="messages.renamePreviews.aiParse.title" width="920px">
      <div v-if="aiParseResult" class="ai-parse-result-panel">
        <div class="ai-parse-summary">
          <el-tag :type="aiParseStatusTagType(aiParseResult.status)" effect="light">
            {{ aiParseStatusLabel(aiParseResult.status) }}
          </el-tag>
          <span>{{ aiParseResult.message }}</span>
          <span v-if="aiParseResult.response_ms !== undefined && aiParseResult.response_ms !== null">
            {{ messages.renamePreviews.aiParse.responseTime }}：{{ aiParseResult.response_ms }}
            {{ messages.renamePreviews.aiParse.responseTimeUnit }}
          </span>
        </div>
        <pre
          v-if="aiParseResult.usage && Object.keys(aiParseResult.usage).length > 0"
          class="metadata-raw-json ai-parse-usage"
        >{{ messages.renamePreviews.aiParse.usage }}：{{ JSON.stringify(aiParseResult.usage, null, 2) }}</pre>
        <el-empty
          v-if="aiParseResult.candidates.length === 0"
          :description="messages.renamePreviews.aiParse.empty"
        />
        <el-table v-else :data="aiParseResult.candidates" class="data-table" table-layout="auto">
          <el-table-column type="expand" width="48">
            <template #default="{ row }">
              <pre class="metadata-raw-json">{{ JSON.stringify(row.raw_data ?? {}, null, 2) }}</pre>
            </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.title" min-width="180" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.mediaType" width="96" align="center" header-align="center">
            <template #default="{ row }">{{ mediaTypeLabel(row.media_type) }}</template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.year" width="84" align="center" header-align="center">
            <template #default="{ row }">{{ row.year ?? "-" }}</template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.seasonEpisode" width="104" align="center" header-align="center">
            <template #default="{ row }">{{ seasonEpisode(row) }}</template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.confidence" width="104" align="center" header-align="center">
            <template #default="{ row }">{{ row.confidence }}%</template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.aiParse.fields.reason" min-width="220" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="candidateValue(row.reason)" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column
            v-if="aiParsePreviewId"
            :label="messages.common.actions"
            width="128"
            align="center"
            header-align="center"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button type="primary" @click="applyAiCandidate(row)">
                {{ messages.renamePreviews.aiParse.apply }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="aiParseDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="metadataDialogVisible" :title="messages.renamePreviews.metadataDialog.title" width="1120px">
      <div class="metadata-field-selector">
        <span>{{ messages.renamePreviews.metadataDialog.fieldSelection }}</span>
        <el-checkbox-group v-model="selectedMetadataFields">
          <el-checkbox v-for="field in metadataFieldOptions" :key="field.value" :label="field.value">
            {{ field.label }}
          </el-checkbox>
        </el-checkbox-group>
      </div>
      <el-empty v-if="metadataCandidates.length === 0" :description="messages.renamePreviews.metadataDialog.empty" />
      <el-table v-else :data="pagedMetadataCandidates" class="data-table" table-layout="auto">
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <pre class="metadata-raw-json">{{ JSON.stringify(row.candidate.raw_data ?? {}, null, 2) }}</pre>
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.metadataScore" width="96" align="center" header-align="center">
          <template #default="{ row }">{{ row.score }}%</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.metadata" width="110" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag :type="metadataStatusTagType(row.status)" effect="light">
              {{ metadataStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.title" min-width="160" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.english_title" min-width="160" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.english_title || row.candidate.original_title || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.year" width="84" align="center" header-align="center">
          <template #default="{ row }">{{ row.candidate.year ?? "-" }}</template>
        </el-table-column>
        <el-table-column label="TMDB" width="104" align="center" header-align="center">
          <template #default="{ row }">{{ row.candidate.tmdb_id || row.candidate.provider_id || "-" }}</template>
        </el-table-column>
        <el-table-column label="IMDb" width="120" align="center" header-align="center">
          <template #default="{ row }">{{ row.candidate.imdb_id || "-" }}</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.rating" width="92" align="center" header-align="center">
          <template #default="{ row }">{{ candidateValue(row.candidate.vote_average) }}</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.language" width="84" align="center" header-align="center">
          <template #default="{ row }">{{ candidateValue(row.candidate.original_language) }}</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.genres" min-width="140" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="joinCandidateValues(row.candidate.genres)" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.cast" min-width="180" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="joinCandidateValues(row.candidate.cast)" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.director" min-width="120" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="joinCandidateValues(row.candidate.directors)" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.poster_path" min-width="120" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.poster_path || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.fields.overview" min-width="220" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.overview || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="148" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" @click="applyMetadataCandidate(row)">
              {{ messages.renamePreviews.metadataDialog.selectedApply }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <TablePagination
        v-if="metadataCandidates.length > 0"
        pagination-key="metadata-candidates"
        :total="metadataCandidates.length"
      />
      <template #footer>
        <el-button @click="metadataDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="emptyTargetDialogVisible" :title="messages.renamePreviews.emptyTargetDialog.title" width="760px">
      <el-alert :title="messages.renamePreviews.emptyTargetDialog.message" type="warning" show-icon />
      <el-table :data="findEmptyTargetNamePreviews(pendingRenamePreviews)" class="data-table" table-layout="auto">
        <el-table-column prop="file_name" :label="messages.renamePreviews.columns.originalName" min-width="220">
          <template #default="{ row }">
            <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
        </el-table-column>
        <el-table-column prop="parsed_title" :label="messages.renamePreviews.columns.parsedTitle" min-width="160">
          <template #default="{ row }">
            <TextCell :value="row.parsed_title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column prop="status" :label="messages.common.status" width="100" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="emptyTargetDialogVisible = false">{{ messages.renamePreviews.emptyTargetDialog.back }}</el-button>
        <el-button type="warning" :loading="renameOperationStore.loading" @click="continueWithoutEmptyTargets">
          {{ messages.renamePreviews.emptyTargetDialog.removeAndContinue }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="operationDialogVisible" :title="operationDialogTitle" width="860px">
      <div class="rename-operation-dialog">
        <el-alert
          v-if="renameOperationStore.errorMessage"
          :title="renameOperationStore.errorMessage"
          type="error"
          show-icon
        />
        <el-alert
          v-if="operationResultAlert"
          :title="operationResultAlert.title"
          :description="operationResultAlert.description"
          :type="operationResultAlert.type"
          show-icon
          :closable="false"
        />
        <div v-if="renameOperationStore.currentOperation" class="preview-stats operation-stats">
          <div>
            <span>{{ messages.common.total }}</span>
            <strong>{{ renameOperationStore.currentOperation.total_count }}</strong>
          </div>
          <div class="stat-generated">
            <span>{{ messages.status.ready }}</span>
            <strong>{{ renameOperationStore.currentOperation.ready_count }}</strong>
          </div>
          <div class="stat-review">
            <span>{{ messages.status.conflict }}</span>
            <strong>{{ renameOperationStore.currentOperation.conflict_count }}</strong>
          </div>
          <div class="stat-edited">
            <span>{{ messages.status.renamed }}</span>
            <strong>{{ renameOperationStore.currentOperation.renamed_count }}</strong>
          </div>
          <div class="stat-failed">
            <span>{{ messages.status.failed }}</span>
            <strong>{{ renameOperationStore.currentOperation.failed_count }}</strong>
          </div>
        </div>
        <el-table
          v-if="renameOperationStore.currentOperation"
          :data="renameOperationStore.currentOperation.items"
          class="data-table"
          table-layout="auto"
        >
          <el-table-column :label="messages.common.status" width="110" align="center" header-align="center">
            <template #default="{ row }">
              <el-tag :type="operationStatusTagType(row.status)" effect="light">
                {{ operationStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.sourcePath" min-width="220" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.source_path" :max-length="tableDisplayConfig.pathMaxLength" />
            </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.targetPath" min-width="220" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.target_path" :max-length="tableDisplayConfig.pathMaxLength" />
            </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.reason" min-width="160" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.message || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="operationDialogVisible = false">{{ messages.common.close }}</el-button>
        <el-button
          v-if="!operationHasExecuted"
          type="danger"
          :disabled="!renameOperationStore.canExecute"
          :loading="renameOperationStore.loading"
          @click="executeRenameOperation"
        >
          {{ messages.renamePreviews.operationDialog.confirm }}
        </el-button>
      </template>
    </el-dialog>
  </ListPageLayout>
</template>
