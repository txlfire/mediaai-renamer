<script setup lang="ts">
import { Refresh, Search } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import ListPageLayout from "../components/ListPageLayout.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatFileSize } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();
const selectedSourceId = ref<number>();
const selectedScanJobId = ref<number>();
const detailDialogVisible = ref(false);
const selectedDetailRow = ref<Record<string, unknown> | null>(null);
const defaultSort = { prop: "modified_at", order: "descending" as const };

const pagedMediaFiles = computed(() =>
  paginationStore.paginate(
    "scan-results",
    tableSortStore.applySort("scan-results", mediaStore.mediaFiles),
  ),
);
const scanResultsPagination = computed(() => paginationStore.getState("scan-results"));

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

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-results", event.prop, event.order);
}

function sequenceNumber(index: number) {
  const state = scanResultsPagination.value;
  if (state.pageSize === 0) {
    return index + 1;
  }
  return (state.currentPage - 1) * state.pageSize + index + 1;
}

function handleRowClick(row: Record<string, unknown>) {
  selectedDetailRow.value = row;
  detailDialogVisible.value = true;
}

const detailRows = computed(() => {
  const row = selectedDetailRow.value;
  if (!row) {
    return [];
  }
  return [
    { label: messages.scanResults.columns.fileName, value: row.file_name },
    { label: messages.scanResults.columns.format, value: row.extension },
    { label: messages.scanResults.columns.size, value: formatFileSize(row.file_size) },
    { label: messages.scanResults.columns.taskId, value: row.scan_job_id },
    { label: messages.scanResults.columns.path, value: row.file_path },
    { label: messages.scanResults.columns.modifiedAt, value: formatDateTime(row.modified_at) },
  ];
});

async function queryMediaFiles() {
  if (!selectedScanJobId.value) {
    mediaStore.mediaFiles = [];
    return;
  }
  await mediaStore.loadMediaFiles({
    media_source_id: selectedSourceId.value,
    scan_job_id: selectedScanJobId.value,
  });
}

async function queryScanJobsForSource() {
  selectedScanJobId.value = undefined;
  mediaStore.mediaFiles = [];
  if (!selectedSourceId.value) {
    mediaStore.scanJobs = [];
    return;
  }
  await mediaStore.loadScanJobs({ media_source_id: selectedSourceId.value });
}

async function resetScanResults() {
  selectedSourceId.value = undefined;
  selectedScanJobId.value = undefined;
  mediaStore.scanJobs = [];
  mediaStore.mediaFiles = [];
  await mediaStore.loadMediaSources();
}

onMounted(async () => {
  await mediaStore.loadMediaSources();
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
    await mediaStore.loadScanJobs({ media_source_id: routeSourceId });
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    selectedScanJobId.value = routeScanJobId;
    await queryMediaFiles();
  }
});
</script>

<template>
  <ListPageLayout :title="messages.scanResults.title" :description="messages.scanResults.description">
    <template #filters>
      <el-select
        v-model="selectedSourceId"
        :placeholder="messages.scanResults.selectMediaSource"
        clearable
        @change="queryScanJobsForSource"
        @clear="queryScanJobsForSource"
      >
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="selectedScanJobId" :placeholder="messages.scanResults.selectScanJob" clearable>
        <el-option v-for="item in scanJobOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-button class="query-action-button" :icon="Search" :disabled="!selectedScanJobId" @click="queryMediaFiles">
        {{ messages.common.query }}
      </el-button>
      <el-button @click="resetScanResults">{{ messages.common.reset }}</el-button>
    </template>

    <template #filterActions>
      <el-button class="scan-results-refresh-action" :icon="Refresh" :disabled="!selectedScanJobId" @click="queryMediaFiles">
        {{ messages.common.refresh }}
      </el-button>
    </template>

    <template #table>
      <el-table
        :data="pagedMediaFiles"
        class="data-table scan-results-table"
        height="100%"
        table-layout="auto"
        :default-sort="defaultSort"
        @row-click="handleRowClick"
        @sort-change="handleSortChange"
      >
        <el-table-column
          prop="__sequence"
          :label="messages.common.sequence"
          width="76"
          align="center"
          header-align="center"
          fixed="left"
          sortable="custom"
        >
          <template #default="{ $index }">
            {{ sequenceNumber($index) }}
          </template>
        </el-table-column>
        <el-table-column :label="messages.scanResults.columns.fileName" min-width="280" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.scanResults.columns.format" width="72" align="center" header-align="center">
          <template #default="{ row }">
            <TextCell :value="row.extension" :max-length="6" />
          </template>
        </el-table-column>
        <el-table-column
          prop="file_size"
          :label="messages.scanResults.columns.size"
          width="112"
          align="center"
          header-align="center"
          sortable="custom"
        >
          <template #default="{ row }">
            {{ formatFileSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column prop="scan_job_id" :label="messages.scanResults.columns.taskId" width="96" align="center" header-align="center" />
        <el-table-column :label="messages.scanResults.columns.path" min-width="360" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.file_path" :max-length="tableDisplayConfig.pathMaxLength" />
          </template>
        </el-table-column>
        <el-table-column
          prop="modified_at"
          :label="messages.scanResults.columns.modifiedAt"
          width="168"
          class-name="nowrap-column"
          align="center"
          header-align="center"
          sortable="custom"
        >
          <template #default="{ row }">
            {{ formatDateTime(row.modified_at) }}
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template #pagination>
      <TablePagination pagination-key="scan-results" :total="mediaStore.mediaFiles.length" />
    </template>

    <el-dialog v-model="detailDialogVisible" :title="messages.scanResults.detailTitle" width="720px">
      <div class="detail-panel scan-result-detail-panel">
        <div v-for="item in detailRows" :key="item.label" class="detail-item">
          <span>{{ item.label }}</span>
          <strong>{{ item.value ?? "-" }}</strong>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>
  </ListPageLayout>
</template>
