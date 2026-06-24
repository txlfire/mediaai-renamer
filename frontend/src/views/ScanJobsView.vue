<script setup lang="ts">
/**
 * 扫描任务页面。
 *
 * 用于选择媒体源并启动全量分批扫描。
 */

import { Notebook, Refresh, VideoPlay } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatScanJobStatus } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const selectedSourceId = ref<number>();
const defaultSort = { prop: "id", order: "descending" as const };
const pagedScanJobs = computed(() =>
  paginationStore.paginate("scan-jobs", tableSortStore.applySort("scan-jobs", mediaStore.scanJobs)),
);

const sourceOptions = computed(() =>
  mediaStore.mediaSources.map((source) => ({
    label: source.name,
    value: source.id,
  })),
);

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-jobs", event.prop, event.order);
}

async function startScan() {
  if (!selectedSourceId.value) {
    return;
  }

  await mediaStore.startScan(selectedSourceId.value);
}

onMounted(async () => {
  await Promise.all([mediaStore.loadMediaSources(), mediaStore.loadScanJobs()]);
});
</script>

<template>
  <section class="workspace-page">
    <div class="page-header">
      <div>
        <h1>扫描任务</h1>
        <p>选择媒体源后启动全量扫描。M1 使用分批扫描，默认每批 100 个文件、间隔 1 秒。</p>
      </div>
      <div class="page-actions">
        <el-button :icon="Notebook" @click="mediaStore.openLogDrawer">查看日志</el-button>
        <el-button :icon="Refresh" @click="mediaStore.loadScanJobs">刷新</el-button>
      </div>
    </div>

    <div class="scan-toolbar">
      <el-select v-model="selectedSourceId" placeholder="选择媒体源" class="source-select">
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-button type="primary" :icon="VideoPlay" :loading="mediaStore.loading" @click="startScan">
        开始全量扫描
      </el-button>
    </div>

    <el-alert v-if="mediaStore.errorMessage" type="error" :title="mediaStore.errorMessage" show-icon />

    <el-table
      :data="pagedScanJobs"
      class="data-table"
      :default-sort="defaultSort"
      @sort-change="handleSortChange"
    >
      <el-table-column prop="id" label="任务 ID" width="110" align="center" header-align="center" sortable="custom" />
      <el-table-column label="状态" width="120" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="formatScanJobStatus(row.status)" :max-length="tableDisplayConfig.statusMaxLength" />
        </template>
      </el-table-column>
      <el-table-column prop="scanned_count" label="已扫描" width="120" align="center" header-align="center" sortable="custom" />
      <el-table-column prop="video_count" label="视频文件" width="120" align="center" header-align="center" sortable="custom" />
      <el-table-column prop="warning_count" label="警告" width="100" align="center" header-align="center" sortable="custom" />
      <el-table-column prop="batch_size" label="批大小" width="100" align="center" header-align="center" sortable="custom" />
      <el-table-column
        prop="batch_interval_seconds"
        label="批间隔"
        width="100"
        align="center"
        header-align="center"
        sortable="custom"
      />
      <el-table-column
        prop="started_at"
        label="开始时间"
        min-width="180"
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
        label="结束时间"
        min-width="180"
        align="center"
        header-align="center"
        sortable="custom"
      >
        <template #default="{ row }">
          {{ formatDateTime(row.ended_at) }}
        </template>
      </el-table-column>
    </el-table>

    <TablePagination pagination-key="scan-jobs" :total="mediaStore.scanJobs.length" />
  </section>
</template>
