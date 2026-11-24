<script setup lang="ts">
import { Refresh, Search, View } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute } from "vue-router";

import FullscreenTablePanel from "../components/FullscreenTablePanel.vue";
import ListPageLayout from "../components/ListPageLayout.vue";
import MediaSourceSelect from "../components/MediaSourceSelect.vue";
import ScanJobSelect from "../components/ScanJobSelect.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatFileSize } from "../utils/displayFormat";
import { fetchMediaFilesPage } from "../api/client";
import { isSessionChangedError } from "../utils/sessionGeneration";

const { messages } = useInterfaceLanguage();

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();
const selectedSourceId = ref<number>();
const selectedScanJobId = ref<number>();
const detailDialogVisible = ref(false);
const selectedDetailRow = ref<Record<string, unknown> | null>(null);
const tableActionsHost = ref<HTMLElement | null>(null);
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
    { key: "fileName", label: messages.value.scanResults.columns.fileName, value: row.file_name },
    { key: "path", label: messages.value.scanResults.columns.path, value: row.file_path },
    { key: "format", label: messages.value.scanResults.columns.format, value: row.extension },
    { key: "size", label: messages.value.scanResults.columns.size, value: formatFileSize(row.file_size) },
    { key: "taskId", label: messages.value.scanResults.columns.taskId, value: row.scan_job_id },
    { key: "modifiedAt", label: messages.value.scanResults.columns.modifiedAt, value: formatDateTime(row.modified_at) },
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
  } catch (error) {
    if (isSessionChangedError(error)) return;
    if (version === mediaRequestVersion) {
      mediaStore.mediaFiles = [];
      mediaFileTotal.value = 0;
      ElMessage.error(messages.value.scanResults.loadFailed);
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

watch(() => [route.query.media_source_id, route.query.scan_job_id], async () => {
  if (!viewActive) return;
  clearMediaFiles();
  selectedSourceId.value = undefined;
  selectedScanJobId.value = undefined;
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    selectedSourceId.value = routeSourceId;
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    selectedScanJobId.value = routeScanJobId;
    await queryMediaFiles();
  }
}, { immediate: true });

onBeforeUnmount(() => {
  viewActive = false;
  mediaRequestVersion += 1;
});
</script>

<template>
  <ListPageLayout class="scan-results-page" :title="messages.scanResults.title" :description="messages.scanResults.description" flat-table>
    <template #filters>
      <MediaSourceSelect
        v-model="selectedSourceId"
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
      <span ref="tableActionsHost" class="table-view-actions" />
    </template>

    <template #table>
      <FullscreenTablePanel :actions-host="tableActionsHost">
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
              <div class="scan-result-file-cell">
                <div class="scan-result-file-name">
                  <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
                </div>
                <el-tooltip :trigger-keys="[]" :content="messages.common.detail" placement="top">
                  <el-button
                    class="table-action-button action-view scan-result-detail-trigger"
                    :icon="View"
                    :aria-label="`${messages.common.detail}: ${row.file_name}`"
                    text
                    circle
                    @click.stop="handleRowClick(row)"
                  />
                </el-tooltip>
              </div>
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

    <el-dialog
      v-model="detailDialogVisible"
      :title="messages.scanResults.detailTitle"
      width="min(720px, calc(100vw - 24px))"
      align-center
      destroy-on-close
    >
      <dl class="scan-result-detail-fields">
        <div v-for="item in detailRows" :key="item.key" :class="{ 'is-wide': item.key === 'fileName' || item.key === 'path' }">
          <dt>{{ item.label }}</dt>
          <dd>{{ item.value ?? "-" }}</dd>
        </div>
      </dl>
      <template #footer>
        <el-button @click="detailDialogVisible = false">{{ messages.common.close }}</el-button>
      </template>
    </el-dialog>
  </ListPageLayout>
</template>

<style scoped>
.scan-result-file-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.scan-result-file-name {
  flex: 1 1 auto;
  min-width: 0;
}

.scan-result-detail-trigger {
  flex: 0 0 auto;
}

.scan-result-detail-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr));
  gap: 12px 16px;
  margin: 0;
  overflow-wrap: anywhere;
}

.scan-result-detail-fields .is-wide {
  grid-column: 1 / -1;
}

.scan-result-detail-fields dt {
  margin-bottom: 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.scan-result-detail-fields dd {
  margin: 0;
  color: var(--el-text-color-primary);
  white-space: pre-wrap;
}

.scan-result-detail-fields .is-wide dd {
  font-weight: 600;
}
</style>
