<script setup lang="ts">
import { isSessionChangedError } from "../utils/sessionGeneration";
import { ArrowDown, CloseBold, Connection, Delete, Document, Edit, MagicStick, Refresh, Search, Select, TrendCharts, View } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type {
  RenameSelectionItem,
  PreviewSelectionAction,
  AiParseCandidate,
  AiParseResult,
  BatchAiParseResult,
  BatchMultiSourceMatchResult,
  MetadataMatchResult,
  MetadataMatchSource,
  MultiSourceMatchMode,
  ProviderSearchResult,
  PendingFile,
  PendingMoveTask,
  RenamePreview,
} from "../api/client";
import { fetchAiReadiness, fetchMediaFilesPage, fetchPendingFilesPage, fetchRenamePreviewSelection, fetchRenamePreviewsPage, type AiReadiness } from "../api/client";
import FullscreenTablePanel from "../components/FullscreenTablePanel.vue";
import ListPageLayout from "../components/ListPageLayout.vue";
import MediaSourceSelect from "../components/MediaSourceSelect.vue";
import ScanJobSelect from "../components/ScanJobSelect.vue";
import ListStatItem from "../components/ListStatItem.vue";
import OperationLogDrawer from "../components/OperationLogDrawer.vue";
import RenamePreviewDetailsDialog from "../components/RenamePreviewDetailsDialog.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { formatMessage } from "../locales/zh-CN";
import { useAuthStore } from "../stores/auth";
import { useBackgroundJobsStore } from "../stores/backgroundJobs";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { usePendingFileStore } from "../stores/pendingFiles";
import { usePreviewStore } from "../stores/preview";
import { useRenameOperationStore } from "../stores/renameOperation";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime } from "../utils/displayFormat";
import { formatFileSize } from "../utils/displayFormat";
import { useTableRowSelection } from "../utils/tableRowSelection";
import {
  buildMultiSourceMatchRows,
  type MultiSourceMatchRow,
} from "../utils/multiSourceMatchResults";
import {
  namingTemplateStatusLabel,
  namingTemplateStatusTagType,
  namingTemplateStatusTooltip,
} from "../utils/renamePreviewTemplateStatus";
import {
  canPrepareRename,
  findEmptyTargetNamePreviews,
  removeEmptyTargetNamePreviews,
} from "../utils/renameSelection";

const { language, messages } = useInterfaceLanguage();
const labelSeparator = computed(() => language.value === "en-US" ? ": " : "：");

const mediaStore = useMediaStore();
const authStore = useAuthStore();
const backgroundJobs = useBackgroundJobsStore();
const previewStore = usePreviewStore();
const pendingFileStore = usePendingFileStore();
const renameOperationStore = useRenameOperationStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const { table: previewTable, toggleByRowClick: togglePreviewRow } = useTableRowSelection<RenamePreview>();
const { table: pendingTable, toggleByRowClick: togglePendingRow } = useTableRowSelection<PendingFile>();
const route = useRoute();
const router = useRouter();
const canSubmitMetadata = computed(() => authStore.hasPermission("metadata:submit"));
const canExecuteRename = computed(() => authStore.hasPermission("rename:execute"));
const canManagePending = computed(() => authStore.hasPermission("scan:run"));
const canReadLogs = computed(() => authStore.hasPermission("audit:read"));
const canExecuteRollback = computed(() => authStore.hasPermission("rollback:execute"));
const metadataPermissionTitle = computed(() => (canSubmitMetadata.value ? "" : messages.value.auth.permissionDenied));
const renamePermissionTitle = computed(() => (canExecuteRename.value ? "" : messages.value.auth.permissionDenied));
const rollbackPermissionTitle = computed(() =>
  canExecuteRollback.value ? "" : messages.value.renamePreviews.operationDialog.rollbackPermissionDenied,
);

const editDialogVisible = ref(false);
const operationDialogVisible = ref(false);
const operationLogVisible = ref(false);
const selectedOperationLogTaskId = ref<number | null>(null);
const selectedOperationLogTaskType = ref<"rename_operation" | "rollback_plan" | "pending_move">("rename_operation");
const emptyTargetDialogVisible = ref(false);
const detailDialogVisible = ref(false);
const metadataDialogVisible = ref(false);
const multiSourceResultDialogVisible = ref(false);
const aiParseDialogVisible = ref(false);
const pendingMoveDialogVisible = ref(false);
const pendingMoveResultVisible = ref(false);
const editingPreviewId = ref<number | null>(null);
const selectedDetailRow = ref<RenamePreview | null>(null);
const metadataPreviewId = ref<number | null>(null);
const aiParsePreviewId = ref<number | null>(null);
const editingTargetName = ref("");
const selectedPreviewIds = ref<number[]>([]);
const selectedPreviewRows = ref<RenamePreview[]>([]);
const selectedPendingFileIds = ref<number[]>([]);
const pendingRenamePreviews = ref<RenameSelectionItem[]>([]);
const metadataCandidates = ref<MetadataMatchResult[]>([]);
const multiSourceMatchRows = ref<MultiSourceMatchRow[]>([]);
const aiParseResult = ref<AiParseResult | null>(null);
const aiParsingPreviewId = ref<number | null>(null);
const aiBatchResults = ref<Record<number, AiParseResult>>({});
const selectedMetadataFields = ref<string[]>(["title", "english_title", "year", "tmdb_id", "imdb_id"]);
const pendingMoveTargetDirectory = ref("");
const activePreviewTab = ref("previews");
const operationProgressVisible = ref(false);
const operationProgress = ref<{ current: number; total: number } | PendingMoveTask | null>(null);
const operationLabels = computed(() => {
  const text = messages.value.renamePreviews;
  return {
    generate: text.generate,
    multiSourceMatch: text.multiSourceMatch,
    multiSourceMatchAll: text.multiSourceMatchAll,
    tmdbMatch: text.tmdbMatch,
    tmdbMatchAll: text.tmdbMatchAll,
    matchModeTmdbAiFallback: text.matchModeTmdbAiFallback,
    aiSelected: text.aiParse.batchSelected,
    aiAll: text.aiParse.batchAll,
    excludeSelected: text.excludeSelected,
    pendingClear: text.pendingFiles.clear,
    pendingMove: text.pendingFiles.move,
    rename: text.rename,
  };
});
type OperationKey = keyof typeof operationLabels.value;
const activeOperationKey = ref<OperationKey | null>(null);
const operationProgressText = computed(() => {
  const progress = operationProgress.value;
  if (!progress) return activeOperationKey.value ? operationLabels.value[activeOperationKey.value] : "";
  return "current" in progress
    ? formatMessage(messages.value.renamePreviews.processing, progress)
    : formatPendingMoveProgress(progress);
});
const operationProgressPercent = ref(0);
const operationProgressLogs = ref<string[]>([]);
const operationBackgroundKind = {
  generate: "preview_generation", tmdbMatch: "tmdb_batch", tmdbMatchAll: "tmdb_batch", matchModeTmdbAiFallback: "tmdb_ai_batch",
  multiSourceMatch: "metadata_batch", multiSourceMatchAll: "metadata_batch", aiSelected: "ai_batch", aiAll: "ai_batch",
} as Partial<Record<OperationKey, string>>;
watch(() => backgroundJobs.submittedJob, job => {
  if (!job || !["queued", "running"].includes(job.status) || !operationProgressVisible.value
    || !activeOperationKey.value || operationBackgroundKind[activeOperationKey.value] !== job.kind || !previewStore.loading) return;
  operationProgressPercent.value = Math.min(95, Math.round(100 * job.completed_count / Math.max(1, job.total_count)));
  operationProgress.value = { current: job.completed_count, total: job.total_count };
}, { deep: true });
const operationResultVisible = ref(false);
const operationResultExpanded = ref(false);
const resultOperationKey = ref<OperationKey | null>(null);
const operationResultTitle = computed(() => resultOperationKey.value
  ? formatMessage(messages.value.renamePreviews.operationResultDialog.completedTitle, {
    operation: operationLabels.value[resultOperationKey.value],
  })
  : "");
const operationResultSuccess = ref(0);
const operationResultFailed = ref(0);
const operationResultSkipped = ref(0);
const operationResultLogs = ref<string[]>([]);
const metadataMatchSource = ref<MetadataMatchSource>("parsed_title");
const multiSourceMatchMode = ref<MultiSourceMatchMode>("fallback");
const multiSourceProviderResults = ref<Record<number, ProviderSearchResult[]>>({});
const aiReadiness = ref<AiReadiness | null>(null);
const defaultSort = { prop: "id", order: "ascending" as const };
const previewTotal = ref(0);
const previewStats = ref<Record<string, number>>({});
const mediaFileCount = ref(0);
const previewsLoading = ref(false);
const batchScopeLoading = ref(false);
const pendingFilesLoading = ref(false);
const pendingTotal = ref(0);
const pendingStats = ref<Record<string, number>>({});
const pendingMoveStatusText = computed(() => {
  const task = pendingFileStore.currentMoveTask;
  return pendingFileStore.moveError || (task ? formatPendingMoveProgress(task) : messages.value.renamePreviews.pendingFiles.queryBeforeRetry);
});
const pendingMoveProgress = computed(() => {
  const task = pendingFileStore.currentMoveTask;
  return task && ["queued", "running"].includes(task.status) && !pendingFileStore.moveError
    ? Math.min(100, Math.round((task.moved_count + task.failed_count) * 100 / Math.max(1, task.total_count))) : null;
});
const operationBusy = computed(() => previewStore.loading || renameOperationStore.loading || pendingFileStore.loading
  || pendingFileStore.moveLoading || pendingFileStore.moveUncertain || ["queued", "running"].includes(pendingFileStore.currentMoveTask?.status ?? "")
  || renameOperationStore.rollbackLoading || renameOperationStore.rollbackUncertain
  || ["queued", "running"].includes(renameOperationStore.currentRollbackPlan?.status ?? "")
  || batchScopeLoading.value || previewsLoading.value || pendingFilesLoading.value
  || renameOperationStore.executionUncertain || ["queued", "running"].includes(renameOperationStore.currentOperation?.status ?? ""));
let previewRequestVersion = 0;
let contextRequestVersion = 0;
let pendingRequestVersion = 0;
let viewActive = true;
const pagedPreviews = computed(() => previewStore.previews);
const pagedMetadataCandidates = computed(() =>
  paginationStore.paginate("metadata-candidates", metadataCandidates.value),
);
const metadataFieldOptions = computed(() => {
  const labels = messages.value.renamePreviews.metadataDialog.fields;
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
  { key: "total", label: messages.value.common.total, value: previewTotal.value, tone: "default" as const },
  { key: "generated", label: messages.value.status.generated, value: previewStats.value.generated ?? 0, tone: "success" as const },
  { key: "needsReview", label: messages.value.status.needsReview, value: previewStats.value.needs_review ?? 0, tone: "warning" as const },
  { key: "edited", label: messages.value.status.edited, value: previewStats.value.edited ?? 0, tone: "edited" as const },
  { key: "renamed", label: messages.value.status.renamed, value: previewStats.value.renamed ?? 0, tone: "renamed" as const },
  { key: "noRename", label: messages.value.status.noRename, value: previewStats.value.no_rename ?? 0, tone: "success" as const },
  { key: "unableRename", label: messages.value.status.unableRename, value: previewStats.value.unable_rename ?? 0, tone: "danger" as const },
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
      mediaFileCount.value > 0,
  ),
);


function mediaTypeLabel(value: string) {
  const labels: Record<string, string> = {
    movie: messages.value.renamePreviews.mediaTypes.movie,
    episode: messages.value.renamePreviews.mediaTypes.episode,
    unknown: messages.value.renamePreviews.mediaTypes.unknown,
  };
  return labels[value] ?? value;
}

function titleSourceLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.value.renamePreviews.titleSources;
  return value ? (labels[value] ?? value) : "-";
}

function recognitionModeLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.value.renamePreviews.recognitionModes;
  return value ? (labels[value] ?? value) : "-";
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    generated: messages.value.status.generated,
    edited: messages.value.status.edited,
    needs_review: messages.value.status.needsReview,
    renamed: messages.value.status.renamed,
    no_rename: messages.value.status.noRename,
    unable_rename: messages.value.status.unableRename,
    excluded: messages.value.status.excluded,
    invalidated: messages.value.tasks.statuses.invalidated,
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
    ready: messages.value.status.ready,
    conflict: messages.value.status.conflict,
    renamed: messages.value.status.renamed,
    rolled_back: messages.value.status.rolledBack,
    failed: messages.value.status.failed,
    invalidated: messages.value.tasks.statuses.invalidated,
  };
  return labels[value] ?? value;
}

function operationStatusTagType(value: string) {
  if (value === "ready" || value === "renamed" || value === "rolled_back") {
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

function rollbackStatusLabel(value: string) {
  return messages.value.renamePreviews.operationDialog.rollbackStatuses[value] ?? value;
}

function rollbackStatusTagType(value: string) {
  if (value === "ready" || value === "executed" || value === "rolled_back") {
    return "success";
  }
  if (value === "checked" || value === "conflict" || value === "partial_failed") {
    return "warning";
  }
  if (value === "failed") {
    return "danger";
  }
  return "info";
}

function pendingReasonLabel(value: string) {
  const labels: Record<string, string> = messages.value.renamePreviews.pendingFiles.reasons;
  return labels[value] ?? value;
}

function metadataStatusLabel(value: string | null) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = messages.value.renamePreviews.metadataStatuses;
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

function providerSearchStatusLabel(value: string) {
  if (value === "success") return messages.value.settings.metadataProviders.statusSuccess;
  if (value === "failed") return messages.value.settings.metadataProviders.statusFailed;
  if (value === "skipped") return messages.value.settings.metadataProviders.statusSkipped;
  return value || "-";
}

function aiParseStatusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = messages.value.renamePreviews.aiParse.statuses;
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

function shouldShowNamingTemplateStatus(row: RenamePreview) {
  return row.naming_template_status === "outdated" || row.naming_template_status === "unknown";
}

function namingTemplateStatusText(row: RenamePreview) {
  return namingTemplateStatusLabel(row.naming_template_status, messages.value.renamePreviews.namingTemplateStatuses);
}

function namingTemplateStatusReason(row: RenamePreview) {
  return namingTemplateStatusTooltip(
    row.naming_template_status,
    row.naming_template_version,
    row.current_naming_template_version,
    messages.value.renamePreviews.namingTemplateStatuses,
  );
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

function candidateSourceLabel(row: MetadataMatchResult) {
  const rawData = row.candidate.raw_data ?? {};
  const sourceLabel = rawData.source_label;
  return typeof sourceLabel === "string" && sourceLabel.trim()
    ? sourceLabel
    : row.candidate.provider;
}

function metadataFieldLabel(field: string) {
  const labels = messages.value.renamePreviews.metadataDialog.fields as Record<string, string>;
  return labels[field] ?? field;
}

function candidateFieldSources(row: MetadataMatchResult) {
  const rawData = row.candidate.raw_data ?? {};
  const fieldSources = rawData.field_sources;
  if (!fieldSources || typeof fieldSources !== "object" || Array.isArray(fieldSources)) {
    return [];
  }
  return Object.entries(fieldSources as Record<string, unknown>)
    .filter(([, value]) => typeof value === "string" && value.trim())
    .map(([field, source]) => ({
      field,
      label: metadataFieldLabel(field),
      source: String(source),
    }));
}

function seasonEpisode(row: { season: number | null; episode: number | null }) {
  if (!row.season && !row.episode) {
    return "-";
  }
  return `S${String(row.season ?? 1).padStart(2, "0")}E${String(row.episode ?? 0).padStart(2, "0")}`;
}

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("rename-previews", event.prop, event.order);
  void loadPreviewPage();
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
    { key: "status", label: messages.value.common.status, value: statusLabel(row.status) },
    { key: "originalName", label: messages.value.renamePreviews.columns.originalName, value: row.file_name },
    { key: "targetName", label: messages.value.renamePreviews.columns.targetName, value: row.current_target_name },
    { key: "parsedTitle", label: messages.value.renamePreviews.columns.parsedTitle, value: row.parsed_title },
    { key: "titleSource", label: messages.value.renamePreviews.columns.titleSource, value: titleSourceLabel(row.title_source) },
    { key: "parentFolderTitle", label: messages.value.renamePreviews.columns.parentFolderTitle, value: row.parent_folder_title ?? "-" },
    { key: "recognitionMode", label: messages.value.renamePreviews.columns.recognitionMode, value: recognitionModeLabel(row.recognition_mode) },
    { key: "titleConflict", label: messages.value.renamePreviews.columns.titleConflict, value: row.title_conflict_message ?? "-" },
    { key: "namingTemplate", label: messages.value.renamePreviews.columns.namingTemplate, value: namingTemplateStatusReason(row) },
    { key: "metadataSource", label: messages.value.renamePreviews.columns.metadataSource, value: row.metadata_source ?? "-" },
    { key: "metadataScore", label: messages.value.renamePreviews.columns.metadataScore, value: `${row.metadata_match_score ?? 0}%` },
    { key: "metadata", label: messages.value.renamePreviews.columns.metadata, value: metadataStatusLabel(row.metadata_match_status) },
    { key: "reason", label: messages.value.renamePreviews.columns.reason, value: row.metadata_message ?? "-" },
    { key: "type", label: messages.value.renamePreviews.type, value: mediaTypeLabel(row.media_type) },
    { key: "year", label: messages.value.renamePreviews.columns.year, value: row.parsed_year ?? "-" },
    { key: "seasonEpisode", label: messages.value.renamePreviews.columns.seasonEpisode, value: seasonEpisode(row) },
    { key: "updatedAt", label: messages.value.renamePreviews.columns.updatedAt, value: formatDateTime(row.updated_at) },
    { key: "sourcePath", label: messages.value.renamePreviews.columns.sourcePath, value: row.file_path },
  ];
});

const operationHasExecuted = computed(() => {
  const status = renameOperationStore.currentOperation?.status;
  return Boolean(status && status !== "dry_run");
});

const operationDialogTitle = computed(() =>
  operationHasExecuted.value
    ? messages.value.renamePreviews.operationDialog.resultTitle
    : messages.value.renamePreviews.operationDialog.title,
);

const operationResultAlert = computed(() => {
  const operation = renameOperationStore.currentOperation;
  if (!operation || !operationHasExecuted.value) {
    return null;
  }

  const summary = formatMessage(messages.value.renamePreviews.operationDialog.summary, {
    total: operation.total_count,
    renamed: operation.renamed_count,
    failed: operation.failed_count,
    conflict: operation.conflict_count,
  });

  if (["queued", "running", "interrupted"].includes(operation.status)) {
    return {
      type: "warning" as const,
      title: messages.value.renamePreviews.operationDialog[operation.status as "queued" | "running" | "interrupted"],
      description: operation.error_message || summary,
    };
  }

  if (operation.failed_count > 0 && operation.renamed_count > 0) {
    return {
      type: "warning" as const,
      title: messages.value.renamePreviews.operationDialog.partialFailed,
      description: `${summary} ${messages.value.renamePreviews.operationDialog.fixSuggestion}`,
    };
  }

  if (operation.failed_count > 0) {
    return {
      type: "error" as const,
      title: messages.value.renamePreviews.operationDialog.failed,
      description: `${summary} ${messages.value.renamePreviews.operationDialog.fixSuggestion}`,
    };
  }

  if (operation.status !== "completed") {
    return {
      type: "warning" as const,
      title: operation.status === "invalidated" ? messages.value.tasks.statuses.invalidated : messages.value.renamePreviews.operationDialog.failed,
      description: operation.error_message || `${summary} ${messages.value.renamePreviews.operationDialog.fixSuggestion}`,
    };
  }

  return {
    type: "success" as const,
    title: messages.value.renamePreviews.operationDialog.success,
    description: summary,
  };
});
const rollbackPlan = computed(() => renameOperationStore.currentRollbackPlan);
const canCreateRollbackPlan = computed(() =>
  Boolean(operationHasExecuted.value && !["queued", "running", "interrupted"].includes(renameOperationStore.currentOperation?.status ?? "")
    && renameOperationStore.currentOperation?.renamed_count && !rollbackPlan.value),
);
const canDryRunRollbackPlan = computed(() =>
  Boolean(
    rollbackPlan.value &&
      ["draft", "checked"].includes(rollbackPlan.value.status) && !operationBusy.value &&
      canExecuteRollback.value,
  ),
);
const canExecuteRollbackPlan = computed(() =>
  Boolean(rollbackPlan.value?.status === "checked" && rollbackPlan.value.executable_count > 0 && canExecuteRollback.value && !operationBusy.value),
);

function clearPreviewPage() {
  previewRequestVersion += 1;
  contextRequestVersion += 1;
  pendingRequestVersion += 1;
  previewStore.previews = [];
  previewTotal.value = 0;
  previewStats.value = {};
  mediaFileCount.value = 0;
  previewsLoading.value = false;
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
  pendingFileStore.pendingFiles = [];
  selectedPendingFileIds.value = [];
  pendingTotal.value = 0;
  pendingStats.value = {};
  pendingFilesLoading.value = false;
}

watch(() => [previewStore.filters.media_source_id, previewStore.filters.scan_job_id], clearPreviewPage, { flush: "sync" });

async function loadPreviewPage(resetPage = true) {
  if (!viewActive) return;
  if (!previewStore.filters.scan_job_id) { clearPreviewPage(); return; }
  const pagination = paginationStore.getState("rename-previews");
  if (pagination.pageSize === 0) paginationStore.setPageSize("rename-previews", 50);
  if (resetPage) paginationStore.setPage("rename-previews", 1);
  const filters = { ...previewStore.filters };
  const sort = tableSortStore.sorts["rename-previews"] ?? defaultSort;
  const version = ++previewRequestVersion;
  previewsLoading.value = true;
  selectedPreviewIds.value = []; selectedPreviewRows.value = [];
  try {
    const result = await fetchRenamePreviewsPage({ ...filters, page: pagination.currentPage, page_size: pagination.pageSize,
      sort_by: !sort.order || sort.prop === "__sequence" ? "id" : sort.prop,
      sort_order: sort.order === "descending" ? "desc" : "asc" });
    if (version !== previewRequestVersion || JSON.stringify(filters) !== JSON.stringify(previewStore.filters)) return;
    previewStore.previews = result.items;
    previewTotal.value = result.total;
    previewStats.value = result.stats;
    paginationStore.setPage("rename-previews", result.page);
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (version === previewRequestVersion && JSON.stringify(filters) === JSON.stringify(previewStore.filters)) {
      previewStore.previews = []; previewTotal.value = 0; previewStats.value = {};
      ElMessage.error(messages.value.previewPaging.loadFailed);
    }
  } finally {
    if (version === previewRequestVersion) previewsLoading.value = false;
  }
}

async function readAllTargets(action: PreviewSelectionAction): Promise<RenameSelectionItem[] | null> {
  if (!viewActive || !previewStore.filters.scan_job_id) return null;
  const filters = { ...previewStore.filters };
  try {
    const result = await fetchRenamePreviewSelection({ ...filters, action });
    if (!viewActive || JSON.stringify(filters) !== JSON.stringify(previewStore.filters)) {
      if (viewActive) ElMessage.warning(messages.value.previewPaging.scopeChanged);
      return null;
    }
    if (result.over_limit) {
      ElMessage.warning(formatMessage(messages.value.previewPaging.overBatchLimit, { count: result.total, limit: result.limit }));
      return null;
    }
    return result.items;
  } catch (error) {
    if (isSessionChangedError(error)) return null;
    if (viewActive) ElMessage.error(messages.value.previewPaging.loadFailed);
    return null;
  }
}

async function runBatchCommand(operation: () => Promise<void>) {
  if (operationBusy.value) return;
  batchScopeLoading.value = true;
  try {
    await operation();
    if (viewActive) await loadPreviewPage(false);
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (error !== "cancel" && error !== "close") {
      operationProgressVisible.value = false;
      if (!previewStore.errorMessage) ElMessage.error(messages.value.errors.unknown);
    }
  } finally {
    batchScopeLoading.value = false;
  }
}

async function refreshPreviews() {
  if (!previewStore.filters.scan_job_id) {
    clearPreviewPage();
    mediaStore.mediaFiles = [];
    pendingFileStore.pendingFiles = [];
    return;
  }
  const sourceId = previewStore.filters.media_source_id, jobId = previewStore.filters.scan_job_id;
  const version = ++contextRequestVersion;
  const current = () => viewActive && version === contextRequestVersion;
  await loadPreviewPage();
  if (!current()) return;
  try {
    const files = await fetchMediaFilesPage({ media_source_id: sourceId, scan_job_id: jobId, page_size: 1 });
    if (!current()) return;
    mediaFileCount.value = files.total;
    await loadPendingPage();
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (current()) {
      mediaFileCount.value = 0;
      pendingFileStore.pendingFiles = [];
      pendingFileStore.refreshFailed = true;
      ElMessage.error(messages.value.previewPaging.loadFailed);
    }
  }
}

async function loadPendingPage(resetPage = true) {
  if (!viewActive || !previewStore.filters.scan_job_id) return;
  const version = ++pendingRequestVersion;
  const sourceId = previewStore.filters.media_source_id, jobId = previewStore.filters.scan_job_id;
  const pagination = paginationStore.getState("pending-files");
  if (pagination.pageSize === 0) paginationStore.setPageSize("pending-files", 50);
  if (resetPage) paginationStore.setPage("pending-files", 1);
  selectedPendingFileIds.value = [];
  pendingFilesLoading.value = true;
  try {
    const result = await fetchPendingFilesPage({ media_source_id: sourceId, scan_job_id: jobId,
      page: pagination.currentPage, page_size: pagination.pageSize });
    if (!viewActive || version !== pendingRequestVersion) return;
    pendingFileStore.filters = { media_source_id: sourceId, scan_job_id: jobId };
    pendingFileStore.pendingFiles = result.items;
    pendingFileStore.refreshFailed = false;
    pendingTotal.value = result.total;
    pendingStats.value = result.stats;
    paginationStore.setPage("pending-files", result.page);
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (viewActive && version === pendingRequestVersion) {
      pendingFileStore.pendingFiles = []; pendingTotal.value = 0; pendingStats.value = {};
      pendingFileStore.refreshFailed = true;
      ElMessage.error(messages.value.previewPaging.loadFailed);
    }
  } finally {
    if (version === pendingRequestVersion) pendingFilesLoading.value = false;
  }
}

async function generatePreviews() {
  const scanJobId = previewStore.filters.scan_job_id, sourceId = previewStore.filters.media_source_id;
  if (!scanJobId) return;
  const count = Math.max(previewTotal.value, mediaFileCount.value, 1);
  try {
    await confirmResourceOperation(messages.value.renamePreviews.generate, count);
  } catch (error) {
    if (error === "cancel" || error === "close" || isSessionChangedError(error)) return;
    throw error;
  }
  resetOperationProgress(count, "generate");
  await previewStore.generateFromScan(scanJobId, sourceId);
  await loadPreviewPage(false);
  const summary = previewStore.generationSummary;
  if (!summary || previewStore.errorMessage) {
    operationProgressVisible.value = false;
    ElMessage.error(previewStore.errorMessage || messages.value.errors.previewGenerationFailed);
    return;
  }
  operationProgressLogs.value.push(
    formatMessage(messages.value.renamePreviews.operationSummary, {
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
  clearPreviewPage();
  mediaStore.mediaFiles = [];
  pendingFileStore.pendingFiles = [];
}

async function resetRenamePreviews() {
  previewStore.filters = {};
  clearPreviewPage();
  mediaStore.mediaFiles = [];
  pendingFileStore.pendingFiles = [];
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
}

function handleSelectionChange(rows: RenamePreview[]) {
  selectedPreviewRows.value = rows;
  selectedPreviewIds.value = rows.map((row) => row.id);
}

function handlePendingSelectionChange(rows: PendingFile[]) {
  selectedPendingFileIds.value = rows.filter((row) => row.status === "pending").map((row) => row.id);
}

function resetOperationProgress(total: number, operation: OperationKey) {
  operationProgressVisible.value = total > 0;
  operationProgressPercent.value = total > 0 ? 5 : 0;
  activeOperationKey.value = operation;
  operationProgress.value = total > 0 ? { current: 0, total } : null;
  operationProgressLogs.value = [];
  multiSourceMatchRows.value = [];
}

function finishOperationProgress(total: number, success: number, failed: number, skipped = 0) {
  operationProgressPercent.value = 100;
  operationProgress.value = { current: total, total };
  resultOperationKey.value = activeOperationKey.value;
  operationResultSuccess.value = success;
  operationResultFailed.value = failed;
  operationResultSkipped.value = Math.max(skipped, total - success - failed);
  operationResultLogs.value = [...operationProgressLogs.value];
  operationResultExpanded.value = false;
  operationResultVisible.value = true;
  operationProgressVisible.value = false;
}

function operationButtonLabel(key: OperationKey, runningLabel: string, label = operationLabels.value[key]) {
  if (operationProgressVisible.value && activeOperationKey.value === key) {
    return formatMessage(messages.value.renamePreviews.operationResultDialog.runningButton, {
      operation: runningLabel,
      percent: operationProgressPercent.value,
    });
  }
  return label;
}

const aiBatchReady = computed(() =>
  Boolean(aiReadiness.value?.ready),
);

const aiBatchBlockMessage = computed(() => {
  if (!aiReadiness.value || aiReadiness.value.status === "untested") {
    return messages.value.renamePreviews.aiParse.guardUntested;
  }
  if (aiReadiness.value.status === "stale") {
    return messages.value.renamePreviews.aiParse.guardStale;
  }
  return aiReadiness.value.message || messages.value.renamePreviews.aiParse.guardFailed;
});

const aiBatchStatusLabel = computed(() => {
  if (aiBatchReady.value) {
    return "";
  }
  if (!aiReadiness.value || aiReadiness.value.status === "untested") {
    return messages.value.renamePreviews.aiParse.statusUntested;
  }
  if (aiReadiness.value.status === "stale") {
    return messages.value.renamePreviews.aiParse.statusStale;
  }
  return messages.value.renamePreviews.aiParse.statusFailed;
});

function aiProviderSummary() {
  return aiReadiness.value?.message || messages.value.renamePreviews.aiParse.providerUnavailable;
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
  const confirmCurrentScope = confirmationScopeGuard();
  await ElMessageBox.confirm(
    formatMessage(messages.value.renamePreviews.confirmOperation, { count, operation }),
    messages.value.renamePreviews.confirmOperationTitle,
    {
      type: "warning",
      confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel,
    },
  );
  confirmCurrentScope();
}

async function confirmAiBatchOperation(operation: string, count: number) {
  const confirmCurrentScope = confirmationScopeGuard();
  await ElMessageBox.confirm(
    formatMessage(messages.value.renamePreviews.aiParse.batchConfirm, {
      count,
      operation,
      providerSummary: aiProviderSummary(),
    }),
    messages.value.renamePreviews.aiParse.batchConfirmTitle,
    {
      type: "warning",
      confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel,
      dangerouslyUseHTMLString: false,
    },
  );
  confirmCurrentScope();
}

async function confirmAiFallbackOperation(count: number) {
  const confirmCurrentScope = confirmationScopeGuard();
  await ElMessageBox.confirm(
    formatMessage(messages.value.renamePreviews.aiParse.fallbackConfirm, {
      count,
      providerSummary: aiProviderSummary(),
    }),
    messages.value.renamePreviews.aiParse.batchConfirmTitle,
    {
      type: "warning",
      confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel,
      dangerouslyUseHTMLString: false,
    },
  );
  confirmCurrentScope();
}

function confirmationScopeGuard() {
  const filters = JSON.stringify(previewStore.filters);
  const selectedIds = JSON.stringify(selectedPreviewIds.value);
  const source = metadataMatchSource.value, mode = multiSourceMatchMode.value;
  return () => {
    if (!viewActive || filters !== JSON.stringify(previewStore.filters)
      || selectedIds !== JSON.stringify(selectedPreviewIds.value)
      || source !== metadataMatchSource.value || mode !== multiSourceMatchMode.value) {
      if (viewActive) ElMessage.warning(messages.value.previewPaging.scopeChanged);
      throw "cancel";
    }
  };
}

async function runRenameDryRun() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await renameOperationStore.runDryRun(selectedPreviewIds.value);
  selectedOperationLogTaskId.value = renameOperationStore.currentOperation?.id ?? null;
  operationDialogVisible.value = true;
}

async function runRenameDryRunForPreviews(previews: RenameSelectionItem[]) {
  const renameablePreviews = previews.filter(canPrepareRename);
  if (renameablePreviews.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.noExecutable);
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
    await confirmResourceOperation(messages.value.renamePreviews.rename, selectedPreviewRows.value.length);
  }
  await runRenameDryRunForPreviews(selectedPreviewRows.value);
}

async function executeAllPreviews() {
  const renameablePreviews = await readAllTargets("rename");
  if (!renameablePreviews) return;
  if (renameablePreviews.length > 0) {
    await confirmResourceOperation(messages.value.renamePreviews.executeAll, renameablePreviews.length);
  }
  await runRenameDryRunForPreviews(renameablePreviews);
}

async function executeSinglePreview(row: RenamePreview) {
  await runRenameDryRunForPreviews([row]);
}

async function matchMetadata(row: RenamePreview) {
  await confirmResourceOperation(messages.value.renamePreviews.tmdbMatch, 1);
  metadataPreviewId.value = row.id;
  const updated = await previewStore.matchMetadata(row.id, metadataMatchSource.value);
  metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id, metadataMatchSource.value);
  if (updated.metadata_match_status && updated.metadata_match_status !== "failed") {
    ElMessage.success(messages.value.renamePreviews.metadataDialog.matched);
  }
  if (!metadataCandidates.value.length) {
    ElMessage.warning(updated.metadata_message || messages.value.renamePreviews.metadataDialog.empty);
  }
}

async function openMetadataBackfill(row: RenamePreview) {
  metadataPreviewId.value = row.id;
  metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id, metadataMatchSource.value);
  if (metadataCandidates.value.length > 0) {
    metadataDialogVisible.value = true;
    return;
  }
  ElMessage.warning(row.metadata_message || messages.value.renamePreviews.metadataDialog.empty);
}

function appendMetadataLogs(result: { items: RenamePreview[]; failed_items: Array<{ id: number; message: string }> }) {
  const failedIds = new Set(result.failed_items.map(item => item.id));
  result.items.forEach((item) => {
    if (failedIds.has(item.id)) return;
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.metadataDialog.matchSuccessLog, {
        name: item.current_target_name,
        score: item.metadata_match_score ?? 0,
      }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.metadataDialog.matchFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
}

function cacheMultiSourceProviderResults(result: BatchMultiSourceMatchResult) {
  const next = { ...multiSourceProviderResults.value };
  result.items.forEach((item) => {
    next[item.preview.id] = item.provider_results;
  });
  multiSourceProviderResults.value = next;
}

function cacheMultiSourceMatchRows(result: BatchMultiSourceMatchResult) {
  multiSourceMatchRows.value = buildMultiSourceMatchRows(result);
}

function openMultiSourceMatchResults() {
  if (multiSourceMatchRows.value.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.metadataDialog.empty);
    return;
  }
  operationResultVisible.value = false;
  multiSourceResultDialogVisible.value = true;
}

function multiSourceProviderSummary(row: MultiSourceMatchRow) {
  return row.providerResults
    .map((item) => `${item.label || item.provider} ${item.candidate_count}`)
    .join(" · ");
}

function appendMultiSourceLogs(result: BatchMultiSourceMatchResult) {
  operationProgressLogs.value.push(
    formatMessage(messages.value.renamePreviews.multiSourceSummaryLog, {
      success: result.success_count,
      failed: result.failed_count,
      skipped: result.blocked_count + result.skipped_count,
    }),
  );
  result.items.forEach((item) => {
    item.provider_results.forEach((providerResult) => {
      operationProgressLogs.value.push(
        formatMessage(messages.value.renamePreviews.multiSourceProviderLog, {
          provider: providerResult.label || providerResult.provider,
          status: providerSearchStatusLabel(providerResult.status),
          count: providerResult.candidate_count,
          message: providerResult.message,
        }),
      );
    });
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.metadataDialog.matchFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
}


async function confirmMultiSourceOperation(count: number) {
  const confirmCurrentScope = confirmationScopeGuard();
  await ElMessageBox.confirm(
    formatMessage(messages.value.renamePreviews.multiSourceConfirm, { count }),
    messages.value.renamePreviews.confirmOperationTitle,
    {
      confirmButtonText: messages.value.common.confirm,
      cancelButtonText: messages.value.common.cancel,
      type: "warning",
    },
  );
  confirmCurrentScope();
}

async function matchMultiSource(row: RenamePreview) {
  await confirmMultiSourceOperation(1);
  metadataPreviewId.value = row.id;
  const result = await previewStore.matchMultiSource(row.id, metadataMatchSource.value, multiSourceMatchMode.value);
  multiSourceProviderResults.value = {
    ...multiSourceProviderResults.value,
    [result.preview.id]: result.provider_results,
  };
  metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id, metadataMatchSource.value);
  if (!metadataCandidates.value.length) {
    ElMessage.warning(result.preview.metadata_message || messages.value.renamePreviews.metadataDialog.empty);
  } else {
    metadataDialogVisible.value = true;
  }
}

async function matchSelectedMultiSource() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await confirmMultiSourceOperation(selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, "multiSourceMatch");
  const result = await previewStore.matchMultiSourceBatch(
    selectedPreviewIds.value,
    metadataMatchSource.value,
    multiSourceMatchMode.value,
  );
  cacheMultiSourceProviderResults(result);
  cacheMultiSourceMatchRows(result);
  appendMultiSourceLogs(result);
  finishOperationProgress(
    result.total_count,
    result.success_count,
    result.failed_count,
    result.blocked_count + result.skipped_count,
  );
}

async function matchAllCurrentMultiSource() {
  const targets = await readAllTargets("multi_source");
  if (!targets) return;
  if (targets.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.metadataDialog.empty);
    return;
  }
  await confirmMultiSourceOperation(targets.length);
  resetOperationProgress(targets.length, "multiSourceMatchAll");
  const result = await previewStore.matchMultiSourceBatch(
    targets.map((item) => item.id),
    metadataMatchSource.value,
    multiSourceMatchMode.value,
  );
  cacheMultiSourceProviderResults(result);
  cacheMultiSourceMatchRows(result);
  appendMultiSourceLogs(result);
  finishOperationProgress(
    result.total_count,
    result.success_count,
    result.failed_count,
    result.blocked_count + result.skipped_count,
  );
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
    const title = bestCandidate?.title || messages.value.renamePreviews.aiParse.noCandidateTitle;
    const confidence = bestCandidate?.confidence ?? 0;
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.aiParse.batchSuccessLog, {
        id: item.id,
        status: aiParseStatusLabel(item.result.status),
        title,
        confidence,
      }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.aiParse.batchFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
  if (result.usage && Object.keys(result.usage).length > 0) {
    operationProgressLogs.value.push(
      `${messages.value.renamePreviews.aiParse.usage}${labelSeparator.value}${JSON.stringify(result.usage)}`,
    );
  }
}

function appendMetadataAiFallbackLogs(result: { total_count: number; fallback_count: number; metadata: { items: RenamePreview[]; failed_items: Array<{ id: number; message: string }> }; ai: BatchAiParseResult }) {
  operationProgressLogs.value.push(
    formatMessage(messages.value.renamePreviews.aiParse.fallbackSummaryLog, {
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
  await confirmResourceOperation(messages.value.renamePreviews.tmdbMatch, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, "tmdbMatch");
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
  resetOperationProgress(selectedPreviewIds.value.length, "matchModeTmdbAiFallback");
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
  await confirmAiBatchOperation(messages.value.renamePreviews.aiParse.batchSelected, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, "aiSelected");
  const result = await previewStore.parseBatchWithAi(selectedPreviewIds.value, metadataMatchSource.value);
  cacheAiBatchResult(result);
  appendAiBatchLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count, result.blocked_count + result.skipped_count);
}

async function parseSelectedWithAiFromToolbar() {
  await parseSelectedWithAi();
}

async function parseAllWithAi() {
  const targets = await readAllTargets("ai");
  if (!targets) return;
  if (targets.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.aiParse.noBatchTargets);
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiBatchOperation(messages.value.renamePreviews.aiParse.batchAll, targets.length);
  resetOperationProgress(targets.length, "aiAll");
  const result = await previewStore.parseBatchWithAi(targets.map((item) => item.id), metadataMatchSource.value);
  cacheAiBatchResult(result);
  appendAiBatchLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count, result.blocked_count + result.skipped_count);
}

async function matchAllTmdbOnly() {
  const targets = await readAllTargets("tmdb");
  if (!targets) return;
  const unmatchedCount = targets.length;
  if (unmatchedCount === 0) {
    ElMessage.warning(messages.value.renamePreviews.metadataDialog.empty);
    return;
  }
  await confirmResourceOperation(messages.value.renamePreviews.tmdbMatchAll, unmatchedCount);
  resetOperationProgress(unmatchedCount, "tmdbMatchAll");
  const result = await previewStore.matchMetadataBatch(targets.map(item => item.id), metadataMatchSource.value);
  appendMetadataLogs(result);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count);
}

async function matchAllTmdbWithAiFallback() {
  const targets = await readAllTargets("tmdb");
  if (!targets) return;
  const unmatchedCount = targets.length;
  if (unmatchedCount === 0) {
    ElMessage.warning(messages.value.renamePreviews.metadataDialog.empty);
    return;
  }
  if (!ensureAiBatchReady()) {
    return;
  }
  await confirmAiFallbackOperation(unmatchedCount);
  resetOperationProgress(unmatchedCount, "matchModeTmdbAiFallback");
  const result = await previewStore.matchMetadataBatchWithAiFallback(targets.map(item => item.id), metadataMatchSource.value);
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
    await runBatchCommand(matchAllTmdbOnly);
    return;
  }
  if (command === "selected_tmdb_ai") {
    await runBatchCommand(matchSelectedTmdbWithAiFallback);
    return;
  }
  if (command === "all_tmdb_ai") {
    await runBatchCommand(matchAllTmdbWithAiFallback);
  }
}

async function handleAiDropdownCommand(command: string) {
  if (command === "all_ai") {
    await runBatchCommand(parseAllWithAi);
  }
}

async function handleMultiSourceDropdownCommand(command: string) {
  if (command === "all_multi_source") {
    await runBatchCommand(matchAllCurrentMultiSource);
  }
}

async function handleRenameDropdownCommand(command: string) {
  if (command === "all_rename") {
    await runBatchCommand(executeAllPreviews);
  }
}

async function excludeSelectedPreviews() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await confirmResourceOperation(messages.value.renamePreviews.excludeSelected, selectedPreviewIds.value.length);
  resetOperationProgress(selectedPreviewIds.value.length, "excludeSelected");
  const result = await previewStore.excludePreviews(selectedPreviewIds.value);
  result.items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.excludeSuccessLog, { name: item.file_name }),
    );
  });
  result.failed_items.forEach((item) => {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.excludeFailedLog, {
        id: item.id,
        reason: item.message,
      }),
    );
  });
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
  await loadPreviewPage(false);
  await loadPendingPage(false);
  finishOperationProgress(result.total_count, result.success_count, result.failed_count);
}

async function excludeSinglePreview(row: RenamePreview) {
  await confirmResourceOperation(messages.value.renamePreviews.excludeSingle, 1);
  await previewStore.excludePreview(row.id);
  await loadPreviewPage(false);
  await loadPendingPage(false);
  ElMessage.success(messages.value.renamePreviews.excludeSingleSuccess);
}

async function applyMetadataCandidate(match: MetadataMatchResult) {
  if (!metadataPreviewId.value) {
    return;
  }
  await previewStore.applyMetadataCandidate(metadataPreviewId.value, match, selectedMetadataFields.value);
  metadataDialogVisible.value = false;
  metadataCandidates.value = [];
  ElMessage.success(messages.value.renamePreviews.metadataDialog.selectSuccess);
  await loadPreviewPage(false);
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
      ElMessage.success(messages.value.renamePreviews.aiParse.success);
    } else if (result.status === "blocked") {
      ElMessage.warning(result.message || messages.value.renamePreviews.aiParse.blocked);
    } else {
      ElMessage.error(result.message || messages.value.renamePreviews.aiParse.failed);
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
      ElMessage.success(messages.value.renamePreviews.aiParse.success);
    } else if (result.status === "blocked") {
      ElMessage.warning(result.message || messages.value.renamePreviews.aiParse.blocked);
    } else {
      ElMessage.error(result.message || messages.value.renamePreviews.aiParse.failed);
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
  ElMessage.success(messages.value.renamePreviews.aiParse.selectSuccess);
  await loadPreviewPage(false);
}

async function removePendingFile(row: { id: number }) {
  await pendingFileStore.removePendingFile(row.id);
  await loadPendingPage(false);
  ElMessage.success(messages.value.renamePreviews.pendingFiles.removed);
}

async function clearPendingFiles() {
  const count = pendingStats.value.pending ?? 0;
  if (count === 0 || !previewStore.filters.scan_job_id || pendingFilesLoading.value) return;
  if (count > 0) {
    await confirmResourceOperation(messages.value.renamePreviews.pendingFiles.clear, count);
    resetOperationProgress(count, "pendingClear");
  }
  const removedCount = await pendingFileStore.clearPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  if (count > 0) {
    operationProgressLogs.value.push(
      formatMessage(messages.value.renamePreviews.operationSummary, { success: removedCount, failed: 0 }),
    );
    finishOperationProgress(count, removedCount, 0);
  }
  ElMessage.success(messages.value.renamePreviews.pendingFiles.cleared);
  await loadPendingPage(false);
}

function openPendingMoveDialog() {
  if (operationBusy.value) return;
  if (selectedPendingFileIds.value.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.pendingFiles.selectFirst);
    return;
  }
  pendingMoveTargetDirectory.value = "";
  pendingMoveDialogVisible.value = true;
}

async function moveSelectedPendingFiles() {
  if (operationBusy.value) return;
  if (!pendingMoveTargetDirectory.value.trim()) {
    ElMessage.warning(messages.value.renamePreviews.pendingFiles.targetDirectoryPlaceholder);
    return;
  }
  const ids = [...selectedPendingFileIds.value];
  const target = pendingMoveTargetDirectory.value.trim();
  const scope = JSON.stringify(previewStore.filters);
  try {
    await confirmResourceOperation(messages.value.renamePreviews.pendingFiles.move, ids.length);
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (error !== "cancel" && error !== "close") ElMessage.error(messages.value.errors.unknown);
    return;
  }
  if (!viewActive || operationBusy.value || JSON.stringify(ids) !== JSON.stringify(selectedPendingFileIds.value)
      || scope !== JSON.stringify(previewStore.filters) || target !== pendingMoveTargetDirectory.value.trim()) return;
  resetOperationProgress(ids.length, "pendingMove");
  let logOpened = false;
  await pendingFileStore.executePendingMove(ids, target, task => {
    updatePendingMoveProgress(task);
    pendingMoveDialogVisible.value = false;
    if (!logOpened && canReadLogs.value) { openPendingMoveLog(); logOpened = true; }
  }, async requestId => {
    await router.replace({ query: { ...route.query, operation_id: undefined, rollback_plan_id: undefined,
      pending_move_request_id: requestId, pending_move_task_id: undefined } });
  });
  if (!viewActive) return;
  operationProgressVisible.value = false;
  if (pendingFileStore.moveError) {
    ElMessage.error(pendingFileStore.moveError);
    if (!pendingFileStore.moveUncertain) await router.replace({ query: { ...route.query, pending_move_request_id: undefined } });
  } else if (pendingFileStore.currentMoveTask?.status === "completed") {
    selectedPendingFileIds.value = [];
    ElMessage.success(messages.value.renamePreviews.pendingFiles.moved);
  } else {
    ElMessage.warning(messages.value.renamePreviews.pendingFiles.incomplete);
  }
  await loadPendingPage(false);
}

function updatePendingMoveProgress(task: PendingMoveTask) {
  if (!viewActive) return;
  const processed = task.moved_count + task.failed_count;
  operationProgressPercent.value = task.total_count ? Math.round(processed / task.total_count * 100) : 0;
  operationProgress.value = task;
}

function formatPendingMoveProgress(task: PendingMoveTask) {
  return formatMessage(messages.value.renamePreviews.pendingFiles.progress,
    { success: task.moved_count, total: task.total_count, failed: task.failed_count,
      remaining: Math.max(0, task.total_count - task.moved_count - task.failed_count) });
}

function openPendingMoveLog() {
  if (!pendingFileStore.currentMoveTask || !canReadLogs.value) return;
  selectedOperationLogTaskType.value = "pending_move";
  selectedOperationLogTaskId.value = pendingFileStore.currentMoveTask.id;
  operationLogVisible.value = true;
}

async function refreshPendingMoveTask(taskId?: number, requestId?: string) {
  await pendingFileStore.refreshPendingMove(taskId, requestId, updatePendingMoveProgress);
  if (!viewActive) return;
  if (pendingFileStore.moveError) ElMessage.error(pendingFileStore.moveError);
  await loadPendingPage(false);
}

async function continueWithoutEmptyTargets() {
  const filteredPreviews = removeEmptyTargetNamePreviews(pendingRenamePreviews.value);
  emptyTargetDialogVisible.value = false;
  pendingRenamePreviews.value = [];

  if (filteredPreviews.length === 0) {
    ElMessage.warning(messages.value.renamePreviews.noExecutableAfterRemove);
    return;
  }

  selectedPreviewIds.value = filteredPreviews.map((preview) => preview.id);
  await runRenameDryRun();
}

async function executeRenameOperation() {
  const total = renameOperationStore.currentOperation?.ready_count ?? 0;
  resetOperationProgress(total, "rename");
  let logOpened = false;
  await renameOperationStore.executeCurrentOperation((operation) => {
    updateRenameProgress(operation);
    if (!logOpened && canReadLogs.value) {
      openRenameOperationLog();
      logOpened = true;
    }
  });
  if (!viewActive) return;
  const operation = renameOperationStore.currentOperation;
  if (renameOperationStore.errorMessage) {
    operationProgressVisible.value = false;
    ElMessage.error(renameOperationStore.errorMessage);
    return;
  }
  if (operation?.status === "interrupted") {
    operationProgressVisible.value = false;
    ElMessage.warning(operation.error_message || messages.value.renamePreviews.operationDialog.interrupted);
    await refreshPreviews();
    return;
  }
  if (operation?.failed_count && operation.renamed_count > 0) {
    ElMessage.warning(messages.value.renamePreviews.operationDialog.partialFailed);
  } else if (operation?.failed_count) {
    ElMessage.error(messages.value.renamePreviews.operationDialog.failed);
  } else if (operation?.status === "completed") {
    ElMessage.success(messages.value.renamePreviews.operationDialog.success);
  } else if (operation) {
    ElMessage.warning(operation.error_message || messages.value.renamePreviews.operationDialog.failed);
  }
  if (operation) {
    operation.items.forEach((item) => {
      const reason = item.message ? `${language.value === "en-US" ? ", " : "，"}${item.message}` : "";
      operationProgressLogs.value.push(`${operationStatusLabel(item.status)}${labelSeparator.value}${item.target_path}${reason}`);
    });
    finishOperationProgress(operation.total_count, operation.renamed_count, operation.failed_count);
  }
  await refreshPreviews();
}

function updateRenameProgress(operation: NonNullable<typeof renameOperationStore.currentOperation>) {
  if (!viewActive) return;
  const processed = operation.renamed_count + operation.failed_count;
  const total = operation.total_count - operation.conflict_count;
  activeOperationKey.value = "rename";
  operationProgressVisible.value = ["queued", "running"].includes(operation.status);
  operationProgressPercent.value = total > 0 ? Math.min(99, Math.round(processed / total * 100)) : 0;
  operationProgress.value = { current: processed, total };
}

async function refreshRenameOperation() {
  await renameOperationStore.refreshCurrentOperation(updateRenameProgress);
  if (!viewActive) return;
  if (renameOperationStore.errorMessage) ElMessage.error(renameOperationStore.errorMessage);
  await refreshPreviews();
}

function openRenameOperationLog() {
  const operationId = renameOperationStore.currentOperation?.id;
  if (!operationId) {
    return;
  }
  selectedOperationLogTaskId.value = operationId;
  selectedOperationLogTaskType.value = "rename_operation";
  operationLogVisible.value = true;
}

function openRollbackOperationLog() {
  const planId = renameOperationStore.currentRollbackPlan?.id;
  if (!planId) {
    return;
  }
  selectedOperationLogTaskId.value = planId;
  selectedOperationLogTaskType.value = "rollback_plan";
  operationLogVisible.value = true;
}

async function createRollbackPlan() {
  await renameOperationStore.createRollbackPlan();
  if (renameOperationStore.rollbackErrorMessage) {
    ElMessage.error(renameOperationStore.rollbackErrorMessage);
    return;
  }
  ElMessage.success(messages.value.renamePreviews.operationDialog.rollbackCreateSuccess);
}

async function dryRunRollbackPlan() {
  await renameOperationStore.dryRunRollbackPlan();
  if (renameOperationStore.rollbackErrorMessage) {
    ElMessage.error(renameOperationStore.rollbackErrorMessage);
    return;
  }
  ElMessage.success(messages.value.renamePreviews.operationDialog.rollbackDryRunSuccess);
}

async function executeRollbackPlan() {
  if (!canExecuteRollbackPlan.value) return;
  try {
    await ElMessageBox.confirm(
      messages.value.renamePreviews.operationDialog.rollbackExecuteConfirm,
      messages.value.renamePreviews.operationDialog.rollbackTitle,
      {
        type: "warning",
        confirmButtonText: messages.value.common.confirm,
        cancelButtonText: messages.value.common.cancel,
      },
    );
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (error === "cancel" || error === "close") return;
    throw error;
  }
  if (!viewActive || !canExecuteRollbackPlan.value) return;
  let logOpened = false;
  await renameOperationStore.executeRollbackPlan(() => {
    if (viewActive && !logOpened && canReadLogs.value) {
      openRollbackOperationLog();
      logOpened = true;
    }
  });
  if (!viewActive) return;
  if (renameOperationStore.rollbackErrorMessage) {
    ElMessage.error(renameOperationStore.rollbackErrorMessage);
    return;
  }
  if (renameOperationStore.currentRollbackPlan?.status === "executed") {
    ElMessage.success(messages.value.renamePreviews.operationDialog.rollbackExecuteSuccess);
  } else {
    ElMessage.warning(messages.value.renamePreviews.operationDialog.rollbackExecuteIncomplete);
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
  await loadPreviewPage(false);
}

async function applyRouteContext() {
  if (!viewActive) return;
  const routeVersion = ++routeContextVersion;
  let readiness: AiReadiness | null = null;
  try {
    readiness = await fetchAiReadiness();
  } catch (error) {
    if (isSessionChangedError(error)) return;
    readiness = null;
  }
  if (!viewActive || routeVersion !== routeContextVersion) return;
  aiReadiness.value = readiness;
  const moveId = Number(route.query.pending_move_task_id);
  const moveRequest = typeof route.query.pending_move_request_id === "string" ? route.query.pending_move_request_id : undefined;
  const hasMoveId = Number.isSafeInteger(moveId) && moveId > 0;
  // 提交前写入原请求编号会触发本页路由监听，不能打断同一次交接或查询。
  const observingSameMove = pendingFileStore.moveLoading && (hasMoveId
    ? pendingFileStore.moveTaskId === moveId
    : Boolean(moveRequest && pendingFileStore.moveRequestId === moveRequest));
  previewStore.filters = {};
  clearPreviewPage();
  renameOperationStore.stopExecutionPolling();
  if (!observingSameMove) pendingFileStore.stopMovePolling();
  operationDialogVisible.value = false;
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    previewStore.filters.media_source_id = routeSourceId;
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    previewStore.filters.scan_job_id = routeScanJobId;
    await refreshPreviews();
  } else {
    previewStore.filters = {};
    previewStore.previews = [];
    clearPreviewPage();
  }
  if (!viewActive || routeVersion !== routeContextVersion) return;
  const operationId = Number(route.query.operation_id);
  if (hasMoveId || moveRequest || pendingFileStore.moveUncertain
      || ["queued", "running"].includes(pendingFileStore.currentMoveTask?.status ?? "")) {
    activePreviewTab.value = "pending";
    if (!observingSameMove) void refreshPendingMoveTask(hasMoveId ? moveId : undefined, moveRequest);
    return;
  }
  if (Number.isSafeInteger(operationId) && operationId > 0) {
    operationDialogVisible.value = true;
    void (async () => {
      await renameOperationStore.refreshCurrentOperation(updateRenameProgress, operationId);
      if (!viewActive || routeVersion !== routeContextVersion || renameOperationStore.currentOperation?.id !== operationId) return;
      const planId = Number(route.query.rollback_plan_id);
      if (Number.isSafeInteger(planId) && planId > 0) await renameOperationStore.refreshRollbackPlan(planId);
    })();
  } else if (renameOperationStore.executionUncertain || ["queued", "running"].includes(renameOperationStore.currentOperation?.status ?? "")) {
    operationDialogVisible.value = true;
    void refreshRenameOperation();
  } else if (renameOperationStore.rollbackUncertain || ["queued", "running"].includes(renameOperationStore.currentRollbackPlan?.status ?? "")) {
    operationDialogVisible.value = true;
    void renameOperationStore.refreshRollbackPlan();
  }
}

let routeContextVersion = 0;
onMounted(applyRouteContext);
watch(() => [route.query.media_source_id, route.query.scan_job_id, route.query.operation_id,
  route.query.rollback_plan_id, route.query.pending_move_task_id, route.query.pending_move_request_id], () => {
  void applyRouteContext();
});

onBeforeUnmount(() => {
  viewActive = false;
  renameOperationStore.stopExecutionPolling();
  pendingFileStore.stopMovePolling();
  previewRequestVersion += 1;
  contextRequestVersion += 1;
  pendingRequestVersion += 1;
});
</script>

<template>
  <ListPageLayout class="rename-preview-page" :title="messages.renamePreviews.title" :description="messages.renamePreviews.description">
    <template #filters>
      <MediaSourceSelect
        v-model="previewStore.filters.media_source_id"
        class="rename-filter-select"
        :disabled="operationBusy"
        @change="loadScanJobsForSelectedSource"
      />
      <ScanJobSelect v-model="previewStore.filters.scan_job_id" :media-source-id="previewStore.filters.media_source_id"
        :disabled="operationBusy" class="rename-filter-select" :placeholder="messages.renamePreviews.scanJob" />
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
      <el-button class="query-action-button" :icon="Search" :loading="previewsLoading" :disabled="!previewStore.filters.scan_job_id || operationBusy" @click="refreshPreviews">
        {{ messages.common.query }}
      </el-button>
      <el-button :disabled="operationBusy" @click="resetRenamePreviews">{{ messages.common.reset }}</el-button>
    </template>

    <template #actions>
      <div v-if="activePreviewTab === 'previews'" class="rename-action-stack">
        <div class="rename-action-groups">
          <div class="rename-action-group">
            <el-button
              type="primary"
              :icon="MagicStick"
              :loading="operationProgressVisible && activeOperationKey === 'generate'"
              :disabled="operationBusy || !previewStore.filters.scan_job_id || !canExecuteRename"
              :title="canExecuteRename ? '' : messages.auth.permissionDenied"
              @click="generatePreviews"
            >
              {{ operationButtonLabel('generate', messages.renamePreviews.operationResultDialog.runningGenerate) }}
            </el-button>
            <el-select
              v-model="metadataMatchSource"
              class="metadata-source-select"
              :placeholder="messages.renamePreviews.metadataMatchSource"
              :disabled="operationBusy"
            >
              <el-option :label="messages.renamePreviews.metadataSourceParsedTitle" value="parsed_title" />
              <el-option :label="messages.renamePreviews.metadataSourceOriginalFileName" value="original_file_name" />
              <el-option :label="messages.renamePreviews.metadataSourceParentFolderTitle" value="parent_folder_title" />
            </el-select>
            <el-select
              v-model="multiSourceMatchMode"
              class="metadata-source-select"
              :placeholder="messages.renamePreviews.metadataMatchMode"
              :disabled="operationBusy"
            >
              <el-option :label="messages.renamePreviews.multiSourceModes.fallback" value="fallback" />
              <el-option :label="messages.renamePreviews.multiSourceModes.parallel" value="parallel" />
            </el-select>
            <div class="action-split-button">
              <el-button
                :icon="Connection"
                :disabled="selectedPreviewIds.length === 0 || operationBusy || !canSubmitMetadata"
                :title="metadataPermissionTitle"
                :loading="operationProgressVisible && activeOperationKey === 'tmdbMatch'"
                class="action-split-main"
                @click="runBatchCommand(matchSelectedTmdbOnly)"
              >
                {{ operationButtonLabel('tmdbMatch', messages.renamePreviews.operationResultDialog.runningMatch) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="operationBusy || !canSubmitMetadata"
                @command="handleTmdbDropdownCommand"
              >
                <el-button class="action-split-caret" :aria-label="messages.renamePreviews.tmdbMenu" :title="messages.renamePreviews.tmdbMenu">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_tmdb" :disabled="!canSubmitMetadata">{{ messages.renamePreviews.tmdbMatchAll }}</el-dropdown-item>
                    <el-dropdown-item command="selected_tmdb_ai" :disabled="!aiBatchReady || !canSubmitMetadata">{{ messages.renamePreviews.tmdbAiFallbackSelected }}</el-dropdown-item>
                    <el-dropdown-item command="all_tmdb_ai" :disabled="!aiBatchReady || !canSubmitMetadata">{{ messages.renamePreviews.tmdbAiFallbackAll }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div class="action-split-button">
              <el-button
                :icon="Connection"
                :disabled="selectedPreviewIds.length === 0 || operationBusy || !canSubmitMetadata"
                :title="metadataPermissionTitle"
                :loading="operationProgressVisible && activeOperationKey === 'multiSourceMatch'"
                class="action-split-main"
                @click="runBatchCommand(matchSelectedMultiSource)"
              >
                {{ operationButtonLabel('multiSourceMatch', messages.renamePreviews.operationResultDialog.runningMatch) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="operationBusy || !canSubmitMetadata"
                @command="handleMultiSourceDropdownCommand"
              >
                <el-button class="action-split-caret" :aria-label="messages.renamePreviews.multiSourceMenu" :title="messages.renamePreviews.multiSourceMenu">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_multi_source" :disabled="previewTotal === 0 || !canSubmitMetadata">
                      {{ messages.renamePreviews.multiSourceMatchAll }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div class="action-split-button">
              <el-button
                :icon="MagicStick"
                :disabled="selectedPreviewIds.length === 0 || operationBusy || !aiBatchReady || !canSubmitMetadata"
                :title="metadataPermissionTitle"
                :loading="operationProgressVisible && activeOperationKey === 'aiSelected'"
                class="action-split-main"
                @click="runBatchCommand(parseSelectedWithAiFromToolbar)"
              >
                {{ operationButtonLabel('aiSelected', messages.renamePreviews.operationResultDialog.runningAi, messages.renamePreviews.aiMatch) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="operationBusy || !canSubmitMetadata"
                @command="handleAiDropdownCommand"
              >
                <el-button class="action-split-caret" :aria-label="messages.renamePreviews.aiMenu" :title="messages.renamePreviews.aiMenu">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_ai" :disabled="!aiBatchReady || previewTotal === 0 || !canSubmitMetadata">
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
                :disabled="selectedPreviewIds.length === 0 || operationBusy || !canExecuteRename"
                :title="renamePermissionTitle"
                :loading="renameOperationStore.loading && !operationProgressVisible"
                class="action-split-main"
                @click="runBatchCommand(executeSelectedPreviews)"
              >
                {{ operationButtonLabel('rename', messages.renamePreviews.operationResultDialog.runningRename) }}
              </el-button>
              <el-dropdown
                trigger="click"
                :disabled="previewTotal === 0 || operationBusy || !canExecuteRename"
                @command="handleRenameDropdownCommand"
              >
                <el-button class="action-split-caret" :aria-label="messages.renamePreviews.renameMenu" :title="messages.renamePreviews.renameMenu">
                  <el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="all_rename" :disabled="!canExecuteRename">{{ messages.renamePreviews.executeAll }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <el-button
              :icon="CloseBold"
              :disabled="selectedPreviewIds.length === 0 || operationBusy || !canExecuteRename"
              :title="canExecuteRename ? '' : messages.auth.permissionDenied"
              :loading="operationProgressVisible && activeOperationKey === 'excludeSelected'"
              @click="runBatchCommand(excludeSelectedPreviews)"
            >
              {{ operationButtonLabel('excludeSelected', messages.renamePreviews.operationResultDialog.runningExclude) }}
            </el-button>
          </div>
        </div>
        <div v-if="!aiBatchReady" class="rename-toolbar-status-row">
          <el-tooltip :trigger-keys="[]" :content="aiBatchBlockMessage" placement="top">
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
          :disabled="selectedPendingFileIds.length === 0 || !canExecuteRename || operationBusy"
          :title="canExecuteRename ? '' : messages.auth.permissionDenied"
          @click="openPendingMoveDialog"
        >
          {{ messages.renamePreviews.pendingFiles.move }}
        </el-button>
        <el-button
          :icon="Delete"
          :disabled="!pendingStats.pending || pendingFilesLoading || !canManagePending"
          :title="canManagePending ? '' : messages.auth.permissionDenied"
          @click="clearPendingFiles"
        >
          {{ messages.renamePreviews.pendingFiles.clear }}
        </el-button>
      </div>
    </template>

    <el-alert v-if="previewStore.errorMessage" type="error" :title="previewStore.errorMessage" show-icon />

    <template #table>
      <FullscreenTablePanel title="" variant="tab-header">
      <el-tabs v-model="activePreviewTab" class="rename-preview-tabs workbench-table-tabs">
        <el-tab-pane :label="messages.renamePreviews.tabs.previews" name="previews">
          <el-table
          ref="previewTable"
          v-loading="previewsLoading || batchScopeLoading"
          :data="pagedPreviews"
          class="data-table rename-previews-table row-select-table"
          height="100%"
          table-layout="fixed"
          :default-sort="defaultSort"
          @row-click="togglePreviewRow"
          @selection-change="handleSelectionChange"
          @sort-change="handleSortChange"
          >
          <template #empty>
            <div v-if="shouldShowGeneratePreviewEmpty" class="rename-empty-preview-action">
              <strong>{{ messages.renamePreviews.emptyPreviewAction.title }}</strong>
              <span>
                {{
                  formatMessage(messages.renamePreviews.emptyPreviewAction.description, {
                    count: mediaFileCount,
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
          <div class="preview-original-cell">
            <div class="preview-original-text">
              <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
            </div>
            <el-tooltip :trigger-keys="[]" :content="messages.common.detail" placement="top">
              <el-button
                class="table-action-button action-view preview-detail-trigger"
                :icon="View"
                :aria-label="`${messages.common.detail}: ${row.file_name}`"
                text
                circle
                @click.stop="openDetailDialog(row)"
              />
            </el-tooltip>
          </div>
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
          <div class="target-name-cell">
            <TextCell :value="row.current_target_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
            <el-tooltip :trigger-keys="[]"
              v-if="shouldShowNamingTemplateStatus(row)"
              :content="namingTemplateStatusReason(row)"
              placement="top"
            >
              <el-tag
                class="naming-template-status-tag"
                :type="namingTemplateStatusTagType(row.naming_template_status)"
                effect="light"
              >
                {{ namingTemplateStatusText(row) }}
              </el-tag>
            </el-tooltip>
          </div>
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.parsedTitle" min-width="132" align="left" header-align="left">
          <template #default="{ row }">
          <el-tooltip :trigger-keys="[]"
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
          <el-tooltip :trigger-keys="[]" :content="metadataReason(row)" placement="top" :disabled="!metadataReason(row) || metadataReason(row) === '-'">
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
            <el-tooltip :trigger-keys="[]" :content="metadataReason(row)" placement="top" :disabled="!metadataReason(row) || metadataReason(row) === '-'">
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
          <el-table-column :label="messages.common.actions" width="190" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
          <div class="table-actions">
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.tmdbMatch" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.tmdbMatch"
          class="table-action-button action-sync"
          :icon="Connection"
          text
          circle
          :disabled="!canSubmitMetadata"
          @click.stop="matchMetadata(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.multiSourceMatch" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.multiSourceMatch"
          class="table-action-button action-sync"
          :icon="Connection"
          text
          circle
          :disabled="!canSubmitMetadata"
          @click.stop="matchMultiSource(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.aiParse" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.aiParse"
          class="table-action-button action-ai"
          :icon="MagicStick"
          text
          circle
          :loading="aiParsingPreviewId === row.id"
          :disabled="!canSubmitMetadata || (previewStore.loading && aiParsingPreviewId !== row.id)"
          @click.stop="parseSingleWithAi(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" v-if="row.metadata_candidate_count > 0" :content="messages.renamePreviews.actions.metadataResult" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.metadataResult"
          class="table-action-button action-sync"
          :icon="View"
          text
          circle
          :disabled="!canSubmitMetadata"
          @click.stop="openMetadataBackfill(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.edit" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.edit" class="table-action-button action-edit" :icon="Edit" :disabled="!canExecuteRename" :title="canExecuteRename ? '' : messages.auth.permissionDenied" text circle @click.stop="openEditDialog(row)" />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.exclude" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.exclude"
          class="table-action-button action-delete"
          :icon="CloseBold"
          text
          circle
          :disabled="!canExecuteRename"
          :title="canExecuteRename ? '' : messages.auth.permissionDenied"
          @click.stop="excludeSinglePreview(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.actions.execute" placement="top">
          <el-button :aria-label="messages.renamePreviews.actions.execute"
          class="table-action-button action-run"
          :icon="Select"
          text
          circle
          :disabled="!canExecuteRename"
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
                    <el-icon class="rename-stats-icon" aria-hidden="true"><TrendCharts /></el-icon>
                    <span>{{ messages.common.statistics }}</span>
                  </el-button>
                </template>
                <div class="rename-stats-popover-list">
                  <ListStatItem
                    v-for="item in previewStatItems"
                    :key="item.key"
                    :label="item.label"
                    :value="item.value"
                    :tone="item.tone"
                  />
                </div>
              </el-popover>
            </div>
            <TablePagination pagination-key="rename-previews" :total="previewTotal" server
              @page-change="loadPreviewPage(false)" @page-size-change="loadPreviewPage()" />
          </div>
        </el-tab-pane>

        <el-tab-pane name="pending" class="pending-files-pane">
          <template #label>
            <el-tooltip :trigger-keys="[]" :content="messages.renamePreviews.pendingFiles.description" placement="top">
              <span class="tab-label-with-tooltip">{{ messages.renamePreviews.tabs.pendingFiles }}</span>
            </el-tooltip>
          </template>
          <div v-if="pendingFileStore.currentMoveTask || pendingFileStore.moveUncertain" class="pending-move-status" role="status">
            <strong v-if="pendingFileStore.currentMoveTask">{{ formatMessage(messages.renamePreviews.pendingFiles.taskTitle, { id: pendingFileStore.currentMoveTask.id }) }}</strong>
            <el-tag v-if="pendingFileStore.currentMoveTask" :type="pendingFileStore.currentMoveTask.status === 'completed' ? 'success' : ['failed','interrupted','partial_failed'].includes(pendingFileStore.currentMoveTask.status) ? 'warning' : 'info'">
              {{ messages.tasks.statuses[pendingFileStore.currentMoveTask.status as keyof typeof messages.tasks.statuses] || pendingFileStore.currentMoveTask.status }}
            </el-tag>
            <el-progress v-if="pendingMoveProgress !== null" class="pending-move-progress" :percentage="pendingMoveProgress"
              :aria-label="messages.backgroundJobs.progress" :show-text="false" :stroke-width="5" />
            <span>{{ pendingMoveStatusText }}</span>
            <el-button class="table-action-button action-edit" :icon="Refresh" :loading="pendingFileStore.moveLoading" :disabled="pendingFileStore.moveLoading"
              :aria-label="messages.common.refresh" :title="messages.common.refresh" @click="refreshPendingMoveTask()" />
            <el-button v-if="pendingFileStore.currentMoveTask" class="table-action-button action-view" :icon="View"
              :aria-label="messages.common.detail" :title="messages.common.detail" @click="pendingMoveResultVisible = true" />
            <el-button v-if="canReadLogs && pendingFileStore.currentMoveTask" class="table-action-button action-view" :icon="Document"
              :aria-label="messages.operationLogs.pendingMoveTitle" :title="messages.operationLogs.pendingMoveTitle" @click="openPendingMoveLog" />
          </div>
          <el-table
          ref="pendingTable"
          :data="pendingFileStore.pendingFiles"
          v-loading="pendingFilesLoading"
          class="data-table row-select-table"
          height="100%"
          table-layout="auto"
          @row-click="togglePendingRow"
          @selection-change="handlePendingSelectionChange"
          >
          <el-table-column type="selection" width="44" align="center" fixed="left" :selectable="(row: PendingFile) => row.status === 'pending'" />
          <el-table-column :label="messages.common.status" width="116" align="center" header-align="center">
          <template #default="{ row }">
          <el-tooltip :trigger-keys="[]" :disabled="row.status === 'pending'" :content="messages.renamePreviews.pendingFiles.unresolved" placement="top">
          <el-tag :type="row.status === 'recovery_required' ? 'danger' : row.status === 'moving' ? 'warning' : 'info'" effect="light">
          {{ messages.renamePreviews.pendingFiles.statuses[row.status as keyof typeof messages.renamePreviews.pendingFiles.statuses] || row.status }}
          </el-tag>
          </el-tooltip>
          </template>
          </el-table-column>
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
          <el-tooltip :trigger-keys="[]" :content="row.status !== 'pending' ? messages.renamePreviews.pendingFiles.unresolved : messages.renamePreviews.actions.aiParse" placement="top">
          <el-button :aria-label="row.status !== 'pending' ? messages.renamePreviews.pendingFiles.unresolved : messages.renamePreviews.actions.aiParse"
          class="table-action-button action-ai"
          :icon="MagicStick"
          text
          circle
          :loading="aiParsingPreviewId === row.id"
          :disabled="row.status !== 'pending' || !canSubmitMetadata || (pendingFileStore.loading && aiParsingPreviewId !== row.id)"
          :title="canSubmitMetadata ? '' : messages.auth.permissionDenied"
          @click="parsePendingWithAi(row)"
          />
          </el-tooltip>
          <el-tooltip :trigger-keys="[]" :content="row.status !== 'pending' ? messages.renamePreviews.pendingFiles.unresolved : messages.renamePreviews.pendingFiles.remove" placement="top">
          <el-button :aria-label="row.status !== 'pending' ? messages.renamePreviews.pendingFiles.unresolved : messages.renamePreviews.pendingFiles.remove"
          class="table-action-button action-delete"
          :icon="Delete"
          text
          circle
          :disabled="row.status !== 'pending' || !canManagePending"
          :title="canManagePending ? '' : messages.auth.permissionDenied"
          @click="removePendingFile(row)"
          />
          </el-tooltip>
          </template>
          </el-table-column>
          </el-table>
          <div class="table-footer">
            <TablePagination pagination-key="pending-files" :total="pendingTotal" server
              @page-change="loadPendingPage(false)" @page-size-change="loadPendingPage()" />
          </div>
        </el-tab-pane>
      </el-tabs>
      </FullscreenTablePanel>
    </template>

    <el-dialog
      v-model="operationResultVisible"
      :title="operationResultTitle"
      width="min(680px, calc(100vw - 24px))"
      align-center
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
        <div class="rename-operation-actions">
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
          <el-button v-if="multiSourceMatchRows.length > 0" @click="openMultiSourceMatchResults">
            {{ messages.renamePreviews.multiSourceResults.viewResults }}
          </el-button>
          <el-button type="primary" @click="operationResultVisible = false">{{ messages.common.close }}</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="pendingMoveDialogVisible"
      :title="messages.renamePreviews.pendingFiles.moveDialog.title"
      width="min(560px, calc(100vw - 24px))"
      align-center
    >
      <el-form label-position="top">
        <el-form-item :label="messages.renamePreviews.pendingFiles.targetDirectory">
          <el-input
            v-model="pendingMoveTargetDirectory"
            :placeholder="messages.renamePreviews.pendingFiles.targetDirectoryPlaceholder"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="rename-operation-actions">
          <el-button @click="pendingMoveDialogVisible = false">{{ messages.common.cancel }}</el-button>
          <el-button type="primary" :loading="pendingFileStore.moveLoading" :disabled="!canExecuteRename || operationBusy" @click="moveSelectedPendingFiles">
            {{ messages.renamePreviews.pendingFiles.moveDialog.confirm }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="pendingMoveResultVisible"
      :title="messages.renamePreviews.pendingFiles.resultTitle"
      width="min(800px, calc(100vw - 24px))"
      align-center
    >
      <p v-if="pendingFileStore.currentMoveTask?.error_message" class="pending-move-error" tabindex="0">
        {{ pendingFileStore.currentMoveTask.error_message }}
      </p>
      <FullscreenTablePanel variant="embedded"><el-table
        :data="pendingFileStore.currentMoveTask?.items ?? []"
        class="data-table rename-dialog-table"
        table-layout="fixed"
        max-height="clamp(140px, calc(100dvh - 260px), 460px)"
        scrollbar-always-on
      >
        <el-table-column prop="pending_file_id" label="ID" width="80" align="center" />
        <el-table-column :label="messages.common.status" width="128" align="center">
          <template #default="{ row }">{{ messages.renamePreviews.pendingFiles.itemStatuses[row.status as keyof typeof messages.renamePreviews.pendingFiles.itemStatuses] || row.status }}</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.pendingFiles.columns.path" min-width="260">
          <template #default="{ row }"><TextCell :value="row.source_path" :max-length="tableDisplayConfig.pathMaxLength" /></template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.pendingFiles.targetPath" min-width="260">
          <template #default="{ row }"><TextCell :value="row.target_path" :max-length="tableDisplayConfig.pathMaxLength" /></template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.pendingFiles.columns.reason" min-width="180">
          <template #default="{ row }"><TextCell :value="row.message" :max-length="40" /></template>
        </el-table-column>
      </el-table></FullscreenTablePanel>
      <template #footer><el-button @click="pendingMoveResultVisible = false">{{ messages.common.close }}</el-button></template>
    </el-dialog>

    <RenamePreviewDetailsDialog v-model:visible="detailDialogVisible" :fields="detailRows" />

    <el-dialog
      v-model="editDialogVisible"
      :title="messages.renamePreviews.editDialog.title"
      width="min(520px, calc(100% - 24px))"
      align-center
    >
      <el-input v-model="editingTargetName" :aria-label="messages.renamePreviews.editDialog.placeholder" :placeholder="messages.renamePreviews.editDialog.placeholder" />
      <template #footer>
        <el-button @click="editDialogVisible = false">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" @click="saveEdit">{{ messages.common.save }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="aiParseDialogVisible"
      :title="messages.renamePreviews.aiParse.title"
      width="min(920px, calc(100% - 24px))"
      align-center
    >
      <div v-if="aiParseResult" class="ai-parse-result-panel">
        <div class="ai-parse-summary">
          <el-tag :type="aiParseStatusTagType(aiParseResult.status)" effect="light">
            {{ aiParseStatusLabel(aiParseResult.status) }}
          </el-tag>
          <span>{{ aiParseResult.message }}</span>
          <span v-if="aiParseResult.response_ms !== undefined && aiParseResult.response_ms !== null">
            {{ messages.renamePreviews.aiParse.responseTime }}{{ labelSeparator }}{{ aiParseResult.response_ms }}
            {{ messages.renamePreviews.aiParse.responseTimeUnit }}
          </span>
        </div>
        <pre
          v-if="aiParseResult.usage && Object.keys(aiParseResult.usage).length > 0"
          class="metadata-raw-json ai-parse-usage"
        >{{ messages.renamePreviews.aiParse.usage }}{{ labelSeparator }}{{ JSON.stringify(aiParseResult.usage, null, 2) }}</pre>
        <el-empty
          v-if="aiParseResult.candidates.length === 0"
          :description="messages.renamePreviews.aiParse.empty"
        />
        <FullscreenTablePanel variant="embedded" v-else><el-table :data="aiParseResult.candidates" class="data-table rename-dialog-table" table-layout="fixed">
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
              <el-button class="candidate-apply-button" type="primary" :disabled="!canExecuteRename" :title="canExecuteRename ? '' : messages.auth.permissionDenied" @click="applyAiCandidate(row)">
                {{ messages.renamePreviews.aiParse.apply }}
              </el-button>
            </template>
          </el-table-column>
        </el-table></FullscreenTablePanel>
      </div>
      <template #footer>
        <el-button @click="aiParseDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="metadataDialogVisible"
      :title="messages.renamePreviews.metadataDialog.title"
      width="min(1120px, calc(100% - 24px))"
      align-center
    >
      <div class="metadata-field-selector">
        <span>{{ messages.renamePreviews.metadataDialog.fieldSelection }}</span>
        <el-checkbox-group v-model="selectedMetadataFields">
          <el-checkbox v-for="field in metadataFieldOptions" :key="field.value" :label="field.value">
            {{ field.label }}
          </el-checkbox>
        </el-checkbox-group>
      </div>
      <el-empty v-if="metadataCandidates.length === 0" :description="messages.renamePreviews.metadataDialog.empty" />
      <FullscreenTablePanel variant="embedded" v-else><el-table :data="pagedMetadataCandidates" class="data-table rename-dialog-table" table-layout="fixed">
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <div class="metadata-candidate-expanded">
              <div v-if="candidateFieldSources(row).length" class="metadata-field-source-list">
                <span class="metadata-field-source-title">{{ messages.renamePreviews.metadataDialog.fieldSources }}</span>
                <el-tag
                  v-for="fieldSource in candidateFieldSources(row)"
                  :key="fieldSource.field"
                  size="small"
                  effect="light"
                >
                  {{ fieldSource.label }}{{ labelSeparator }}{{ fieldSource.source }}
                </el-tag>
              </div>
              <pre class="metadata-raw-json">{{ JSON.stringify(row.candidate.raw_data ?? {}, null, 2) }}</pre>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.metadataDialog.source" width="118" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag effect="light">{{ candidateSourceLabel(row) }}</el-tag>
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
            <el-button class="candidate-apply-button" type="primary" :disabled="!canExecuteRename" :title="canExecuteRename ? '' : messages.auth.permissionDenied" @click="applyMetadataCandidate(row)">
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
      </FullscreenTablePanel>
      <template #footer>
        <el-button @click="metadataDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="multiSourceResultDialogVisible"
      :title="messages.renamePreviews.multiSourceResults.title"
      width="min(980px, calc(100vw - 2rem))"
      align-center
    >
      <el-alert
        :title="messages.renamePreviews.multiSourceResults.notice"
        type="info"
        show-icon
        :closable="false"
      />
      <FullscreenTablePanel variant="embedded"><el-table :data="multiSourceMatchRows" class="data-table multi-source-result-table" table-layout="auto">
        <el-table-column :label="messages.renamePreviews.columns.originalName" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <TextCell :value="row.fileName" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.metadata" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="metadataStatusTagType(row.matchStatus || 'failed')" effect="light">
              {{ metadataStatusLabel(row.matchStatus || 'failed') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.multiSourceResults.candidateCount" width="96" align="center">
          <template #default="{ row }">{{ row.candidateCount }}</template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.multiSourceResults.providers" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">{{ multiSourceProviderSummary(row) || '-' }}</template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="132" fixed="right" align="center">
          <template #default="{ row }">
            <el-button
              type="primary"
              :disabled="!row.canViewCandidates"
              @click="openMetadataBackfill(row.preview)"
            >
              {{ messages.renamePreviews.multiSourceResults.viewCandidates }}
            </el-button>
          </template>
        </el-table-column>
      </el-table></FullscreenTablePanel>
      <template #footer>
        <el-button @click="multiSourceResultDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="emptyTargetDialogVisible" :title="messages.renamePreviews.emptyTargetDialog.title" width="760px">
      <el-alert :title="messages.renamePreviews.emptyTargetDialog.message" type="warning" show-icon />
      <FullscreenTablePanel variant="embedded"><el-table :data="findEmptyTargetNamePreviews(pendingRenamePreviews)" class="data-table" table-layout="auto">
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
      </el-table></FullscreenTablePanel>
      <template #footer>
        <el-button @click="emptyTargetDialogVisible = false">{{ messages.renamePreviews.emptyTargetDialog.back }}</el-button>
        <el-button type="warning" :loading="renameOperationStore.loading" @click="continueWithoutEmptyTargets">
          {{ messages.renamePreviews.emptyTargetDialog.removeAndContinue }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="operationDialogVisible"
      :title="operationDialogTitle"
      width="min(860px, calc(100% - 24px))"
      align-center
    >
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
        <FullscreenTablePanel variant="embedded" v-if="renameOperationStore.currentOperation"><el-table
          :data="renameOperationStore.currentOperation.items"
          class="data-table rename-dialog-table"
          table-layout="fixed"
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
        </el-table></FullscreenTablePanel>
        <el-divider v-if="operationHasExecuted && renameOperationStore.currentOperation?.renamed_count" />
        <div v-if="operationHasExecuted && renameOperationStore.currentOperation?.renamed_count" class="rename-operation-dialog">
          <h3 class="settings-config-card-title rename-rollback-title">{{ messages.renamePreviews.operationDialog.rollbackTitle }}</h3>
          <el-alert
            v-if="renameOperationStore.rollbackErrorMessage || rollbackPlan?.error_message"
            :title="renameOperationStore.rollbackErrorMessage || rollbackPlan?.error_message || ''"
            type="error"
            show-icon
          />
          <div v-if="rollbackPlan" class="preview-stats operation-stats">
            <div class="stat-item">
              <span>{{ messages.renamePreviews.operationDialog.rolledBackCount }}</span>
              <strong>{{ rollbackPlan.rolled_back_count ?? 0 }}</strong>
            </div>
            <div class="stat-item">
              <span>{{ messages.renamePreviews.operationDialog.rollbackFailedCount }}</span>
              <strong>{{ rollbackPlan.failed_count ?? 0 }}</strong>
            </div>
            <div>
              <span>{{ messages.common.status }}</span>
              <strong>{{ rollbackStatusLabel(rollbackPlan.status) }}</strong>
            </div>
            <div>
              <span>{{ messages.common.total }}</span>
              <strong>{{ rollbackPlan.item_count }}</strong>
            </div>
            <div class="stat-generated">
              <span>{{ messages.status.ready }}</span>
              <strong>{{ rollbackPlan.executable_count }}</strong>
            </div>
            <div class="stat-review">
              <span>{{ messages.status.conflict }}</span>
              <strong>{{ rollbackPlan.conflict_count }}</strong>
            </div>
          </div>
          <el-empty
            v-else
            :description="messages.renamePreviews.operationDialog.rollbackEmpty"
          />
          <FullscreenTablePanel variant="embedded" v-if="rollbackPlan"><el-table
            :data="rollbackPlan.items"
            class="data-table rename-dialog-table"
            table-layout="fixed"
          >
            <el-table-column :label="messages.common.status" width="110" align="center" header-align="center">
              <template #default="{ row }">
                <el-tag :type="rollbackStatusTagType(row.status)" effect="light">
                  {{ rollbackStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="messages.renamePreviews.columns.sourcePath" min-width="220" align="left" header-align="left">
              <template #default="{ row }">
                <TextCell :value="row.current_path" :max-length="tableDisplayConfig.pathMaxLength" />
              </template>
            </el-table-column>
            <el-table-column :label="messages.renamePreviews.columns.targetPath" min-width="220" align="left" header-align="left">
              <template #default="{ row }">
                <TextCell :value="row.rollback_path" :max-length="tableDisplayConfig.pathMaxLength" />
              </template>
            </el-table-column>
            <el-table-column :label="messages.renamePreviews.columns.reason" min-width="160" align="left" header-align="left">
              <template #default="{ row }">
                <TextCell :value="row.message || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
              </template>
            </el-table-column>
          </el-table></FullscreenTablePanel>
          <div class="rename-operation-actions">
            <el-button
              v-if="canCreateRollbackPlan"
              :loading="renameOperationStore.rollbackLoading"
              :disabled="!canExecuteRollback"
              :title="rollbackPermissionTitle"
              @click="createRollbackPlan"
            >
              {{ messages.renamePreviews.operationDialog.rollbackCreate }}
            </el-button>
            <el-button
              v-if="rollbackPlan"
              :loading="renameOperationStore.rollbackLoading"
              :disabled="!canDryRunRollbackPlan"
              :title="rollbackPermissionTitle"
              @click="dryRunRollbackPlan"
            >
              {{ messages.renamePreviews.operationDialog.rollbackDryRun }}
            </el-button>
            <el-button
              v-if="rollbackPlan"
              type="danger"
              :loading="renameOperationStore.rollbackLoading"
              :disabled="!canExecuteRollbackPlan"
              :title="rollbackPermissionTitle"
              @click="executeRollbackPlan"
            >
              {{ messages.renamePreviews.operationDialog.rollbackExecute }}
            </el-button>
            <el-button v-if="rollbackPlan" :disabled="!canReadLogs" :title="canReadLogs ? '' : messages.auth.permissionDenied" @click="openRollbackOperationLog">
              {{ messages.renamePreviews.operationDialog.rollbackLog }}
            </el-button>
            <el-button v-if="rollbackPlan" :icon="Refresh" :disabled="renameOperationStore.rollbackLoading"
              @click="renameOperationStore.refreshRollbackPlan()">
              {{ messages.renamePreviews.operationDialog.rollbackRefresh }}
            </el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="rename-operation-actions">
          <el-button v-if="renameOperationStore.currentOperation" :icon="Refresh" :loading="renameOperationStore.loading"
            :disabled="renameOperationStore.loading" @click="refreshRenameOperation">
            {{ messages.common.refresh }}
          </el-button>
          <el-button
            v-if="renameOperationStore.currentOperation"
            @click="openRenameOperationLog"
            :disabled="!canReadLogs"
            :title="canReadLogs ? '' : messages.auth.permissionDenied"
          >
            {{ messages.scanJobs.viewLogs }}
          </el-button>
          <el-button @click="operationDialogVisible = false">{{ messages.common.close }}</el-button>
          <el-button
            v-if="!operationHasExecuted"
            type="danger"
            :disabled="!renameOperationStore.canExecute || !canExecuteRename"
            :title="renamePermissionTitle"
            :loading="renameOperationStore.loading"
            @click="executeRenameOperation"
          >
            {{ messages.renamePreviews.operationDialog.confirm }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <OperationLogDrawer
      v-model:visible="operationLogVisible"
      :task-type="selectedOperationLogTaskType"
      :task-id="selectedOperationLogTaskId"
      :title="selectedOperationLogTaskType === 'pending_move' ? messages.operationLogs.pendingMoveTitle : selectedOperationLogTaskType === 'rollback_plan' ? messages.operationLogs.rollbackTitle : messages.operationLogs.renameTitle"
    />
  </ListPageLayout>
</template>

<style scoped>
.preview-original-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.preview-original-text {
  flex: 1 1 auto;
  min-width: 0;
}

.preview-detail-trigger {
  flex: 0 0 auto;
}

@media (max-height: 560px) {
  .rename-preview-page > :deep(.list-table-card) {
    flex: 0 0 18rem;
  }
}

@media (min-width: 901px) and (max-height: 560px) {
  .rename-preview-page {
    overflow-y: auto;
  }
}

:deep(.pending-files-pane:has(.pending-move-status)) {
  grid-template-rows: auto minmax(0, 1fr) auto;
}
.pending-move-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.pending-move-status > span {
  overflow-wrap: anywhere;
  flex: 1 1 240px;
  min-width: 0;
}
.pending-move-progress {
  flex: 0 1 9rem;
  min-width: 3rem;
}
.pending-move-error {
  max-height: 3rem;
  margin: 0 0 0.5rem;
  overflow-y: auto;
  color: var(--el-color-danger);
  overflow-wrap: anywhere;
}
.metadata-candidate-expanded {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rename-dialog-table {
  width: 100%;
  min-width: 0;
}

.rename-dialog-table :deep(.el-table__header .cell) {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.3;
}

.candidate-apply-button {
  width: 100%;
  height: auto;
  padding: 6px 8px;
  white-space: normal;
}

.candidate-apply-button :deep(span) {
  min-width: 0;
  overflow-wrap: anywhere;
  line-height: 1.3;
}

.ai-parse-result-panel {
  grid-template-columns: minmax(0, 1fr);
}

.ai-parse-summary > span:not(.el-tag),
.ai-parse-result-panel .metadata-raw-json,
.metadata-candidate-expanded .metadata-raw-json {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.metadata-field-source-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.metadata-field-source-list :deep(.el-tag) {
  max-width: 100%;
  height: auto;
  min-height: var(--el-tag-size);
  white-space: normal;
  overflow-wrap: anywhere;
}

.metadata-field-source-title {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.rename-operation-dialog {
  min-width: 0;
  grid-template-columns: minmax(0, 1fr);
}

.rename-operation-dialog .operation-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 7rem), 1fr));
  align-items: stretch;
  gap: 8px;
}

.rename-operation-dialog .operation-stats > div {
  display: grid;
  align-content: start;
  gap: 4px;
  min-width: 0;
  padding: 6px 8px;
  border-left: 0;
}

.rename-operation-dialog .operation-stats > div > span,
.rename-operation-dialog .operation-stats > div > strong {
  white-space: normal;
  overflow-wrap: anywhere;
}

.rename-rollback-title,
.rename-operation-dialog > :deep(.el-divider) {
  margin: 0;
}

.rename-operation-dialog :deep(.el-alert__content) {
  min-width: 0;
  overflow-wrap: anywhere;
}

.rename-operation-dialog .rename-dialog-table :deep(.el-tag) {
  max-width: 100%;
  min-height: var(--el-tag-size);
  height: auto;
  white-space: normal;
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.rename-operation-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.rename-operation-actions :deep(.el-button) {
  max-width: 100%;
  height: auto;
  margin-left: 0;
  padding-block: 6px;
  white-space: normal;
}

.rename-operation-actions :deep(.el-button > span) {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
