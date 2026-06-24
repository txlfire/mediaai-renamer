<script setup lang="ts">
/**
 * 扫描结果页面。
 *
 * 展示 M1 识别到的视频文件，不提供预览和重命名操作。
 */

import { Refresh } from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";

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
const defaultSort = { prop: "modified_at", order: "descending" as const };
const pagedMediaFiles = computed(() =>
  paginationStore.paginate(
    "scan-results",
    tableSortStore.applySort("scan-results", mediaStore.mediaFiles),
  ),
);

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("scan-results", event.prop, event.order);
}

onMounted(() => {
  void mediaStore.loadMediaFiles();
});
</script>

<template>
  <section class="workspace-page">
    <div class="page-header">
      <div>
        <h1>扫描结果</h1>
        <p>查看当前已识别的视频文件列表。命名预览和重命名会在后续阶段加入。</p>
      </div>
      <el-button :icon="Refresh" @click="mediaStore.loadMediaFiles">刷新</el-button>
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
