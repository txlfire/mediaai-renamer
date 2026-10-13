<script setup lang="ts">
import { Files, MagicStick, Notebook, Refresh, Search, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { fetchMediaSource, fetchScanJob, fetchScanJobsPage, fetchScanModeSuggestion, type MediaSource, type ScanJob, type ScanMode } from "../api/client";
import { createScanTaskObserver, isScanActive } from "../utils/scanTaskObserver";
import ListPageLayout from "../components/ListPageLayout.vue";
import MediaSourceSelect from "../components/MediaSourceSelect.vue";
import OperationProgressLog from "../components/OperationProgressLog.vue";
import OperationLogDrawer from "../components/OperationLogDrawer.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { useAuthStore } from "../stores/auth";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatScanJobStatus } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const authStore = useAuthStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();
const router = useRouter();
const selectedSourceId = ref<number>();
const scanMode = ref<ScanMode>("full");
const scanLogDialogVisible = ref(false);
const selectedScanLog = ref("");
const operationLogVisible = ref(false);
const selectedOperationLogTaskId = ref<number | null>(null);
const scanProgressVisible = ref(false);
const scanProgressPercent = ref(0);
const scanProgressText = ref("");
const scanProgressLogs = ref<string[]>([]);
const scanProgressSummary = ref("");
const scanProgressStatus = ref<"success" | "warning" | "error">("success");
const scanProgressIndeterminate = ref(false);
const scanSubmitting = ref(false);
const defaultSort = { prop: "id", order: "descending" as const };
const pageText = messages.scanJobs;

const pagedScanJobs = ref<ScanJob[]>([]);
const scanJobTotal = ref(0);
const jobsLoading = ref(false);
const activeChecked = ref(false);
const activeScanJob = ref<ScanJob | null>(null);
let jobRequestVersion = 0;
let activeRequestVersion = 0;
let suggestionVersion = 0;
let viewActive = true;
const scanPagination = computed(() => paginationStore.getState("scan-jobs"));

const selectedSource = ref<MediaSource | null>(null);
let sourceRequestVersion = 0;
const canRunScan = computed(() => authStore.hasPermission("scan:run"));
const canReadLogs = computed(() => authStore.hasPermission("audit:read"));
const canStartScan = computed(() => Boolean(selectedSourceId.value && selectedSource.value?.enabled && canRunScan.value
  && !scanSubmitting.value && activeChecked.value && !activeScanJob.value));
const scanPermissionTitle = computed(() => (canRunScan.value ? "" : messages.auth.permissionDenied));
const scanModeSuggestion = computed(() => mediaStore.scanModeSuggestion);

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-jobs", event.prop, event.order);
  void queryScanJobs();
}

function applyScanJob(job: ScanJob) {
  if (!viewActive || (selectedSourceId.value && selectedSourceId.value !== job.media_source_id)) return;
  pagedScanJobs.value = pagedScanJobs.value.map((item) => item.id === job.id ? job : item);
  activeScanJob.value = isScanActive(job.status) ? job : null;
  scanProgressVisible.value = true;
  scanProgressIndeterminate.value = isScanActive(job.status);
  scanProgressText.value = formatMessage(pageText.scanProgressLive, { id: job.id, status: formatScanJobStatus(job.status),
    files: job.scanned_count ?? 0, videos: job.video_count ?? 0, warnings: job.warning_count ?? 0 });
  if (isScanActive(job.status)) {
    scanProgressPercent.value = 0;
    scanProgressSummary.value = "";
    return;
  }
  const failed = !["completed", "partial_completed"].includes(job.status);
  scanProgressPercent.value = failed ? 0 : 100;
  scanProgressStatus.value = failed ? "error" : job.status === "partial_completed" || mediaStore.scanRefreshFailed ? "warning" : "success";
  scanProgressSummary.value = failed ? (job.error_message || pageText.scanFailedSummary) : job.status === "partial_completed"
    ? formatMessage(pageText.scanPartialSummary, { videos: job.video_count ?? 0, warnings: job.warning_count ?? 0 })
    : formatMessage(pageText.scanSuccessSummary, { files: job.scanned_count ?? 0, videos: job.video_count ?? 0 });
  if (mediaStore.scanRefreshFailed) scanProgressSummary.value += ` ${pageText.scanRefreshFailed}`;
}

const scanObserver = createScanTaskObserver(fetchScanJob, applyScanJob, () => {
  scanProgressText.value = pageText.scanObservationFailed;
});
onUnmounted(() => {
  viewActive = false;
  jobRequestVersion += 1;
  activeRequestVersion += 1;
  suggestionVersion += 1;
  scanObserver.stop();
});

async function startScan() {
  if (!canStartScan.value) return;
  if (!selectedSourceId.value) {
    return;
  }
  if (!selectedSource.value?.enabled) {
    ElMessage.warning(pageText.disabledSource);
    return;
  }

  scanSubmitting.value = true;
  const sourceId = selectedSourceId.value;
  const sourceName = selectedSource.value.name;
  const requestedMode = scanMode.value;
  try {
    await ElMessageBox.confirm(
    pageText.confirmScan,
    messages.renamePreviews.confirmOperationTitle,
    {
      type: "warning",
      confirmButtonText: messages.common.confirm,
      cancelButtonText: messages.common.cancel,
    },
  );
  scanProgressVisible.value = true;
  scanProgressPercent.value = 0;
  scanProgressIndeterminate.value = true;
  scanProgressText.value = pageText.scanProgressStart;
  scanProgressLogs.value = [formatMessage(pageText.scanLogStart, { name: sourceName })];
  scanProgressSummary.value = "";
  if (!viewActive || selectedSourceId.value !== sourceId) return;
  const job = await mediaStore.startScan(sourceId, requestedMode, false);
  if (!viewActive || selectedSourceId.value !== sourceId) return;
  if (job) {
    applyScanJob(job);
    if (isScanActive(job.status)) scanObserver.start(job.id);
    await queryScanJobs(false);
    if (viewActive && selectedSourceId.value === sourceId) await loadScanModeSuggestion(sourceId);
  } else {
    scanProgressIndeterminate.value = false;
    scanProgressStatus.value = "warning";
    scanProgressSummary.value = pageText.scanAcceptanceUnknown;
  }
  } catch (error) {
    if (error !== "cancel" && error !== "close") throw error;
  } finally {
    scanSubmitting.value = false;
  }
}

async function refreshActiveScan() {
  const version = ++activeRequestVersion;
  activeChecked.value = false;
  try {
    const result = await fetchScanJobsPage({ media_source_id: selectedSourceId.value, active_only: true, page_size: 1 });
    if (!viewActive || version !== activeRequestVersion) return;
    activeScanJob.value = result.items[0] ?? null;
    activeChecked.value = true;
    if (activeScanJob.value) {
      applyScanJob(activeScanJob.value);
      scanObserver.start(activeScanJob.value.id);
    } else scanObserver.stop();
  } catch {
    if (viewActive && version === activeRequestVersion) {
      activeScanJob.value = null;
      ElMessage.error(pageText.loadFailed);
    }
  }
}

async function queryScanJobs(resetPage = true) {
  if (!viewActive) return;
  if (scanPagination.value.pageSize === 0) paginationStore.setPageSize("scan-jobs", 50);
  if (resetPage) paginationStore.setPage("scan-jobs", 1);
  const version = ++jobRequestVersion;
  const sourceId = selectedSourceId.value;
  const sort = tableSortStore.sorts["scan-jobs"] ?? defaultSort;
  jobsLoading.value = true;
  try {
    const result = await fetchScanJobsPage({ media_source_id: sourceId,
      page: scanPagination.value.currentPage, page_size: scanPagination.value.pageSize,
      sort_by: sort.order ? sort.prop : "id", sort_order: sort.order === "ascending" ? "asc" : "desc" });
    if (!viewActive || version !== jobRequestVersion || sourceId !== selectedSourceId.value) return;
    pagedScanJobs.value = result.items;
    scanJobTotal.value = result.total;
    paginationStore.setPage("scan-jobs", result.page);
    await refreshActiveScan();
  } catch {
    if (viewActive && version === jobRequestVersion) {
      pagedScanJobs.value = [];
      scanJobTotal.value = 0;
      ElMessage.error(pageText.loadFailed);
    }
  } finally {
    if (version === jobRequestVersion) jobsLoading.value = false;
  }
}

async function resetScanJobs() {
  selectedSourceId.value = undefined;
  scanMode.value = "full";
  mediaStore.scanModeSuggestion = null;
  jobRequestVersion += 1;
  jobsLoading.value = false;
  pagedScanJobs.value = [];
  scanJobTotal.value = 0;
  activeRequestVersion += 1;
  activeChecked.value = false;
  activeScanJob.value = null;
  scanObserver.stop();
}

async function loadScanModeSuggestion(mediaSourceId: number) {
  const version = ++suggestionVersion;
  try {
    const suggestion = await fetchScanModeSuggestion(mediaSourceId);
    if (!viewActive || version !== suggestionVersion || selectedSourceId.value !== mediaSourceId) return;
    mediaStore.scanModeSuggestion = suggestion;
    scanMode.value = suggestion.recommended_mode;
  } catch {
    if (!viewActive || version !== suggestionVersion || selectedSourceId.value !== mediaSourceId) return;
    mediaStore.scanModeSuggestion = null;
    scanMode.value = "full";
  }
}

function scanModeLabel(value: string) {
  return value === "incremental" ? pageText.scanModes.incremental : pageText.scanModes.full;
}

function viewScanResults(row: { id: number; media_source_id: number }) {
  void router.push({
    name: "scan-results",
    query: {
      media_source_id: String(row.media_source_id),
      scan_job_id: String(row.id),
    },
  });
}

function viewRenamePreviews(row: { id: number; media_source_id: number }) {
  void router.push({
    name: "rename-previews",
    query: {
      media_source_id: String(row.media_source_id),
      scan_job_id: String(row.id),
    },
  });
}

function scanStatusText(row: { status: string; error_message?: string | null }) {
  if (row.status === "partial_completed" && row.error_message) {
    return row.error_message;
  }
  return formatScanJobStatus(row.status);
}

function scanStatusTagType(status: string) {
  if (status === "completed") {
    return "success";
  }
  if (status === "partial_completed") {
    return "warning";
  }
  if (status === "failed") {
    return "danger";
  }
  return "info";
}

function hasScanDetail(row: { status: string; error_message?: string | null }) {
  return Boolean(row.error_message);
}

function openScanLog(row: { error_message?: string | null }) {
  selectedScanLog.value = row.error_message || messages.common.emptyLogs;
  scanLogDialogVisible.value = true;
}

function openScanOperationLog(row: { id: number }) {
  selectedOperationLogTaskId.value = row.id;
  operationLogVisible.value = true;
}

onMounted(async () => {
  if (!viewActive) return;
  const routeSourceId = Number(route.query.media_source_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
    await nextTick();
    await queryScanJobs();
  } else {
    await queryScanJobs();
    if (!viewActive) return;
    const active = activeScanJob.value;
    if (active) {
      selectedSourceId.value = active.media_source_id;
      // 等待选源watch清理旧观察器后，再接续已经存在的任务。
      await nextTick();
      await queryScanJobs();
    }
  }
});

watch(selectedSourceId, async (value) => {
  selectedSource.value = null;
  const sourceVersion = ++sourceRequestVersion;
  scanObserver.stop();
  scanProgressVisible.value = false;
  jobRequestVersion += 1;
  suggestionVersion += 1;
  jobsLoading.value = false;
  pagedScanJobs.value = [];
  scanJobTotal.value = 0;
  activeChecked.value = false;
  activeScanJob.value = null;
  activeRequestVersion += 1;
  if (!value) {
    mediaStore.scanModeSuggestion = null;
    scanMode.value = "full";
    return;
  }
  void refreshActiveScan();
  try {
    const source = await fetchMediaSource(value);
    if (viewActive && sourceVersion === sourceRequestVersion && value === selectedSourceId.value) selectedSource.value = source;
  } catch {
    if (viewActive && sourceVersion === sourceRequestVersion) ElMessage.error(messages.selectors.selectionUnavailable);
  }
  await loadScanModeSuggestion(value);
});

watch(() => route.query.media_source_id, async (value) => {
  if (!viewActive) return;
  const sourceId = Number(value);
  selectedSourceId.value = Number.isSafeInteger(sourceId) && sourceId > 0 ? sourceId : undefined;
  await nextTick();
  await queryScanJobs();
});
</script>

<template>
  <ListPageLayout :title="messages.scanJobs.title" :description="messages.scanJobs.description">
    <template #filters>
      <MediaSourceSelect
        v-model="selectedSourceId"
        class="source-select scan-job-source-select tall-select"
        popper-class="tall-select-dropdown"
        :placeholder="messages.scanJobs.selectMediaSource"
        enabled-only
      />
      <el-button class="query-action-button" :icon="Search" :disabled="!selectedSourceId" :loading="jobsLoading" @click="queryScanJobs()">{{ messages.common.query }}</el-button>
      <el-button @click="resetScanJobs">{{ messages.common.reset }}</el-button>
    </template>

    <template #filterActions>
      <div class="scan-mode-selector">
        <el-radio-group v-model="scanMode" :disabled="!selectedSourceId || mediaStore.loading" size="large">
          <el-radio-button value="full">{{ messages.scanJobs.scanModes.full }}</el-radio-button>
          <el-radio-button value="incremental">{{ messages.scanJobs.scanModes.incremental }}</el-radio-button>
        </el-radio-group>
        <el-tooltip
          v-if="scanModeSuggestion"
          :content="scanModeSuggestion.reason"
          placement="top"
        >
          <el-tag type="info" effect="light">
            {{ formatMessage(messages.scanJobs.recommendedMode, { mode: scanModeLabel(scanModeSuggestion.recommended_mode) }) }}
          </el-tag>
        </el-tooltip>
      </div>
      <el-button
        type="primary"
        :icon="VideoPlay"
        :disabled="!canStartScan"
        :title="scanPermissionTitle"
        :loading="mediaStore.loading"
        @click="startScan"
      >
        {{ messages.scanJobs.startScan }}
      </el-button>
      <el-button :icon="Refresh" :disabled="!selectedSourceId || jobsLoading" @click="queryScanJobs(false)">{{ messages.common.refresh }}</el-button>
      <el-button :icon="Notebook" :disabled="!canReadLogs" :title="canReadLogs ? '' : messages.auth.permissionDenied" @click="mediaStore.openLogDrawer">{{ messages.scanJobs.viewLogs }}</el-button>
    </template>

    <el-alert v-if="mediaStore.errorMessage" type="error" :title="mediaStore.errorMessage" show-icon />
    <OperationProgressLog
      :visible="scanProgressVisible"
      :percentage="scanProgressPercent"
      :text="scanProgressText"
      :logs="scanProgressLogs"
      :summary="scanProgressSummary"
      :status="scanProgressStatus"
      :indeterminate="scanProgressIndeterminate"
    />

    <template #table>
      <el-table
        v-loading="jobsLoading"
        :data="pagedScanJobs"
        class="data-table scan-jobs-table"
        height="100%"
        table-layout="auto"
        :default-sort="defaultSort"
        @sort-change="handleSortChange"
      >
        <el-table-column prop="id" :label="messages.scanJobs.columns.taskId" min-width="92" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="scan_mode" :label="messages.scanJobs.columns.scanMode" min-width="92" align="center" header-align="center" sortable="custom">
          <template #default="{ row }">
            <el-tag effect="light" :type="row.scan_mode === 'incremental' ? 'success' : 'info'">
              {{ scanModeLabel(row.scan_mode) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.status" min-width="240" align="left" header-align="left">
          <template #default="{ row }">
            <div class="scan-status-cell">
              <el-tag :type="scanStatusTagType(row.status)" effect="light">
                {{ formatScanJobStatus(row.status) }}
              </el-tag>
              <el-tooltip
                v-if="hasScanDetail(row)"
                :content="row.error_message"
                placement="top"
              >
                <button class="scan-status-detail-button" type="button" @click.stop="openScanLog(row)">
                  <TextCell :value="scanStatusText(row)" :max-length="tableDisplayConfig.statusMaxLength" />
                </button>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="scanned_count" :label="messages.scanJobs.columns.scanned" min-width="90" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="video_count" :label="messages.scanJobs.columns.videos" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="new_count" :label="messages.scanJobs.columns.newFiles" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="changed_count" :label="messages.scanJobs.columns.changedFiles" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="skipped_count" :label="messages.scanJobs.columns.skippedFiles" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="missing_count" :label="messages.scanJobs.columns.missingFiles" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="warning_count" :label="messages.scanJobs.columns.warnings" min-width="76" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="batch_size" :label="messages.scanJobs.columns.batchSize" min-width="100" align="center" header-align="center" sortable="custom" />
        <el-table-column
          prop="batch_interval_seconds"
          :label="messages.scanJobs.columns.interval"
          width="64"
          class-name="interval-column"
          header-class-name="interval-column"
          align="center"
          header-align="center"
          sortable="custom"
        />
        <el-table-column
          prop="started_at"
          :label="messages.scanJobs.columns.startedAt"
          min-width="156"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          sortable="custom"
        >
          <template #default="{ row }">
            {{ formatDateTime(row.started_at) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="ended_at"
          :label="messages.scanJobs.columns.endedAt"
          min-width="156"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          sortable="custom"
        >
          <template #default="{ row }">
            {{ formatDateTime(row.ended_at) }}
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="112" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-tooltip :content="messages.scanJobs.viewResults" placement="top">
                <el-button
                  class="table-action-button action-view"
                  :icon="Files"
                  text
                  circle
                  @click="viewScanResults(row)"
                />
              </el-tooltip>
              <el-tooltip :content="messages.scanJobs.viewPreviews" placement="top">
                <el-button
                  class="table-action-button action-magic"
                  :icon="MagicStick"
                  text
                  circle
                  @click="viewRenamePreviews(row)"
                />
              </el-tooltip>
              <el-tooltip :content="canReadLogs ? messages.scanJobs.viewLogs : messages.auth.permissionDenied" placement="top">
                <el-button
                  class="table-action-button action-view"
                  :icon="Notebook"
                  :disabled="!canReadLogs"
                  text
                  circle
                  @click="openScanOperationLog(row)"
                />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template #pagination>
      <TablePagination pagination-key="scan-jobs" :total="scanJobTotal" server
        @page-change="queryScanJobs(false)" @page-size-change="queryScanJobs()" />
    </template>

    <el-dialog v-model="scanLogDialogVisible" :title="messages.scanJobs.viewLogs" width="640px">
      <p class="scan-job-log-detail">{{ selectedScanLog }}</p>
      <template #footer>
        <el-button @click="scanLogDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>
    <OperationLogDrawer
      v-model:visible="operationLogVisible"
      task-type="scan_job"
      :task-id="selectedOperationLogTaskId"
      :title="messages.operationLogs.scanTitle"
    />
  </ListPageLayout>
</template>
