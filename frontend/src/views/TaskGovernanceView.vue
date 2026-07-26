<script setup lang="ts">
import { Box, Document, Notebook, Refresh, RefreshLeft, Search } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, h, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import {
  archiveTask,
  fetchRemoteOperations,
  fetchTasks,
  recoverRemoteOperation,
  type RemoteOperationItem,
  type RemoteOperationStatus,
  type RemoteOperationType,
  type TaskGovernanceItem,
} from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
import OperationLogDrawer from "../components/OperationLogDrawer.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { useTableSortStore } from "../stores/tableSort";
import { formatDateTime, formatScanJobStatus } from "../utils/displayFormat";

const router = useRouter();
const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const tableSortStore = useTableSortStore();
const pageText = messages.tasks;
const activeTab = ref<"tasks" | "webdav">("tasks");
const tasks = ref<TaskGovernanceItem[]>([]);
const loading = ref(false);
const remoteOperations = ref<RemoteOperationItem[]>([]);
const remoteTotal = ref(0);
const remoteLoading = ref(false);
const remoteLoaded = ref(false);
const recoveringIds = ref<Set<number>>(new Set());
const operationLogVisible = ref(false);
const selectedLogTaskType = ref<string | null>(null);
const selectedLogTaskId = ref<number | null>(null);
const defaultSort = { prop: "updated_at", order: "descending" as const };
const filters = reactive({
  taskType: "",
  status: "",
  mediaSourceId: undefined as number | undefined,
  dateRange: [] as string[],
  includeArchived: false,
});
const remoteFilters = reactive({
  mediaSourceId: undefined as number | undefined,
  operationType: "" as RemoteOperationType | "",
  status: "" as RemoteOperationStatus | "",
});

const pagedTasks = computed(() =>
  paginationStore.paginate("task-governance", tableSortStore.applySort("task-governance", tasks.value)),
);
const sourceOptions = computed(() =>
  mediaStore.mediaSources.map((source) => ({
    label: source.name,
    value: source.id,
  })),
);

function buildFilters() {
  return {
    task_type: filters.taskType || undefined,
    status: filters.status || undefined,
    media_source_id: filters.mediaSourceId,
    start_at: filters.dateRange?.[0],
    end_at: filters.dateRange?.[1],
    include_archived: filters.includeArchived || undefined,
    limit: 500,
  };
}

async function loadTasks() {
  loading.value = true;
  try {
    tasks.value = await fetchTasks(buildFilters());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : pageText.loadFailed);
  } finally {
    loading.value = false;
  }
}

async function loadRemoteOperations() {
  remoteLoading.value = true;
  const pagination = paginationStore.getState("webdav-recovery");
  try {
    const result = await fetchRemoteOperations({
      page: pagination.currentPage,
      page_size: pagination.pageSize,
      media_source_id: remoteFilters.mediaSourceId,
      operation_type: remoteFilters.operationType || undefined,
      status: remoteFilters.status || undefined,
    });
    remoteOperations.value = result.items;
    remoteTotal.value = result.total;
    remoteLoaded.value = true;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : pageText.webdav.loadFailed);
  } finally {
    remoteLoading.value = false;
  }
}

function resetFilters() {
  filters.taskType = "";
  filters.status = "";
  filters.mediaSourceId = undefined;
  filters.dateRange = [];
  filters.includeArchived = false;
  void loadTasks();
}

function queryRemoteOperations() {
  paginationStore.setPage("webdav-recovery", 1);
  void loadRemoteOperations();
}

function resetRemoteFilters() {
  remoteFilters.mediaSourceId = undefined;
  remoteFilters.operationType = "";
  remoteFilters.status = "";
  paginationStore.setPage("webdav-recovery", 1);
  void loadRemoteOperations();
}

function handleTabChange(tabName: string | number) {
  if (String(tabName) === "webdav" && !remoteLoaded.value) {
    void loadRemoteOperations();
  }
}

function taskTypeLabel(value: string) {
  return pageText.taskTypes[value as keyof typeof pageText.taskTypes] || value;
}

function statusLabel(row: TaskGovernanceItem) {
  if (row.task_type === "scan_job") {
    return formatScanJobStatus(row.status);
  }
  return pageText.statuses[row.status as keyof typeof pageText.statuses] || row.status;
}

function statusTagType(status: string) {
  if (["completed", "executed", "success"].includes(status)) {
    return "success";
  }
  if (["partial_completed", "partial_failed", "checked", "dry_run"].includes(status)) {
    return "warning";
  }
  if (["failed", "interrupted"].includes(status)) {
    return "danger";
  }
  return "info";
}

function remoteStatusLabel(status: RemoteOperationStatus) {
  return pageText.webdav.statuses[status] || status;
}

function remoteStatusTagType(status: RemoteOperationStatus) {
  if (status === "completed") {
    return "success";
  }
  if (status === "recovery_required") {
    return "danger";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "recovering") {
    return "warning";
  }
  return "info";
}

function remoteOperationTypeLabel(operationType: RemoteOperationType) {
  return pageText.webdav.operationTypes[operationType] || operationType;
}

function canRecoverRemoteOperation(row: RemoteOperationItem) {
  return row.status !== "completed";
}

function isRecovering(itemId: number) {
  return recoveringIds.value.has(itemId);
}

function setRecovering(itemId: number, recovering: boolean) {
  const nextIds = new Set(recoveringIds.value);
  if (recovering) {
    nextIds.add(itemId);
  } else {
    nextIds.delete(itemId);
  }
  recoveringIds.value = nextIds;
}

function showRemoteOperationDetail(row: RemoteOperationItem) {
  const detailRows = [
    [pageText.webdav.columns.operationId, String(row.id)],
    [pageText.webdav.columns.mediaSource, row.media_source_name || "-"],
    [pageText.webdav.columns.operationType, remoteOperationTypeLabel(row.operation_type)],
    [pageText.webdav.columns.sourcePath, row.source_path],
    [pageText.webdav.columns.targetPath, row.target_path || "-"],
    [messages.common.status, remoteStatusLabel(row.status)],
    [pageText.webdav.columns.errorMessage, row.error_message || "-"],
    [pageText.webdav.columns.updatedAt, formatDateTime(row.updated_at)],
  ];
  void ElMessageBox.alert(
    h(
      "div",
      { class: "remote-operation-detail" },
      detailRows.map(([label, value]) =>
        h("div", { class: "remote-operation-detail-row" }, [
          h("strong", `${label}：`),
          h("span", value),
        ]),
      ),
    ),
    pageText.webdav.detailTitle,
    { confirmButtonText: messages.common.confirm },
  );
}

async function handleRecoverRemoteOperation(row: RemoteOperationItem) {
  try {
    await ElMessageBox.confirm(
      pageText.webdav.recoverConfirmMessage,
      pageText.webdav.recoverConfirmTitle,
      { type: "warning" },
    );
    setRecovering(row.id, true);
    const result = await recoverRemoteOperation(row.id);
    const successMessage =
      pageText.webdav.recoverActions[result.action as keyof typeof pageText.webdav.recoverActions] ||
      result.message;
    ElMessage.success(successMessage);
    await loadRemoteOperations();
  } catch (error) {
    if (error === "cancel" || error === "close") {
      return;
    }
    ElMessage.error(error instanceof Error ? error.message : pageText.webdav.recoverFailed);
    await loadRemoteOperations();
  } finally {
    setRecovering(row.id, false);
  }
}

function handleSortChange(event: { prop: string; order: "ascending" | "descending" | null }) {
  tableSortStore.setSort("task-governance", event.prop, event.order);
}

function openTaskTarget(row: TaskGovernanceItem) {
  if (!row.target_route) {
    ElMessage.warning(pageText.noTarget);
    return;
  }
  const query = Object.fromEntries(Object.entries(row.target_query).filter(([, value]) => value));
  void router.push({ name: row.target_route, query });
}

function openTaskLog(row: TaskGovernanceItem) {
  selectedLogTaskType.value = row.log_task_type;
  selectedLogTaskId.value = row.log_task_id;
  operationLogVisible.value = true;
}

async function toggleTaskArchive(row: TaskGovernanceItem) {
  const nextArchived = !row.archived;
  try {
    await ElMessageBox.confirm(
      nextArchived ? pageText.archiveConfirmMessage : pageText.restoreConfirmMessage,
      pageText.archiveConfirmTitle,
      { type: nextArchived ? "warning" : "info" },
    );
    await archiveTask(row.task_type, row.task_id, {
      archived: nextArchived,
      reason: nextArchived ? pageText.archiveReason : undefined,
    });
    ElMessage.success(nextArchived ? pageText.archiveSuccess : pageText.restoreSuccess);
    await loadTasks();
  } catch (error) {
    if (error === "cancel" || error === "close") {
      return;
    }
    ElMessage.error(error instanceof Error ? error.message : pageText.archiveFailed);
  }
}

onMounted(async () => {
  await mediaStore.loadMediaSources();
  await loadTasks();
});
</script>

<template>
  <ListPageLayout :title="pageText.title" :description="pageText.description">
    <template #filters>
      <template v-if="activeTab === 'tasks'">
        <el-select v-model="filters.taskType" class="source-select" clearable :placeholder="pageText.filters.taskType">
          <el-option :label="pageText.taskTypes.scan_job" value="scan_job" />
          <el-option :label="pageText.taskTypes.rename_operation" value="rename_operation" />
          <el-option :label="pageText.taskTypes.rollback_plan" value="rollback_plan" />
        </el-select>
        <el-select v-model="filters.status" class="source-select" clearable :placeholder="pageText.filters.status">
          <el-option v-for="item in pageText.statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.mediaSourceId" class="source-select" clearable :placeholder="pageText.filters.mediaSource">
          <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-date-picker
          v-model="filters.dateRange"
          class="task-date-range"
          type="daterange"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          :start-placeholder="pageText.filters.startDate"
          :end-placeholder="pageText.filters.endDate"
          range-separator="-"
          clearable
        />
        <el-checkbox v-model="filters.includeArchived" @change="loadTasks">
          {{ pageText.filters.includeArchived }}
        </el-checkbox>
        <el-button class="query-action-button" :icon="Search" :loading="loading" @click="loadTasks">{{ messages.common.query }}</el-button>
        <el-button @click="resetFilters">{{ messages.common.reset }}</el-button>
      </template>
      <template v-else>
        <el-select
          v-model="remoteFilters.mediaSourceId"
          class="source-select"
          clearable
          :placeholder="pageText.webdav.filters.mediaSource"
        >
          <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select
          v-model="remoteFilters.operationType"
          class="source-select"
          clearable
          :placeholder="pageText.webdav.filters.operationType"
        >
          <el-option :label="pageText.webdav.operationTypes.rename" value="rename" />
          <el-option :label="pageText.webdav.operationTypes.rollback" value="rollback" />
        </el-select>
        <el-select
          v-model="remoteFilters.status"
          class="source-select"
          clearable
          :placeholder="pageText.webdav.filters.status"
        >
          <el-option
            v-for="item in pageText.webdav.statusOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-button
          class="query-action-button"
          :icon="Search"
          :loading="remoteLoading"
          @click="queryRemoteOperations"
        >
          {{ messages.common.query }}
        </el-button>
        <el-button @click="resetRemoteFilters">{{ messages.common.reset }}</el-button>
      </template>
    </template>

    <template #filterActions>
      <el-button
        :icon="Refresh"
        :loading="activeTab === 'tasks' ? loading : remoteLoading"
        @click="activeTab === 'tasks' ? loadTasks() : loadRemoteOperations()"
      >
        {{ messages.common.refresh }}
      </el-button>
    </template>

    <template #table>
      <div class="task-governance-table">
        <el-tabs v-model="activeTab" class="task-governance-tabs" @tab-change="handleTabChange">
          <el-tab-pane :label="pageText.tabs.tasks" name="tasks" />
          <el-tab-pane :label="pageText.tabs.webdav" name="webdav" />
        </el-tabs>
        <el-table
          v-if="activeTab === 'tasks'"
          :data="pagedTasks"
          class="data-table"
          height="100%"
          table-layout="auto"
          :default-sort="defaultSort"
          @sort-change="handleSortChange"
        >
        <el-table-column prop="task_id" :label="pageText.columns.taskId" width="96" align="center" header-align="center" sortable="custom" />
        <el-table-column :label="pageText.columns.taskType" width="120" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag effect="light">{{ taskTypeLabel(row.task_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.status" width="112" align="center" header-align="center" sortable="custom" prop="status">
          <template #default="{ row }">
            <div class="task-status-cell">
              <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row) }}</el-tag>
              <el-tag v-if="row.archived" type="info" effect="plain">{{ pageText.archivedTag }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="pageText.columns.title" min-width="170" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.title" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column :label="pageText.columns.mediaSource" min-width="140" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.media_source_name || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column prop="total_count" :label="messages.common.total" width="84" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="success_count" :label="pageText.columns.success" width="84" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="warning_count" :label="pageText.columns.warning" width="84" align="center" header-align="center" sortable="custom" />
        <el-table-column prop="failed_count" :label="pageText.columns.failed" width="84" align="center" header-align="center" sortable="custom" />
        <el-table-column :label="pageText.columns.summary" min-width="260" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.summary" :max-length="tableDisplayConfig.tableTextMaxBytes" />
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" :label="pageText.columns.updatedAt" width="168" align="center" header-align="center" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="168" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-tooltip :content="pageText.openTarget" placement="top">
                <el-button class="table-action-button action-view" :icon="Document" text circle @click="openTaskTarget(row)" />
              </el-tooltip>
              <el-tooltip :content="pageText.viewLog" placement="top">
                <el-button class="table-action-button action-view" :icon="Notebook" text circle @click="openTaskLog(row)" />
              </el-tooltip>
              <el-tooltip :content="row.archived ? pageText.restoreTask : pageText.archiveTask" placement="top">
                <el-button
                  :class="['table-action-button', row.archived ? 'action-sync' : 'action-edit']"
                  :icon="row.archived ? RefreshLeft : Box"
                  text
                  circle
                  @click="toggleTaskArchive(row)"
                />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        </el-table>
        <el-table
          v-else
          v-loading="remoteLoading"
          :data="remoteOperations"
          class="data-table"
          height="100%"
          table-layout="auto"
        >
          <el-table-column
            prop="id"
            :label="pageText.webdav.columns.operationId"
            width="96"
            align="center"
            header-align="center"
          />
          <el-table-column
            :label="pageText.webdav.columns.mediaSource"
            min-width="140"
            align="left"
            header-align="left"
          >
            <template #default="{ row }">
              <TextCell :value="row.media_source_name || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column
            :label="pageText.webdav.columns.operationType"
            width="112"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">
              <el-tag effect="light">{{ remoteOperationTypeLabel(row.operation_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="pageText.webdav.columns.sourcePath"
            min-width="240"
            align="left"
            header-align="left"
          >
            <template #default="{ row }">
              <TextCell :value="row.source_path" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column
            :label="pageText.webdav.columns.targetPath"
            min-width="240"
            align="left"
            header-align="left"
          >
            <template #default="{ row }">
              <TextCell :value="row.target_path || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column
            :label="messages.common.status"
            width="124"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">
              <el-tag :type="remoteStatusTagType(row.status)" effect="light">
                {{ remoteStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="pageText.webdav.columns.errorMessage"
            min-width="200"
            align="left"
            header-align="left"
          >
            <template #default="{ row }">
              <TextCell :value="row.error_message || '-'" :max-length="tableDisplayConfig.tableTextMaxBytes" />
            </template>
          </el-table-column>
          <el-table-column
            :label="pageText.webdav.columns.updatedAt"
            width="168"
            align="center"
            header-align="center"
          >
            <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column :label="messages.common.actions" width="112" align="center" header-align="center" fixed="right">
            <template #default="{ row }">
              <div class="table-actions">
                <el-tooltip :content="pageText.webdav.viewDetail" placement="top">
                  <el-button
                    class="table-action-button action-view"
                    :icon="Document"
                    text
                    circle
                    @click="showRemoteOperationDetail(row)"
                  />
                </el-tooltip>
                <el-tooltip v-if="canRecoverRemoteOperation(row)" :content="pageText.webdav.recover" placement="top">
                  <el-button
                    class="table-action-button action-sync"
                    :icon="RefreshLeft"
                    :loading="isRecovering(row.id)"
                    :disabled="isRecovering(row.id)"
                    text
                    circle
                    @click="handleRecoverRemoteOperation(row)"
                  />
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <template #pagination>
      <TablePagination
        v-if="activeTab === 'tasks'"
        pagination-key="task-governance"
        :total="tasks.length"
      />
      <TablePagination
        v-else
        pagination-key="webdav-recovery"
        :total="remoteTotal"
        :pager-count="3"
        server
        @page-change="loadRemoteOperations"
        @page-size-change="loadRemoteOperations"
      />
    </template>

    <OperationLogDrawer
      v-model:visible="operationLogVisible"
      :task-type="selectedLogTaskType"
      :task-id="selectedLogTaskId"
      :title="pageText.logTitle"
    />
  </ListPageLayout>
</template>

<style scoped>
.task-date-range {
  width: 280px;
  max-width: 100%;
}

.task-status-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  max-width: 100%;
}

.task-governance-table {
  display: grid;
  min-width: 0;
  min-height: 0;
  height: 100%;
  grid-template-rows: auto minmax(0, 1fr);
}

.task-governance-tabs {
  min-width: 0;
}

.task-governance-tabs :deep(.el-tabs__header) {
  margin: 0 0 0.5rem;
}

.task-governance-tabs :deep(.el-tabs__content) {
  display: none;
}

:global(.remote-operation-detail) {
  display: grid;
  gap: 0.6rem;
  text-align: left;
}

:global(.remote-operation-detail-row) {
  display: grid;
  grid-template-columns: 8rem minmax(0, 1fr);
  gap: 0.5rem;
  align-items: start;
}

:global(.remote-operation-detail-row span) {
  overflow-wrap: anywhere;
}
</style>
