<script setup lang="ts">
import {
  Delete,
  Edit,
  FolderAdd,
  FolderOpened,
  List,
  Refresh,
  SwitchButton,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import {
  fetchLocalDirectories,
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
  enabled: true,
});
const editForm = reactive({
  id: 0,
  name: "",
  path: "",
  originalPath: "",
  enabled: true,
});
const selectedRows = ref<MediaSource[]>([]);
const editDialogVisible = ref(false);
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

async function submitSource() {
  if (!form.name.trim() || !form.path.trim()) {
    return;
  }

  await mediaStore.addMediaSource({ ...form });
  ElMessage.success(pageText.saved);
  resetFormOnly();
}

function resetFormOnly() {
  form.name = "";
  form.path = "";
  form.enabled = true;
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

function openEditDialog(row: MediaSource) {
  editForm.id = row.id;
  editForm.name = row.name;
  editForm.path = row.path;
  editForm.originalPath = row.path;
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
  if (!editForm.name.trim() || !editForm.path.trim()) {
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
            clear_history_on_path_change: true,
          }),
        )
      : await mediaStore.editMediaSource(editForm.id, {
          name: editForm.name,
          path: editForm.path,
          enabled: editForm.enabled,
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
      <el-form class="source-form source-form-inline source-form-managed" :model="form" label-position="top">
        <el-form-item :label="messages.mediaSources.name" required>
          <el-input v-model="form.name" :placeholder="messages.mediaSources.namePlaceholder" />
        </el-form-item>
        <el-form-item class="source-path-item" :label="messages.mediaSources.targetPath" required>
          <el-input v-model="form.path" :placeholder="messages.mediaSources.pathPlaceholder">
            <template #append>
              <el-button :icon="FolderOpened" @click="openDirectoryPicker('create')">
                {{ messages.mediaSources.chooseDirectory }}
              </el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item :label="messages.common.status">
          <el-switch v-model="form.enabled" :active-text="messages.status.enabled" :inactive-text="messages.status.disabled" />
        </el-form-item>
      </el-form>
    </template>

    <template #filterActions>
      <div class="source-form-actions">
        <el-button type="primary" :icon="FolderAdd" @click="submitSource">
          {{ messages.mediaSources.save }}
        </el-button>
        <el-button @click="resetMediaSources">{{ messages.common.reset }}</el-button>
      </div>
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
        <el-table-column :label="messages.common.status" width="96" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? messages.status.enabled : messages.status.disabled }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="messages.common.actions" width="172" align="center" header-align="center" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
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
        <el-form-item :label="messages.mediaSources.targetPath" required>
          <el-input v-model="editForm.path" :placeholder="messages.mediaSources.pathPlaceholder">
            <template #append>
              <el-button :icon="FolderOpened" @click="openDirectoryPicker('edit')">
                {{ messages.mediaSources.chooseDirectory }}
              </el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item :label="messages.common.status">
          <el-switch v-model="editForm.enabled" :active-text="messages.status.enabled" :inactive-text="messages.status.disabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">{{ messages.common.cancel }}</el-button>
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
