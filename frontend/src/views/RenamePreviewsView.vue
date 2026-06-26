<script setup lang="ts">
import { Connection, Delete, Edit, MagicStick, Refresh, Search, Select } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import type { MetadataMatchResult, RenamePreview } from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
import ListStatItem from "../components/ListStatItem.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { usePendingFileStore } from "../stores/pendingFiles";
import { usePreviewStore } from "../stores/preview";
import { useRenameOperationStore } from "../stores/renameOperation";
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
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();

const editDialogVisible = ref(false);
const operationDialogVisible = ref(false);
const emptyTargetDialogVisible = ref(false);
const detailDialogVisible = ref(false);
const metadataDialogVisible = ref(false);
const pendingMoveDialogVisible = ref(false);
const editingPreviewId = ref<number | null>(null);
const selectedDetailRow = ref<RenamePreview | null>(null);
const metadataPreviewId = ref<number | null>(null);
const editingTargetName = ref("");
const selectedPreviewIds = ref<number[]>([]);
const selectedPreviewRows = ref<RenamePreview[]>([]);
const selectedPendingFileIds = ref<number[]>([]);
const pendingRenamePreviews = ref<RenamePreview[]>([]);
const metadataCandidates = ref<MetadataMatchResult[]>([]);
const pendingMoveTargetDirectory = ref("");
const activePreviewTab = ref("previews");
const defaultSort = { prop: "updated_at", order: "descending" as const };
const previewTableTopScroll = ref<HTMLElement | null>(null);
const previewTable = ref();
const previewTopScrollWidth = ref(0);
const previewTopScrollVisible = ref(false);
let syncingPreviewScroll = false;

const pagedPreviews = computed(() =>
  paginationStore.paginate(
    "rename-previews",
    tableSortStore.applySort("rename-previews", previewStore.previews),
  ),
);

function getPreviewScrollBody() {
  return previewTable.value?.$el?.querySelector(".el-scrollbar__wrap") as HTMLElement | null;
}

async function syncPreviewTopScrollWidth() {
  await nextTick();
  const scrollBody = previewTable.value?.$el?.querySelector(".el-scrollbar__wrap") as HTMLElement | null;
  const scrollContent = previewTable.value?.$el?.querySelector(".el-table__body") as HTMLElement | null;
  const scrollWidth = Math.max(scrollContent?.scrollWidth ?? 0, scrollBody?.scrollWidth ?? 0);
  previewTopScrollWidth.value = previewStore.previews.length > 0 ? scrollWidth : 0;
  previewTopScrollVisible.value = previewStore.previews.length > 0 && scrollBody ? scrollWidth > scrollBody.clientWidth : false;
  if (previewTableTopScroll.value) {
    previewTableTopScroll.value.scrollLeft = 0;
  }
  if (scrollBody && !previewTopScrollVisible.value) {
    scrollBody.scrollLeft = 0;
  }
}

function handleTopPreviewScroll(event: Event) {
  if (syncingPreviewScroll) {
    return;
  }
  const scrollBody = getPreviewScrollBody();
  if (!scrollBody) {
    return;
  }
  syncingPreviewScroll = true;
  const topScroll = event.currentTarget as HTMLElement;
  const topMax = Math.max(topScroll.scrollWidth - topScroll.clientWidth, 0);
  const bodyMax = Math.max(scrollBody.scrollWidth - scrollBody.clientWidth, 0);
  scrollBody.scrollLeft = topMax > 0 ? (topScroll.scrollLeft / topMax) * bodyMax : 0;
  window.requestAnimationFrame(() => {
    syncingPreviewScroll = false;
  });
}

function bindPreviewBodyScroll() {
  const scrollBody = getPreviewScrollBody();
  if (!scrollBody) {
    return;
  }
  scrollBody.addEventListener("scroll", () => {
    if (syncingPreviewScroll || !previewTableTopScroll.value) {
      return;
    }
    syncingPreviewScroll = true;
    const topMax = Math.max(previewTableTopScroll.value.scrollWidth - previewTableTopScroll.value.clientWidth, 0);
    const bodyMax = Math.max(scrollBody.scrollWidth - scrollBody.clientWidth, 0);
    previewTableTopScroll.value.scrollLeft = bodyMax > 0 ? (scrollBody.scrollLeft / bodyMax) * topMax : 0;
    window.requestAnimationFrame(() => {
      syncingPreviewScroll = false;
    });
  });
}

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

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    generated: messages.status.generated,
    edited: messages.status.edited,
    needs_review: messages.status.needsReview,
    renamed: messages.status.renamed,
    tmdb_matched: messages.renamePreviews.metadataStatuses.high_confidence,
    tmdb_selected: messages.renamePreviews.metadataStatuses.manual_selected,
  };
  return labels[value] ?? value;
}

function statusTagType(value: string) {
  if (value === "generated" || value === "renamed") {
    return "success";
  }
  if (value === "edited") {
    return "primary";
  }
  if (value === "needs_review") {
    return "warning";
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
    { label: messages.renamePreviews.columns.metadataSource, value: row.metadata_source ?? "-" },
    { label: messages.renamePreviews.columns.metadataScore, value: `${row.metadata_match_score ?? 0}%` },
    { label: messages.renamePreviews.columns.metadata, value: metadataStatusLabel(row.metadata_match_status) },
    { label: messages.renamePreviews.type, value: mediaTypeLabel(row.media_type) },
    { label: messages.renamePreviews.columns.year, value: row.parsed_year ?? "-" },
    { label: messages.renamePreviews.columns.seasonEpisode, value: seasonEpisode(row) },
    { label: messages.renamePreviews.columns.updatedAt, value: formatDateTime(row.updated_at) },
    { label: messages.renamePreviews.columns.sourcePath, value: row.file_path },
  ];
});

async function refreshPreviews() {
  if (!previewStore.filters.scan_job_id) {
    previewStore.previews = [];
    pendingFileStore.pendingFiles = [];
    await syncPreviewTopScrollWidth();
    return;
  }
  await previewStore.loadPreviews(previewStore.filters);
  await pendingFileStore.loadPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  await syncPreviewTopScrollWidth();
}

async function generatePreviews() {
  if (!previewStore.filters.scan_job_id) {
    return;
  }
  await previewStore.generatePreviews({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
  await syncPreviewTopScrollWidth();
}

async function loadScanJobsForSelectedSource() {
  previewStore.filters.scan_job_id = undefined;
  previewStore.previews = [];
  pendingFileStore.pendingFiles = [];
  await syncPreviewTopScrollWidth();
  if (!previewStore.filters.media_source_id) {
    mediaStore.scanJobs = [];
    return;
  }
  await mediaStore.loadScanJobs({ media_source_id: previewStore.filters.media_source_id });
}

async function resetRenamePreviews() {
  previewStore.filters = {};
  previewStore.previews = [];
  pendingFileStore.pendingFiles = [];
  selectedPreviewIds.value = [];
  selectedPreviewRows.value = [];
  await syncPreviewTopScrollWidth();
  await mediaStore.loadMediaSources();
}

function handleSelectionChange(rows: RenamePreview[]) {
  selectedPreviewRows.value = rows;
  selectedPreviewIds.value = rows.map((row) => row.id);
}

function handlePendingSelectionChange(rows: Array<{ id: number }>) {
  selectedPendingFileIds.value = rows.map((row) => row.id);
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
  await runRenameDryRunForPreviews(selectedPreviewRows.value);
}

async function executeAllPreviews() {
  const renameableIds = new Set(getRenameablePreviewIds(previewStore.previews));
  await runRenameDryRunForPreviews(previewStore.previews.filter((preview) => renameableIds.has(preview.id)));
}

async function executeSinglePreview(row: RenamePreview) {
  await runRenameDryRunForPreviews([row]);
}

async function matchMetadata(row: RenamePreview) {
  metadataPreviewId.value = row.id;
  const updated = await previewStore.matchMetadata(row.id);
  if (updated.metadata_match_status === "low_confidence") {
    metadataCandidates.value = await previewStore.loadMetadataCandidates(row.id);
    metadataDialogVisible.value = true;
    return;
  }
  ElMessage.success(messages.renamePreviews.metadataDialog.matched);
}

async function applyMetadataCandidate(match: MetadataMatchResult) {
  if (!metadataPreviewId.value) {
    return;
  }
  await previewStore.applyMetadataCandidate(metadataPreviewId.value, match);
  metadataDialogVisible.value = false;
  metadataCandidates.value = [];
  ElMessage.success(messages.renamePreviews.metadataDialog.selectSuccess);
}

async function removePendingFile(row: { id: number }) {
  await pendingFileStore.removePendingFile(row.id);
  ElMessage.success(messages.renamePreviews.pendingFiles.removed);
}

async function clearPendingFiles() {
  await pendingFileStore.clearPendingFiles({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
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
  await pendingFileStore.movePendingFiles(selectedPendingFileIds.value, pendingMoveTargetDirectory.value.trim());
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
  await renameOperationStore.executeCurrentOperation();
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
  window.requestAnimationFrame(() => {
    void syncPreviewTopScrollWidth();
    bindPreviewBodyScroll();
  });
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
        <el-option :label="messages.status.generated" value="generated" />
        <el-option :label="messages.status.edited" value="edited" />
        <el-option :label="messages.status.needsReview" value="needs_review" />
      </el-select>
      <el-select v-model="previewStore.filters.media_type" class="rename-filter-select" :placeholder="messages.renamePreviews.type" clearable>
        <el-option :label="messages.renamePreviews.mediaTypes.movie" value="movie" />
        <el-option :label="messages.renamePreviews.mediaTypes.episode" value="episode" />
        <el-option :label="messages.renamePreviews.mediaTypes.unknown" value="unknown" />
      </el-select>
      <el-input v-model="previewStore.filters.keyword" class="rename-keyword-input" :placeholder="messages.renamePreviews.keywordPlaceholder" clearable />
    </template>

    <template #queryAction>
      <el-button :icon="Search" :disabled="!previewStore.filters.scan_job_id" @click="refreshPreviews">
        {{ messages.common.query }}
      </el-button>
      <el-button @click="resetRenamePreviews">{{ messages.common.reset }}</el-button>
    </template>

    <template #stats>
      <ListStatItem :label="messages.common.total" :value="previewStore.stats.total" />
      <ListStatItem :label="messages.status.generated" :value="previewStore.stats.generated" tone="success" />
      <ListStatItem :label="messages.status.needsReview" :value="previewStore.stats.needsReview" tone="warning" />
      <ListStatItem :label="messages.status.edited" :value="previewStore.stats.edited" tone="edited" />
      <ListStatItem :label="messages.status.renamed" :value="previewStore.stats.renamed" tone="renamed" />
    </template>

    <template #actions>
      <el-button
        type="success"
        :icon="Select"
        :disabled="selectedPreviewIds.length === 0"
        :loading="renameOperationStore.loading"
        @click="executeSelectedPreviews"
      >
        {{ messages.renamePreviews.executeSelected }}
      </el-button>
      <el-button type="primary" :icon="MagicStick" :loading="previewStore.loading" @click="generatePreviews">
        {{ messages.renamePreviews.generate }}
      </el-button>
      <el-button
        type="warning"
        :icon="Select"
        :disabled="previewStore.previews.length === 0"
        :loading="renameOperationStore.loading"
        @click="executeAllPreviews"
      >
        {{ messages.renamePreviews.executeAll }}
      </el-button>
      <el-button :icon="Refresh" @click="refreshPreviews">{{ messages.common.refresh }}</el-button>
    </template>

    <el-alert v-if="previewStore.errorMessage" type="error" :title="previewStore.errorMessage" show-icon />

    <template #table>
      <el-tabs v-model="activePreviewTab" class="rename-preview-tabs">
        <el-tab-pane :label="messages.renamePreviews.tabs.previews" name="previews">
          <div v-show="previewTopScrollVisible" ref="previewTableTopScroll" class="table-top-scroll" @scroll="handleTopPreviewScroll">
          <div class="table-top-scroll-track" :style="{ width: `${previewTopScrollWidth}px` }" />
          </div>
          <el-table
          ref="previewTable"
          :data="pagedPreviews"
          class="data-table rename-previews-table"
          table-layout="auto"
          :default-sort="defaultSort"
          @row-click="openDetailDialog"
          @selection-change="handleSelectionChange"
          @sort-change="handleSortChange"
          >
          <el-table-column type="selection" width="44" align="center" />
          <el-table-column
          prop="status"
          :label="messages.common.status"
          width="96"
          class-name="preview-compact-column preview-status-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
          </el-table-column>
          <el-table-column prop="file_name" :label="messages.renamePreviews.columns.originalName" min-width="150" align="left" header-align="left" sortable="custom">
          <template #default="{ row }">
          <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
          </el-table-column>
          <el-table-column
          prop="current_target_name"
          :label="messages.renamePreviews.columns.targetName"
          min-width="150"
          align="left"
          header-align="left"
          sortable="custom"
          >
          <template #default="{ row }">
          <TextCell :value="row.current_target_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
          </el-table-column>
          <el-table-column :label="messages.renamePreviews.columns.parsedTitle" min-width="176" align="left" header-align="left">
          <template #default="{ row }">
          <TextCell :value="row.parsed_title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
          </el-table-column>
          <el-table-column
          :label="messages.renamePreviews.columns.metadata"
          width="128"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          >
          <template #default="{ row }">
          <el-tag :type="metadataStatusTagType(row.metadata_match_status)" effect="light">
          {{ metadataStatusLabel(row.metadata_match_status) }}
          </el-tag>
          </template>
          </el-table-column>
          <el-table-column
          prop="metadata_match_score"
          :label="messages.renamePreviews.columns.metadataScore"
          width="112"
          class-name="metadata-score-column"
          header-class-name="metadata-score-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">{{ row.metadata_match_score ?? 0 }}%</template>
          </el-table-column>
          <el-table-column
          prop="media_type"
          :label="messages.renamePreviews.type"
          width="96"
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
          width="92"
          class-name="preview-year-column"
          align="center"
          header-align="center"
          sortable="custom"
          >
          <template #default="{ row }">{{ row.parsed_year ?? "-" }}</template>
          </el-table-column>
          <el-table-column
          :label="messages.renamePreviews.columns.seasonEpisode"
          width="104"
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
          <el-table-column :label="messages.common.actions" width="128" align="center" header-align="center" fixed="right">
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
          <el-tooltip :content="messages.renamePreviews.actions.edit" placement="top">
          <el-button class="table-action-button action-edit" :icon="Edit" text circle @click.stop="openEditDialog(row)" />
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
          <TablePagination pagination-key="rename-previews" :total="previewStore.previews.length" />
        </el-tab-pane>

        <el-tab-pane :label="messages.renamePreviews.tabs.pendingFiles" name="pending">
          <div class="subsection-header">
          <div>
          <h2>{{ messages.renamePreviews.pendingFiles.title }}</h2>
          <p>{{ messages.renamePreviews.pendingFiles.description }}</p>
          </div>
          <div class="subsection-actions">
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
          </div>
          <el-table
          :data="pendingFileStore.pendingFiles"
          class="data-table"
          table-layout="auto"
          @selection-change="handlePendingSelectionChange"
          >
          <el-table-column type="selection" width="44" align="center" />
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
          <el-table-column :label="messages.common.actions" width="96" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
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
    </template>

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

    <el-dialog v-model="metadataDialogVisible" :title="messages.renamePreviews.metadataDialog.title" width="860px">
      <el-empty v-if="metadataCandidates.length === 0" :description="messages.renamePreviews.metadataDialog.empty" />
      <el-table v-else :data="metadataCandidates" class="data-table" table-layout="auto">
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
        <el-table-column :label="messages.renamePreviews.columns.parsedTitle" min-width="180" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.targetName" min-width="180" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.candidate.original_title || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.renamePreviews.columns.year" width="84" align="center" header-align="center">
          <template #default="{ row }">{{ row.candidate.year ?? "-" }}</template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="96" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" @click="applyMetadataCandidate(row)">
              {{ messages.renamePreviews.metadataDialog.apply }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
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

    <el-dialog v-model="operationDialogVisible" :title="messages.renamePreviews.operationDialog.title" width="860px">
      <div class="rename-operation-dialog">
        <el-alert
          v-if="renameOperationStore.errorMessage"
          :title="renameOperationStore.errorMessage"
          type="error"
          show-icon
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
