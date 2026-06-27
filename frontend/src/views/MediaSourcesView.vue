<script setup lang="ts">
import {
  Delete,
  Edit,
  FolderAdd,
  FolderOpened,
  InfoFilled,
  Link,
  List,
  Refresh,
  SwitchButton,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import {
  fetchLocalDirectories,
  testMediaSourceConnection,
  testMediaSourceConnectionPayload,
  type CleanupSummary,
  type LocalDirectoryListing,
  type MediaSource,
} from "../api/client";
import ListPageLayout from "../components/ListPageLayout.vue";
import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { canGoToParentDirectory, parentDirectoryPath } from "../utils/localDirectory";

const pageText = messages.mediaSources;

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const router = useRouter();
const form = reactive({
  name: "",
  path: "",
  path_type: "local" as "local" | "unc" | "mounted_nfs",
  username: "",
  secret: "",
  nfs_host: "",
  nfs_export: "",
  enabled: true,
});
const editForm = reactive({
  id: 0,
  name: "",
  path: "",
  originalPath: "",
  path_type: "local" as "local" | "unc" | "mounted_nfs",
  username: "",
  secret: "",
  nfs_host: "",
  nfs_export: "",
  has_secret: false,
  enabled: true,
});
const selectedRows = ref<MediaSource[]>([]);
const editDialogVisible = ref(false);
const pathPopoverVisible = ref(false);
const testingEditSource = ref(false);
const testingSourceIds = ref<Set<number>>(new Set());
const cleanupDialog = reactive({
  visible: false,
  percentage: 0,
  title: pageText.cleanupTitle,
  message: "",
  summary: null as CleanupSummary | null,
});
const directoryTarget = ref<"create" | "edit">("create");
const directoryPicker = reactive({
  visible: false,
  loading: false,
  errorMessage: "",
  selectedPath: "",
  listing: {
    current_path: null,
    parent_path: null,
    entries: [],
  } as LocalDirectoryListing,
});
const pagedMediaSources = computed(() =>
  paginationStore.paginate("media-sources", mediaStore.mediaSources),
);
const selectedIds = computed(() => selectedRows.value.map((row) => row.id));
const pathTypeNotice = computed(() => {
  if (form.path_type === "unc") {
    return pageText.smbNotice;
  }
  if (form.path_type === "mounted_nfs") {
    return pageText.nfsNotice;
  }
  return "";
});

function showPathPopoverIfNeeded() {
  pathPopoverVisible.value = form.path_type !== "local";
}

function connectionErrorMessage(message: string, suggestion?: string | null) {
  const detail = suggestion ? `${message}：${suggestion}` : message;
  if (detail.includes("路径不存在") || detail.includes("目录不存在")) {
    return `${detail}。${pageText.pathNotFoundSuggestion}`;
  }
  return detail;
}

watch(
  () => form.path_type,
  (pathType) => {
    if (pathType !== "unc") {
      form.username = "";
      form.secret = "";
    }
    if (pathType !== "mounted_nfs") {
      form.nfs_host = "";
      form.nfs_export = "";
    }
    pathPopoverVisible.value = pathType !== "local";
  },
);

async function submitSource() {
  if (!form.name.trim()) {
    ElMessage.warning(`${pageText.name}${messages.validation.requiredSuffix}`);
    return;
  }
  if (!form.path.trim()) {
    ElMessage.warning(`${pageText.targetPath}${messages.validation.requiredSuffix}`);
    return;
  }
  if (form.path_type === "unc" && !form.path.trim().startsWith("\\\\")) {
    showPathPopoverIfNeeded();
    ElMessage.warning(pageText.uncPathRequired);
    return;
  }
  if (form.path_type === "unc" && !form.username.trim()) {
    showPathPopoverIfNeeded();
    ElMessage.warning(`${pageText.username}${messages.validation.requiredSuffix}`);
    return;
  }
  if (form.path_type === "mounted_nfs" && !form.nfs_host.trim()) {
    showPathPopoverIfNeeded();
    ElMessage.warning(`${pageText.nfsHost}${messages.validation.requiredSuffix}`);
    return;
  }
  if (form.path_type === "mounted_nfs" && !form.nfs_export.trim()) {
    showPathPopoverIfNeeded();
    ElMessage.warning(`${pageText.nfsExport}${messages.validation.requiredSuffix}`);
    return;
  }

  try {
    await mediaStore.addMediaSource({
      name: form.name,
      path: form.path,
      enabled: form.enabled,
      path_type: form.path_type,
      username: form.path_type === "unc" ? form.username : null,
      secret: form.path_type === "unc" ? form.secret : null,
      nfs_host: form.path_type === "mounted_nfs" ? form.nfs_host : null,
      nfs_export: form.path_type === "mounted_nfs" ? form.nfs_export : null,
    });
    ElMessage.success(pageText.saved);
    resetFormOnly();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : pageText.saveFailed);
  }
}

function resetFormOnly() {
  form.name = "";
  form.path = "";
  form.path_type = "local";
  form.username = "";
  form.secret = "";
  form.nfs_host = "";
  form.nfs_export = "";
  form.enabled = true;
  pathPopoverVisible.value = false;
}

function protocolLabel(row: MediaSource) {
  if (row.path_type === "unc") {
    return pageText.pathTypes.unc;
  }
  if (row.path_type === "mounted_nfs") {
    return pageText.pathTypes.mountedNfs;
  }
  return pageText.pathTypes.local;
}

function editPathTypeLabel() {
  if (editForm.path_type === "unc") {
    return pageText.pathTypes.unc;
  }
  if (editForm.path_type === "mounted_nfs") {
    return pageText.pathTypes.mountedNfs;
  }
  return pageText.pathTypes.local;
}

async function resetMediaSources() {
  resetFormOnly();
  selectedRows.value = [];
  await mediaStore.loadMediaSources();
}

async function loadDirectories(path = "") {
  directoryPicker.loading = true;
  directoryPicker.errorMessage = "";
  try {
    directoryPicker.listing = await fetchLocalDirectories(path);
    directoryPicker.selectedPath = directoryPicker.listing.current_path ?? "";
  } catch (error) {
    directoryPicker.errorMessage = error instanceof Error ? error.message : messages.mediaSources.loadDirectoryFailed;
  } finally {
    directoryPicker.loading = false;
  }
}

async function openDirectoryPicker(target: "create" | "edit" = "create") {
  directoryTarget.value = target;
  directoryPicker.visible = true;
  await loadDirectories(target === "create" ? form.path : editForm.path);
}

async function enterDirectory(path: string) {
  await loadDirectories(path);
}

function confirmDirectorySelection() {
  if (directoryPicker.selectedPath) {
    if (directoryTarget.value === "create") {
      form.path = directoryPicker.selectedPath;
    } else {
      editForm.path = directoryPicker.selectedPath;
    }
  }
  directoryPicker.visible = false;
}

function viewScanJobs(row: MediaSource) {
  if (!row.enabled) {
    ElMessage.warning(pageText.disabledScanTip);
    return;
  }
  void router.push({ name: "scan-jobs", query: { media_source_id: String(row.id) } });
}

async function testConnection(row: MediaSource) {
  testingSourceIds.value = new Set(testingSourceIds.value).add(row.id);
  try {
    const result = await testMediaSourceConnection(row.id);
    if (result.success) {
      ElMessage.success(result.message || pageText.connectionSuccess);
      return;
    }
    ElMessage.error(connectionErrorMessage(result.message, result.suggestion));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : pageText.connectionFailed);
  } finally {
    const next = new Set(testingSourceIds.value);
    next.delete(row.id);
    testingSourceIds.value = next;
  }
}

async function testEditConnection() {
  if (!editForm.path.trim()) {
    ElMessage.warning(`${pageText.targetPath}${messages.validation.requiredSuffix}`);
    return;
  }
  if (editForm.path_type === "unc" && !editForm.path.trim().startsWith("\\\\")) {
    ElMessage.warning(pageText.uncPathRequired);
    return;
  }
  testingEditSource.value = true;
  try {
    const result = await testMediaSourceConnectionPayload({
      path: editForm.path,
      path_type: editForm.path_type,
    });
    if (result.success) {
      ElMessage.success(result.message || pageText.connectionSuccess);
      return;
    }
    ElMessage.error(connectionErrorMessage(result.message, result.suggestion));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : pageText.connectionFailed);
  } finally {
    testingEditSource.value = false;
  }
}

function openEditDialog(row: MediaSource) {
  editForm.id = row.id;
  editForm.name = row.name;
  editForm.path = row.path;
  editForm.originalPath = row.path;
  editForm.path_type = row.path_type as "local" | "unc" | "mounted_nfs";
  editForm.username = row.username ?? "";
  editForm.secret = "";
  editForm.nfs_host = row.nfs_host ?? "";
  editForm.nfs_export = row.nfs_export ?? "";
  editForm.has_secret = Boolean(row.has_secret);
  editForm.enabled = row.enabled;
  editDialogVisible.value = true;
}

async function showCleanupProgress<T>(work: () => Promise<T>): Promise<T> {
  cleanupDialog.visible = true;
  cleanupDialog.percentage = 15;
  cleanupDialog.message = pageText.cleanupPreparing;
  cleanupDialog.summary = null;
  await new Promise((resolve) => window.setTimeout(resolve, 120));
  cleanupDialog.percentage = 55;
  cleanupDialog.message = pageText.cleanupRunning;
  const result = await work();
  cleanupDialog.percentage = 90;
  cleanupDialog.message = pageText.cleanupFinished;
  await new Promise((resolve) => window.setTimeout(resolve, 160));
  cleanupDialog.percentage = 100;
  return result;
}

function setCleanupSummary(summary?: CleanupSummary) {
  cleanupDialog.summary = summary ?? null;
}

async function submitEditSource() {
  if (!editForm.name.trim()) {
    ElMessage.warning(`${pageText.name}${messages.validation.requiredSuffix}`);
    return;
  }
  if (!editForm.path.trim()) {
    ElMessage.warning(`${pageText.targetPath}${messages.validation.requiredSuffix}`);
    return;
  }
  if (editForm.path_type === "unc" && !editForm.path.trim().startsWith("\\\\")) {
    ElMessage.warning(pageText.uncPathRequired);
    return;
  }
  if (editForm.path_type === "unc" && !editForm.username.trim()) {
    ElMessage.warning(`${pageText.username}${messages.validation.requiredSuffix}`);
    return;
  }
  if (editForm.path_type === "mounted_nfs" && !editForm.nfs_host.trim()) {
    ElMessage.warning(`${pageText.nfsHost}${messages.validation.requiredSuffix}`);
    return;
  }
  if (editForm.path_type === "mounted_nfs" && !editForm.nfs_export.trim()) {
    ElMessage.warning(`${pageText.nfsExport}${messages.validation.requiredSuffix}`);
    return;
  }

  const pathChanged = editForm.path !== editForm.originalPath;
  if (pathChanged) {
    await ElMessageBox.confirm(
      pageText.cleanupConfirm,
      pageText.cleanupConfirmTitle,
      {
        type: "warning",
        confirmButtonText: messages.common.save,
        cancelButtonText: messages.common.cancel,
      },
    );
  }

  try {
    const result = pathChanged
      ? await showCleanupProgress(() =>
          mediaStore.editMediaSource(editForm.id, {
            name: editForm.name,
            path: editForm.path,
            enabled: editForm.enabled,
            username: editForm.path_type === "unc" ? editForm.username : null,
            secret: editForm.path_type === "unc" ? editForm.secret || null : null,
            nfs_host: editForm.path_type === "mounted_nfs" ? editForm.nfs_host : null,
            nfs_export: editForm.path_type === "mounted_nfs" ? editForm.nfs_export : null,
            clear_history_on_path_change: true,
          }),
        )
      : await mediaStore.editMediaSource(editForm.id, {
          name: editForm.name,
          path: editForm.path,
          enabled: editForm.enabled,
          username: editForm.path_type === "unc" ? editForm.username : null,
          secret: editForm.path_type === "unc" ? editForm.secret || null : null,
          nfs_host: editForm.path_type === "mounted_nfs" ? editForm.nfs_host : null,
          nfs_export: editForm.path_type === "mounted_nfs" ? editForm.nfs_export : null,
        });
    setCleanupSummary(result.cleanup_summary);
    editDialogVisible.value = false;
    ElMessage.success(pageText.saved);
  } finally {
    window.setTimeout(() => {
      cleanupDialog.visible = false;
    }, 400);
  }
}

async function toggleSource(row: MediaSource) {
  await mediaStore.toggleMediaSource(row.id, !row.enabled);
  ElMessage.success(pageText.statusUpdated);
}

async function removeSource(row: MediaSource) {
  await ElMessageBox.confirm(pageText.deleteConfirm, pageText.deleteConfirmTitle, {
    type: "warning",
    confirmButtonText: pageText.remove,
    cancelButtonText: messages.common.cancel,
  });
  const result = await showCleanupProgress(() => mediaStore.removeMediaSource(row.id));
  setCleanupSummary(result.cleanup_summary);
  ElMessage.success(pageText.deleted);
  window.setTimeout(() => {
    cleanupDialog.visible = false;
  }, 400);
}

async function removeSelectedSources() {
  if (!selectedIds.value.length) {
    ElMessage.warning(pageText.selectBeforeBatch);
    return;
  }

  await ElMessageBox.confirm(pageText.batchDeleteConfirm, pageText.deleteConfirmTitle, {
    type: "warning",
    confirmButtonText: pageText.batchRemove,
    cancelButtonText: messages.common.cancel,
  });
  const result = await showCleanupProgress(() => mediaStore.removeMediaSources(selectedIds.value));
  setCleanupSummary(result.cleanup_summary);
  selectedRows.value = [];
  ElMessage.success(pageText.deleted);
  window.setTimeout(() => {
    cleanupDialog.visible = false;
  }, 400);
}

function handleSelectionChange(rows: MediaSource[]) {
  selectedRows.value = rows;
}

onMounted(() => {
  void mediaStore.loadMediaSources();
});
</script>

<template>
  <ListPageLayout
    :title="messages.mediaSources.title"
    :description="messages.mediaSources.description"
  >
    <template #filters>
      <el-form class="source-form source-form-inline source-form-managed media-source-path-panel" :model="form" label-position="top">
        <div class="media-source-path-grid" :class="{ 'has-extra': form.path_type !== 'local' }">
          <div class="media-source-path-header">
            <div class="media-source-path-main">
              <el-form-item :label="messages.mediaSources.name" required>
                <el-input v-model="form.name" :placeholder="messages.mediaSources.namePlaceholder" />
              </el-form-item>
              <el-form-item class="media-source-path-type-item" :label="messages.mediaSources.pathType">
                <div class="media-source-path-type-control">
                  <el-select v-model="form.path_type">
                    <el-option :label="pageText.pathTypes.local" value="local" />
                    <el-option :label="pageText.pathTypes.unc" value="unc" />
                    <el-option :label="pageText.pathTypes.mountedNfs" value="mounted_nfs" />
                  </el-select>
                  <el-popover
                    v-if="form.path_type !== 'local'"
                    v-model:visible="pathPopoverVisible"
                    popper-class="media-source-path-popover"
                    placement="right-start"
                    trigger="click"
                    :width="360"
                  >
                    <template #reference>
                      <span class="media-source-path-type-help">
                        <el-icon><InfoFilled /></el-icon>
                      </span>
                    </template>
                    <div class="media-source-floating-form">
                      <template v-if="form.path_type === 'unc'">
                        <p>{{ pageText.smbNotice }}</p>
                        <el-form-item :label="pageText.username">
                          <el-input v-model="form.username" :placeholder="pageText.usernamePlaceholder" />
                        </el-form-item>
                        <el-form-item :label="pageText.password">
                          <el-input
                            v-model="form.secret"
                            type="password"
                            show-password
                            :placeholder="pageText.passwordPlaceholder"
                          />
                        </el-form-item>
                      </template>
                      <template v-if="form.path_type === 'mounted_nfs'">
                        <p>{{ pageText.nfsNotice }}</p>
                        <el-form-item :label="pageText.nfsHost">
                          <el-input v-model="form.nfs_host" :placeholder="pageText.nfsHostPlaceholder" />
                        </el-form-item>
                        <el-form-item :label="pageText.nfsExport">
                          <el-input v-model="form.nfs_export" :placeholder="pageText.nfsExportPlaceholder" />
                        </el-form-item>
                      </template>
                    </div>
                  </el-popover>
                </div>
              </el-form-item>
              <el-form-item class="media-source-path-item" :label="messages.mediaSources.targetPath" required>
                <div class="media-source-path-control">
                  <el-input v-model="form.path" class="media-source-path-input" :placeholder="messages.mediaSources.pathPlaceholder" />
                  <el-button
                    v-if="form.path_type !== 'unc'"
                    class="media-source-path-picker"
                    :icon="FolderOpened"
                    @click="openDirectoryPicker('create')"
                  >
                    {{ messages.mediaSources.chooseDirectory }}
                  </el-button>
                  <el-tooltip
                    :content="pathTypeNotice"
                    placement="bottom-end"
                    :disabled="form.path_type === 'local'"
                  >
                    <span
                      class="media-source-path-help"
                      :class="{ 'is-hidden': form.path_type === 'local' }"
                      :aria-hidden="form.path_type === 'local'"
                    >
                      <el-icon><InfoFilled /></el-icon>
                    </span>
                  </el-tooltip>
                </div>
              </el-form-item>
            </div>
            <div class="source-form-actions media-source-path-actions">
              <el-form-item class="media-source-status-item" :label="messages.common.status">
                <el-switch v-model="form.enabled" :active-text="messages.status.enabled" :inactive-text="messages.status.disabled" />
              </el-form-item>
              <el-button type="primary" :icon="FolderAdd" @click="submitSource">
                {{ messages.mediaSources.save }}
              </el-button>
              <el-button @click="resetMediaSources">{{ messages.common.reset }}</el-button>
            </div>
          </div>

        </div>
      </el-form>
    </template>

    <template #actions>
      <div class="media-source-table-actions">
        <el-button :icon="Delete" :disabled="!selectedIds.length" @click="removeSelectedSources">
          {{ pageText.batchRemove }}
        </el-button>
        <el-button :icon="Refresh" @click="mediaStore.loadMediaSources">{{ messages.common.refresh }}</el-button>
      </div>
    </template>

    <template #table>
      <el-table
        :data="pagedMediaSources"
        class="data-table"
        table-layout="auto"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="44" align="center" />
        <el-table-column :label="messages.mediaSources.name" min-width="180" align="left" header-align="left">
          <template #default="{ row }">
            <button type="button" class="link-cell" @click="openEditDialog(row)">
              <TextCell :value="row.name" :max-length="tableDisplayConfig.mediaSourceNameMaxLength" />
            </button>
          </template>
        </el-table-column>
        <el-table-column :label="messages.mediaSources.targetPath" min-width="300" align="left" header-align="left">
          <template #default="{ row }">
            <TextCell :value="row.path" :max-length="tableDisplayConfig.pathMaxLength" />
          </template>
        </el-table-column>
        <el-table-column :label="messages.mediaSources.pathType" width="150" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag>{{ protocolLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.status" width="96" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? messages.status.enabled : messages.status.disabled }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="208" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-tooltip :content="pageText.testConnection" placement="top">
                <el-button
                  class="table-action-button action-view"
                  :icon="Link"
                  :loading="testingSourceIds.has(row.id)"
                  text
                  circle
                  @click="testConnection(row)"
                />
              </el-tooltip>
              <el-tooltip :content="messages.mediaSources.viewTasks" placement="top">
                <el-button
                  class="table-action-button action-view"
                  :disabled="!row.enabled"
                  :icon="List"
                  text
                  circle
                  @click="viewScanJobs(row)"
                />
              </el-tooltip>
              <el-tooltip :content="pageText.edit" placement="top">
                <el-button
                  class="table-action-button action-edit"
                  :icon="Edit"
                  text
                  circle
                  @click="openEditDialog(row)"
                />
              </el-tooltip>
              <el-tooltip :content="row.enabled ? pageText.disable : pageText.enable" placement="top">
                <el-button
                  class="table-action-button action-toggle"
                  :icon="SwitchButton"
                  text
                  circle
                  @click="toggleSource(row)"
                />
              </el-tooltip>
              <el-tooltip :content="pageText.remove" placement="top">
                <el-button
                  class="table-action-button action-delete"
                  :icon="Delete"
                  text
                  circle
                  @click="removeSource(row)"
                />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template #pagination>
      <TablePagination pagination-key="media-sources" :total="mediaStore.mediaSources.length" />
    </template>

    <el-dialog v-model="editDialogVisible" :title="pageText.editTitle" width="640px">
      <el-form class="dialog-form" :model="editForm" label-position="top">
        <el-form-item :label="messages.mediaSources.name" required>
          <el-input v-model="editForm.name" :placeholder="messages.mediaSources.namePlaceholder" />
        </el-form-item>
        <el-form-item :label="messages.mediaSources.pathType">
          <el-input :model-value="editPathTypeLabel()" disabled :title="pageText.readOnlyPathTypeTip" />
        </el-form-item>
        <el-form-item :label="messages.mediaSources.targetPath" required>
          <div class="media-source-path-control">
            <el-input v-model="editForm.path" class="media-source-path-input" :placeholder="messages.mediaSources.pathPlaceholder" />
            <el-button
              v-if="editForm.path_type !== 'unc'"
              class="media-source-path-picker"
              :icon="FolderOpened"
              @click="openDirectoryPicker('edit')"
            >
              {{ messages.mediaSources.chooseDirectory }}
            </el-button>
          </div>
        </el-form-item>
        <template v-if="editForm.path_type === 'unc'">
          <el-form-item :label="pageText.username">
            <el-input v-model="editForm.username" :placeholder="pageText.usernamePlaceholder" />
          </el-form-item>
          <el-form-item :label="pageText.password">
            <el-input
              v-model="editForm.secret"
              type="password"
              show-password
              :placeholder="editForm.has_secret ? pageText.passwordKeepHint : pageText.passwordPlaceholder"
            />
          </el-form-item>
        </template>
        <template v-if="editForm.path_type === 'mounted_nfs'">
          <el-form-item :label="pageText.nfsHost">
            <el-input v-model="editForm.nfs_host" :placeholder="pageText.nfsHostPlaceholder" />
          </el-form-item>
          <el-form-item :label="pageText.nfsExport">
            <el-input v-model="editForm.nfs_export" :placeholder="pageText.nfsExportPlaceholder" />
          </el-form-item>
        </template>
        <el-form-item :label="messages.common.status">
          <el-switch v-model="editForm.enabled" :active-text="messages.status.enabled" :inactive-text="messages.status.disabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">{{ messages.common.cancel }}</el-button>
        <el-button :icon="Link" :loading="testingEditSource" @click="testEditConnection">
          {{ pageText.testConnection }}
        </el-button>
        <el-button type="primary" @click="submitEditSource">{{ messages.common.save }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cleanupDialog.visible" :title="cleanupDialog.title" width="520px" :close-on-click-modal="false">
      <div class="cleanup-progress">
        <el-progress :percentage="cleanupDialog.percentage" />
        <p>{{ cleanupDialog.message }}</p>
        <dl v-if="cleanupDialog.summary" class="cleanup-summary">
          <div>
            <dt>{{ pageText.cleanupScanJobs }}</dt>
            <dd>{{ cleanupDialog.summary.scan_jobs }}</dd>
          </div>
          <div>
            <dt>{{ pageText.cleanupMediaFiles }}</dt>
            <dd>{{ cleanupDialog.summary.media_files }}</dd>
          </div>
          <div>
            <dt>{{ pageText.cleanupPreviews }}</dt>
            <dd>{{ cleanupDialog.summary.rename_previews }}</dd>
          </div>
        </dl>
      </div>
    </el-dialog>

    <el-dialog v-model="directoryPicker.visible" :title="messages.mediaSources.directoryDialogTitle" width="680px">
      <div class="directory-picker">
        <el-alert :title="messages.mediaSources.directoryTip" type="info" show-icon :closable="false" />
        <div class="directory-current">
          <span>{{ messages.mediaSources.currentPath }}</span>
          <strong>{{ directoryPicker.selectedPath || messages.mediaSources.thisPc }}</strong>
        </div>
        <div class="directory-toolbar">
          <el-button
            :disabled="!canGoToParentDirectory(directoryPicker.listing) || directoryPicker.loading"
            @click="enterDirectory(parentDirectoryPath(directoryPicker.listing))"
          >
            {{ messages.mediaSources.parent }}
          </el-button>
          <el-button :loading="directoryPicker.loading" @click="loadDirectories(directoryPicker.selectedPath)">
            {{ messages.common.refresh }}
          </el-button>
        </div>
        <el-alert
          v-if="directoryPicker.errorMessage"
          :title="directoryPicker.errorMessage"
          type="error"
          show-icon
        />
        <div v-loading="directoryPicker.loading" class="directory-list">
          <button
            v-for="entry in directoryPicker.listing.entries"
            :key="entry.path"
            type="button"
            class="directory-item"
            @click="enterDirectory(entry.path)"
          >
            <el-icon><FolderOpened /></el-icon>
            <span>{{ entry.name }}</span>
          </button>
          <p v-if="!directoryPicker.loading && directoryPicker.listing.entries.length === 0" class="empty-text">
            {{ messages.mediaSources.noSubDirectories }}
          </p>
        </div>
      </div>
      <template #footer>
        <el-button @click="directoryPicker.visible = false">{{ messages.common.cancel }}</el-button>
        <el-button type="primary" :disabled="!directoryPicker.selectedPath" @click="confirmDirectorySelection">
          {{ messages.mediaSources.useDirectory }}
        </el-button>
      </template>
    </el-dialog>
  </ListPageLayout>
</template>

<style scoped>
.media-source-path-panel {
  display: block;
  min-height: 6.75rem;
  padding: clamp(0.75rem, 1.4vw, 1rem);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--panel-bg);
  transition: padding 180ms ease, box-shadow 180ms ease;
}

.media-source-path-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: end;
  gap: clamp(0.75rem, 1.4vw, 1rem);
  min-width: 0;
}

.media-source-path-main {
  display: grid;
  grid-template-columns:
    minmax(11rem, 0.95fr)
    minmax(10rem, 0.8fr)
    minmax(16rem, 1.2fr);
  align-items: end;
  gap: clamp(0.75rem, 1.4vw, 1rem);
}

.media-source-path-type-control {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  gap: 0.375rem;
}

.media-source-path-actions {
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  gap: clamp(0.5rem, 0.8vw, 0.75rem);
  margin-left: 0;
  min-width: max-content;
  padding-top: 0;
  white-space: nowrap;
}

.media-source-status-item {
  flex: 0 0 auto;
  width: max-content;
}

.media-source-path-actions > .el-button {
  flex: 0 0 auto;
}

.media-source-path-actions > .el-button:first-of-type {
  min-width: 8.25rem;
  padding-inline: 0.75rem;
}

.media-source-path-panel :deep(.el-form-item) {
  min-width: 0;
  margin-bottom: 0;
}

.media-source-path-panel :deep(.el-form-item__label) {
  max-width: 100%;
}

.media-source-path-panel :deep(.el-input__wrapper),
.media-source-path-panel :deep(.el-select__wrapper) {
  border-radius: 1px;
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
}

.media-source-path-panel :deep(.el-input__inner) {
  padding-inline: 0.35rem;
}

.media-source-path-control {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  gap: 0.375rem;
  overflow: hidden;
}

.media-source-path-input {
  flex: 1 1 auto;
  min-width: 0;
}

.media-source-path-picker {
  flex: 0 0 auto;
  max-width: clamp(6.25rem, 8vw, 7.5rem);
  min-width: 0;
  padding-inline: 0.55rem;
  white-space: nowrap;
}

.media-source-path-picker :deep(.el-icon) {
  flex: 0 0 auto;
}

.media-source-path-picker :deep(span) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.media-source-path-help {
  display: inline-flex;
  flex: 0 0 1.5rem;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  color: var(--el-color-primary);
  cursor: help;
}

.media-source-path-help.is-hidden {
  visibility: hidden;
  pointer-events: none;
}

.media-source-path-type-help {
  display: inline-flex;
  flex: 0 0 1.5rem;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  color: var(--el-color-primary);
  cursor: help;
}

:global(.media-source-path-popover) {
  max-width: min(22.5rem, calc(100vw - 2rem));
}

:global(.media-source-path-popover .media-source-floating-form) {
  display: grid;
  gap: 0.75rem;
}

:global(.media-source-path-popover .media-source-floating-form p) {
  margin: 0;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  white-space: normal;
}

:global(.media-source-path-popover .media-source-floating-form .el-form-item) {
  margin-bottom: 0;
}

@media (max-width: 1280px) {
  .media-source-path-header {
    grid-template-columns: minmax(0, 1fr);
  }

  .media-source-path-actions {
    justify-content: flex-start;
    padding-top: 0;
  }

  .media-source-path-main {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .media-source-path-item {
    grid-column: 1 / -1;
  }
}

@media (max-width: 720px) {
  .media-source-path-main {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
