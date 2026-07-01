<script setup lang="ts">
import { Files, MagicStick, Notebook, Refresh, Search, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import ListPageLayout from "../components/ListPageLayout.vue";
import OperationProgressLog from "../components/OperationProgressLog.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatScanJobStatus } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();
const router = useRouter();
const selectedSourceId = ref<number>();
const scanLogDialogVisible = ref(false);
const selectedScanLog = ref("");
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
const canStartScan = computed(() => Boolean(selectedSourceId.value && selectedSource.value?.enabled));

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
  await mediaStore.startScan(selectedSourceId.value);
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
}

async function resetScanJobs() {
  selectedSourceId.value = undefined;
  mediaStore.scanJobs = [];
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

onMounted(async () => {
  await mediaStore.loadMediaSources();
  const routeSourceId = Number(route.query.media_source_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
    await queryScanJobs();
  }
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
      <el-button
        type="primary"
        :icon="VideoPlay"
        :disabled="!canStartScan"
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
                  :disabled="!row.error_message"
                  :icon="Notebook"
                  text
                  circle
                  @click="openScanLog(row)"
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
  </ListPageLayout>
</template>
