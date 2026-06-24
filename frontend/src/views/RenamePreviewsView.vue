<script setup lang="ts">
import { Edit, MagicStick, Refresh, Search, Select } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { usePreviewStore } from "../stores/preview";
import { useRenameOperationStore } from "../stores/renameOperation";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime } from "../utils/displayFormat";

const mediaStore = useMediaStore();
const previewStore = usePreviewStore();
const renameOperationStore = useRenameOperationStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const route = useRoute();

const editDialogVisible = ref(false);
const operationDialogVisible = ref(false);
const editingPreviewId = ref<number | null>(null);
const editingTargetName = ref("");
const selectedPreviewIds = ref<number[]>([]);
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
    renamed: "已重命名",
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
  if (value === "renamed") {
    return "success";
  }
  if (value === "needs_review") {
    return "warning";
  }
  return "info";
}

function operationStatusLabel(value: string) {
  const labels: Record<string, string> = {
    ready: "可执行",
    conflict: "冲突",
    renamed: "已重命名",
    failed: "失败",
  };
  return labels[value] ?? value;
}

function operationStatusTagType(value: string) {
  if (value === "ready" || value === "renamed") {
    return "success";
  }
  if (value === "conflict") {
    return "warning";
  }
  if (value === "failed") {
    return "danger";
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
  if (!previewStore.filters.scan_job_id) {
    previewStore.previews = [];
    return;
  }
  await previewStore.loadPreviews(previewStore.filters);
}

async function generatePreviews() {
  if (!previewStore.filters.scan_job_id) {
    return;
  }
  await previewStore.generatePreviews({
    media_source_id: previewStore.filters.media_source_id,
    scan_job_id: previewStore.filters.scan_job_id,
  });
}

async function loadScanJobsForSelectedSource() {
  previewStore.filters.scan_job_id = undefined;
  previewStore.previews = [];
  if (!previewStore.filters.media_source_id) {
    mediaStore.scanJobs = [];
    return;
  }
  await mediaStore.loadScanJobs({ media_source_id: previewStore.filters.media_source_id });
}

function handleSelectionChange(rows: Array<{ id: number }>) {
  selectedPreviewIds.value = rows.map((row) => row.id);
}

async function runRenameDryRun() {
  if (selectedPreviewIds.value.length === 0) {
    return;
  }
  await renameOperationStore.runDryRun(selectedPreviewIds.value);
  operationDialogVisible.value = true;
}

async function executeRenameOperation() {
  await renameOperationStore.executeCurrentOperation();
  await refreshPreviews();
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
  await mediaStore.loadMediaSources();
  const routeSourceId = Number(route.query.media_source_id);
  const routeScanJobId = Number(route.query.scan_job_id);
  if (Number.isFinite(routeSourceId) && routeSourceId > 0) {
    previewStore.filters.media_source_id = routeSourceId;
    await mediaStore.loadScanJobs({ media_source_id: routeSourceId });
  }
  if (Number.isFinite(routeScanJobId) && routeScanJobId > 0) {
    previewStore.filters.scan_job_id = routeScanJobId;
    await refreshPreviews();
  }
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
      <div class="preview-filter-group">
      <el-select
        v-model="previewStore.filters.media_source_id"
        placeholder="媒体源"
        clearable
        class="filter-select"
        @change="loadScanJobsForSelectedSource"
        @clear="loadScanJobsForSelectedSource"
      >
        <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-model="previewStore.filters.scan_job_id"
        placeholder="扫描任务"
        clearable
        class="filter-select"
      >
        <el-option v-for="item in scanJobOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-model="previewStore.filters.status"
        placeholder="状态"
        clearable
        class="filter-select"
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
      />
      </div>
      <div class="preview-toolbar-actions">
        <el-button :icon="Search" :disabled="!previewStore.filters.scan_job_id" @click="refreshPreviews">
          查询
        </el-button>
        <el-button
          type="success"
          :icon="Select"
          :disabled="selectedPreviewIds.length === 0"
          :loading="renameOperationStore.loading"
          @click="runRenameDryRun"
        >
          冲突检测
        </el-button>
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
      <div class="stat-renamed">
        <span>已重命名</span>
        <strong>{{ previewStore.stats.renamed }}</strong>
      </div>
    </div>

    <el-alert v-if="previewStore.errorMessage" type="error" :title="previewStore.errorMessage" show-icon />

    <el-table
      :data="pagedPreviews"
      class="data-table"
      :default-sort="defaultSort"
      max-height="62vh"
      @selection-change="handleSelectionChange"
      @sort-change="handleSortChange"
    >
      <el-table-column type="selection" width="44" align="center" />
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

    <el-dialog v-model="operationDialogVisible" title="重命名冲突检测" width="860px">
      <div class="rename-operation-dialog">
        <el-alert
          v-if="renameOperationStore.errorMessage"
          :title="renameOperationStore.errorMessage"
          type="error"
          show-icon
        />
        <div v-if="renameOperationStore.currentOperation" class="preview-stats operation-stats">
          <div>
            <span>总数</span>
            <strong>{{ renameOperationStore.currentOperation.total_count }}</strong>
          </div>
          <div class="stat-generated">
            <span>可执行</span>
            <strong>{{ renameOperationStore.currentOperation.ready_count }}</strong>
          </div>
          <div class="stat-review">
            <span>冲突</span>
            <strong>{{ renameOperationStore.currentOperation.conflict_count }}</strong>
          </div>
          <div class="stat-edited">
            <span>已重命名</span>
            <strong>{{ renameOperationStore.currentOperation.renamed_count }}</strong>
          </div>
        </div>
        <el-table
          v-if="renameOperationStore.currentOperation"
          :data="renameOperationStore.currentOperation.items"
          class="data-table"
          max-height="420"
        >
          <el-table-column label="状态" width="110" align="center" header-align="center">
            <template #default="{ row }">
              <el-tag :type="operationStatusTagType(row.status)" effect="light">
                {{ operationStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="源路径" min-width="220" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.source_path" :max-length="tableDisplayConfig.pathMaxLength" />
            </template>
          </el-table-column>
          <el-table-column label="目标路径" min-width="220" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.target_path" :max-length="tableDisplayConfig.pathMaxLength" />
            </template>
          </el-table-column>
          <el-table-column label="原因" min-width="160" align="left" header-align="left">
            <template #default="{ row }">
              <TextCell :value="row.message || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="operationDialogVisible = false">关闭</el-button>
        <el-button
          type="danger"
          :disabled="!renameOperationStore.canExecute"
          :loading="renameOperationStore.loading"
          @click="executeRenameOperation"
        >
          确认重命名
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
