<script setup lang="ts">
import { Refresh, Search } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute } from "vue-router";

import FullscreenTablePanel from "../components/FullscreenTablePanel.vue";
import ListPageLayout from "../components/ListPageLayout.vue";
import MediaSourceSelect from "../components/MediaSourceSelect.vue";
import ScanJobSelect from "../components/ScanJobSelect.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatFileSize } from "../utils/displayFormat";
import { fetchMediaFilesPage } from "../api/client";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();
const selectedSourceId = ref<number>();
const selectedScanJobId = ref<number>();
const detailDialogVisible = ref(false);
const selectedDetailRow = ref<Record<string, unknown> | null>(null);
const defaultSort = { prop: "modified_at", order: "descending" as const };

const mediaFileTotal = ref(0);
const mediaFilesLoading = ref(false);
let mediaRequestVersion = 0;
let viewActive = true;
const pagedMediaFiles = computed(() => mediaStore.mediaFiles);
const scanResultsPagination = computed(() => paginationStore.getState("scan-results"));


function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-results", event.prop, event.order);
  void queryMediaFiles();
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

function clearMediaFiles() {
  mediaRequestVersion += 1;
  mediaFilesLoading.value = false;
  mediaStore.mediaFiles = [];
  mediaFileTotal.value = 0;
  detailDialogVisible.value = false;
  selectedDetailRow.value = null;
}

async function queryMediaFiles(resetPage = true) {
  if (!viewActive) return;
  if (!selectedScanJobId.value) {
    clearMediaFiles();
    return;
  }
  if (scanResultsPagination.value.pageSize === 0) paginationStore.setPageSize("scan-results", 50);
  if (resetPage) paginationStore.setPage("scan-results", 1);
  const version = ++mediaRequestVersion;
  mediaFilesLoading.value = true;
  const sort = tableSortStore.sorts["scan-results"] ?? defaultSort;
  try {
    const result = await fetchMediaFilesPage({
      media_source_id: selectedSourceId.value, scan_job_id: selectedScanJobId.value,
      page: scanResultsPagination.value.currentPage, page_size: scanResultsPagination.value.pageSize,
      sort_by: !sort.order || sort.prop === "__sequence" ? "id" : sort.prop,
      sort_order: sort.order === "descending" ? "desc" : "asc",
    });
    if (version !== mediaRequestVersion) return;
    mediaStore.mediaFiles = result.items;
    mediaFileTotal.value = result.total;
    paginationStore.setPage("scan-results", result.page);
  } catch {
    if (version === mediaRequestVersion) {
      mediaStore.mediaFiles = [];
      mediaFileTotal.value = 0;
      ElMessage.error(messages.scanResults.loadFailed);
    }
  } finally {
    if (version === mediaRequestVersion) mediaFilesLoading.value = false;
  }
}

async function queryScanJobsForSource() {
  selectedScanJobId.value = undefined;
  clearMediaFiles();
}

async function resetScanResults() {
  selectedSourceId.value = undefined;
  selectedScanJobId.value = undefined;
  mediaStore.scanJobs = [];
  clearMediaFiles();
}

onMounted(async () => {
  if (!viewActive) return;
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
    await queryScanJobsForSource();
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    selectedScanJobId.value = routeScanJobId;
    await queryMediaFiles();
  }
});

onBeforeUnmount(() => {
  viewActive = false;
  mediaRequestVersion += 1;
});
</script>

<template>
  <ListPageLayout class="scan-results-page" :title="messages.scanResults.title" :description="messages.scanResults.description">
    <template #filters>
      <MediaSourceSelect
        v-model="selectedSourceId"
        :placeholder="messages.scanResults.selectMediaSource"
        @change="queryScanJobsForSource"
      />
      <ScanJobSelect v-model="selectedScanJobId" :media-source-id="selectedSourceId" :placeholder="messages.scanResults.selectScanJob" @change="clearMediaFiles" />
      <el-button class="query-action-button" :icon="Search" :loading="mediaFilesLoading" :disabled="!selectedScanJobId" @click="queryMediaFiles()">
        {{ messages.common.query }}
      </el-button>
      <el-button @click="resetScanResults">{{ messages.common.reset }}</el-button>
    </template>

    <template #filterActions>
      <el-button class="scan-results-refresh-action" :icon="Refresh" :disabled="!selectedScanJobId || mediaFilesLoading" @click="queryMediaFiles(false)">
        {{ messages.common.refresh }}
      </el-button>
    </template>

    <template #table>
      <FullscreenTablePanel :title="messages.scanResults.title">
        <el-table
          v-loading="mediaFilesLoading"
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
        <TablePagination pagination-key="scan-results" :total="mediaFileTotal" server
          @page-change="queryMediaFiles(false)" @page-size-change="queryMediaFiles()" />
      </FullscreenTablePanel>
    </template>

    <el-dialog v-model="detailDialogVisible" :title="messages.scanResults.detailTitle" width="min(720px, 92vw)">
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
