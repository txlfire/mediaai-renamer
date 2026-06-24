<script setup lang="ts">
/**
 * 扫描结果页面。
 *
 * 展示 M1 识别到的视频文件，不提供预览和重命名操作。
 */

import { Refresh, Search } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
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
const defaultSort = { prop: "modified_at", order: "descending" as const };
const pagedMediaFiles = computed(() =>
  paginationStore.paginate(
    "scan-results",
    tableSortStore.applySort("scan-results", mediaStore.mediaFiles),
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
    label: `任务 ${job.id}`,
    value: job.id,
  })),
);

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-results", event.prop, event.order);
}

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
  <section class="workspace-page">
    <div class="page-header">
      <div>
        <h1>扫描结果</h1>
        <p>查看当前已识别的视频文件列表。命名预览和重命名会在后续阶段加入。</p>
      </div>
      <el-button :icon="Refresh" @click="queryMediaFiles">刷新</el-button>
    </div>

    <div class="scan-toolbar">
      <el-select
        v-model="selectedSourceId"
        placeholder="选择媒体源"
        clearable
        class="source-select"
        @change="queryScanJobsForSource"
        @clear="queryScanJobsForSource"
      >
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="selectedScanJobId" placeholder="选择扫描任务" clearable class="source-select">
        <el-option v-for="item in scanJobOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-button :icon="Search" :disabled="!selectedScanJobId" @click="queryMediaFiles">查询</el-button>
    </div>

    <el-table
      :data="pagedMediaFiles"
      class="data-table"
      :default-sort="defaultSort"
      @sort-change="handleSortChange"
    >
      <el-table-column label="文件名" min-width="320" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
        </template>
      </el-table-column>
      <el-table-column label="格式" width="100" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.extension" :max-length="tableDisplayConfig.extensionMaxLength" />
        </template>
      </el-table-column>
      <el-table-column
        prop="file_size"
        label="大小"
        width="130"
        align="center"
        header-align="center"
        sortable="custom"
      >
        <template #default="{ row }">
          {{ formatFileSize(row.file_size) }}
        </template>
      </el-table-column>
      <el-table-column prop="scan_job_id" label="任务 ID" width="100" align="center" header-align="center" />
      <el-table-column label="路径" min-width="460" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.file_path" :max-length="tableDisplayConfig.pathMaxLength" />
        </template>
      </el-table-column>
      <el-table-column
        prop="modified_at"
        label="修改时间"
        min-width="180"
        align="center"
        header-align="center"
        sortable="custom"
      >
        <template #default="{ row }">
          {{ formatDateTime(row.modified_at) }}
        </template>
      </el-table-column>
    </el-table>

    <TablePagination pagination-key="scan-results" :total="mediaStore.mediaFiles.length" />
  </section>
</template>
