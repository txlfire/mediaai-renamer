<script setup lang="ts">
import { Edit, MagicStick, Refresh } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { usePreviewStore } from "../stores/preview";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const previewStore = usePreviewStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();

const editDialogVisible = ref(false);
const editingPreviewId = ref<number | null>(null);
const editingTargetName = ref("");
const defaultSort = { prop: "updated_at", order: "descending" as const };

const pagedPreviews = computed(() =>
  paginationStore.paginate(
    "rename-previews",
    tableSortStore.applySort("rename-previews", previewStore.previews),
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

function mediaTypeLabel(value: string) {
  const labels: Record<string, string> = {
    movie: "电影",
    episode: "剧集",
    unknown: "待识别",
  };
  return labels[value] ?? value;
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    generated: "可使用",
    edited: "已编辑",
    needs_review: "需检查",
  };
  return labels[value] ?? value;
}

function statusTagType(value: string) {
  if (value === "generated") {
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

function seasonEpisode(row: { season: number | null; episode: number | null }) {
  if (!row.season && !row.episode) {
    return "-";
  }
  return `S${String(row.season ?? 1).padStart(2, "0")}E${String(row.episode ?? 0).padStart(2, "0")}`;
}

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("rename-previews", event.prop, event.order);
}

async function refreshPreviews() {
  await previewStore.loadPreviews(previewStore.filters);
}

async function generatePreviews() {
  await previewStore.generatePreviews({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
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
  await Promise.all([
    mediaStore.loadMediaSources(),
    mediaStore.loadScanJobs(),
    previewStore.loadPreviews(),
  ]);
});
</script>

<template>
  <section class="workspace-page">
    <div class="page-header">
      <div>
        <h1>命名预览</h1>
        <p>基于扫描结果生成标准文件名预览，可检查和编辑目标文件名。</p>
      </div>
    </div>

    <div class="preview-toolbar">
      <el-select
        v-model="previewStore.filters.media_source_id"
        placeholder="媒体源"
        clearable
        class="filter-select"
        @change="refreshPreviews"
        @clear="refreshPreviews"
      >
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-model="previewStore.filters.scan_job_id"
        placeholder="扫描任务"
        clearable
        class="filter-select"
        @change="refreshPreviews"
        @clear="refreshPreviews"
      >
        <el-option v-for="item in scanJobOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-model="previewStore.filters.status"
        placeholder="状态"
        clearable
        class="filter-select"
        @change="refreshPreviews"
        @clear="refreshPreviews"
      >
        <el-option label="可使用" value="generated" />
        <el-option label="已编辑" value="edited" />
        <el-option label="需检查" value="needs_review" />
      </el-select>
      <el-select
        v-model="previewStore.filters.media_type"
        placeholder="类型"
        clearable
        class="filter-select"
        @change="refreshPreviews"
        @clear="refreshPreviews"
      >
        <el-option label="电影" value="movie" />
        <el-option label="剧集" value="episode" />
        <el-option label="待识别" value="unknown" />
      </el-select>
      <el-input
        v-model="previewStore.filters.keyword"
        placeholder="搜索文件名或标题"
        clearable
        class="preview-keyword"
        @change="refreshPreviews"
        @clear="refreshPreviews"
      />
      <div class="preview-toolbar-actions">
        <el-button type="primary" :icon="MagicStick" :loading="previewStore.loading" @click="generatePreviews">
          生成预览
        </el-button>
        <el-button :icon="Refresh" @click="refreshPreviews">刷新</el-button>
      </div>
    </div>

    <div class="preview-stats">
      <div>
        <span>总数</span>
        <strong>{{ previewStore.stats.total }}</strong>
      </div>
      <div class="stat-generated">
        <span>可使用</span>
        <strong>{{ previewStore.stats.generated }}</strong>
      </div>
      <div class="stat-review">
        <span>需检查</span>
        <strong>{{ previewStore.stats.needsReview }}</strong>
      </div>
      <div class="stat-edited">
        <span>已编辑</span>
        <strong>{{ previewStore.stats.edited }}</strong>
      </div>
    </div>

    <el-alert v-if="previewStore.errorMessage" type="error" :title="previewStore.errorMessage" show-icon />

    <el-table
      :data="pagedPreviews"
      class="data-table"
      :default-sort="defaultSort"
      max-height="62vh"
      @sort-change="handleSortChange"
    >
      <el-table-column prop="status" label="状态" width="92" align="center" header-align="center" sortable="custom">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="file_name" label="原文件名" min-width="150" align="left" header-align="left" sortable="custom">
        <template #default="{ row }">
          <TextCell :value="row.file_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
        </template>
      </el-table-column>
      <el-table-column
        prop="current_target_name"
        label="目标文件名"
        min-width="150"
        align="left"
        header-align="left"
        sortable="custom"
      >
        <template #default="{ row }">
          <TextCell :value="row.current_target_name" :max-length="tableDisplayConfig.fileNameMaxLength" />
        </template>
      </el-table-column>
      <el-table-column label="解析标题" width="176" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.parsed_title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
        </template>
      </el-table-column>
      <el-table-column prop="media_type" label="类型" width="82" align="center" header-align="center" sortable="custom">
        <template #default="{ row }">{{ mediaTypeLabel(row.media_type) }}</template>
      </el-table-column>
      <el-table-column prop="parsed_year" label="年份" width="76" align="center" header-align="center" sortable="custom">
        <template #default="{ row }">{{ row.parsed_year ?? "-" }}</template>
      </el-table-column>
      <el-table-column label="季/集" width="82" align="center" header-align="center">
        <template #default="{ row }">{{ seasonEpisode(row) }}</template>
      </el-table-column>
      <el-table-column
        prop="updated_at"
        label="更新时间"
        width="168"
        class-name="nowrap-column"
        align="center"
        header-align="center"
        sortable="custom"
      >
        <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="62" align="center" header-align="center" fixed="right">
        <template #default="{ row }">
          <el-tooltip content="编辑" placement="top">
            <el-button class="table-action-button action-edit" :icon="Edit" text circle @click="openEditDialog(row)" />
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <TablePagination pagination-key="rename-previews" :total="previewStore.previews.length" />

    <el-dialog v-model="editDialogVisible" title="编辑目标文件名" width="520px">
      <el-input v-model="editingTargetName" placeholder="目标文件名" />
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>
