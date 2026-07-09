<script setup lang="ts">
import { Files, MagicStick, Notebook, Refresh, Search, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type { ScanMode } from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
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
const defaultSort = { prop: "id", order: "descending" as const };
const pageText = messages.scanJobs;

const pagedScanJobs = computed(() =>
  paginationStore.paginate("scan-jobs", tableSortStore.applySort("scan-jobs", mediaStore.scanJobs)),
);

const sourceOptions = computed(() =>
  mediaStore.mediaSources
    .filter((source) => source.enabled)
    .map((source) => ({
      label: source.name,
      value: source.id,
    })),
);
const selectedSource = computed(() =>
  mediaStore.mediaSources.find((source) => source.id === selectedSourceId.value),
);
const canRunScan = computed(() => authStore.hasPermission("scan:run"));
const canStartScan = computed(() => Boolean(selectedSourceId.value && selectedSource.value?.enabled && canRunScan.value));
const scanPermissionTitle = computed(() => (canRunScan.value ? "" : messages.auth.permissionDenied));
const scanModeSuggestion = computed(() => mediaStore.scanModeSuggestion);

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-jobs", event.prop, event.order);
}

async function startScan() {
  if (!selectedSourceId.value) {
    return;
  }
  if (!selectedSource.value?.enabled) {
    ElMessage.warning(pageText.disabledSource);
    return;
  }

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
  scanProgressPercent.value = 15;
  scanProgressText.value = pageText.scanProgressStart;
  scanProgressLogs.value = [formatMessage(pageText.scanLogStart, { name: selectedSource.value.name })];
  scanProgressSummary.value = "";
  await mediaStore.startScan(selectedSourceId.value, scanMode.value);
  scanProgressPercent.value = 100;
  scanProgressText.value = pageText.scanProgressDone;
  scanProgressSummary.value = mediaStore.errorMessage ? pageText.scanFailedSummary : pageText.scanSuccessSummary;
}

async function queryScanJobs() {
  if (!selectedSourceId.value) {
    mediaStore.scanJobs = [];
    return;
  }
  await mediaStore.loadScanJobs({ media_source_id: selectedSourceId.value });
  await loadScanModeSuggestion(selectedSourceId.value);
}

async function resetScanJobs() {
  selectedSourceId.value = undefined;
  scanMode.value = "full";
  mediaStore.scanModeSuggestion = null;
  mediaStore.scanJobs = [];
}

async function loadScanModeSuggestion(mediaSourceId: number) {
  try {
    const suggestion = await mediaStore.loadScanModeSuggestion(mediaSourceId);
    scanMode.value = suggestion.recommended_mode;
  } catch {
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
  return row.status === "partial_completed" && Boolean(row.error_message);
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
  await mediaStore.loadMediaSources();
  const routeSourceId = Number(route.query.media_source_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
    await queryScanJobs();
  }
});

watch(selectedSourceId, async (value) => {
  if (!value) {
    mediaStore.scanModeSuggestion = null;
    scanMode.value = "full";
    return;
  }
  await loadScanModeSuggestion(value);
});
</script>

<template>
  <ListPageLayout :title="messages.scanJobs.title" :description="messages.scanJobs.description">
    <template #filters>
      <el-select
        v-model="selectedSourceId"
        class="source-select scan-job-source-select tall-select"
        popper-class="tall-select-dropdown"
        :placeholder="messages.scanJobs.selectMediaSource"
        clearable
      >
        <el-option
          v-for="item in sourceOptions"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <el-button class="query-action-button" :icon="Search" :disabled="!selectedSourceId" @click="queryScanJobs">{{ messages.common.query }}</el-button>
      <el-button @click="resetScanJobs">{{ messages.common.reset }}</el-button>
    </template>

    <template #filterActions>
      <div class="scan-mode-selector">
        <el-radio-group v-model="scanMode" :disabled="!selectedSourceId || mediaStore.loading" size="large">
          <el-radio-button label="full">{{ messages.scanJobs.scanModes.full }}</el-radio-button>
          <el-radio-button label="incremental">{{ messages.scanJobs.scanModes.incremental }}</el-radio-button>
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
      <el-button :icon="Refresh" :disabled="!selectedSourceId" @click="queryScanJobs">{{ messages.common.refresh }}</el-button>
      <el-button :icon="Notebook" @click="mediaStore.openLogDrawer">{{ messages.scanJobs.viewLogs }}</el-button>
    </template>

    <el-alert v-if="mediaStore.errorMessage" type="error" :title="mediaStore.errorMessage" show-icon />
    <OperationProgressLog
      :visible="scanProgressVisible"
      :percentage="scanProgressPercent"
      :text="scanProgressText"
      :logs="scanProgressLogs"
      :summary="scanProgressSummary"
    />

    <template #table>
      <el-table
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
        <el-table-column :label="messages.common.actions" min-width="96" align="center" header-align="center" fixed="right">
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
              <el-tooltip :content="messages.scanJobs.viewLogs" placement="top">
                <el-button
                  class="table-action-button action-view"
                  :icon="Notebook"
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
      <TablePagination pagination-key="scan-jobs" :total="mediaStore.scanJobs.length" />
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
