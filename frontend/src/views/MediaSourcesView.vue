<script setup lang="ts">
/**
 * 媒体源页面。
 *
 * 用于保存本地或已挂载媒体目录。
 */

import { FolderAdd, FolderOpened, Refresh } from "@element-plus/icons-vue";
import { computed, onMounted, reactive } from "vue";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { fetchLocalDirectories, type LocalDirectoryListing } from "../api/client";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";
import { canGoToParentDirectory, parentDirectoryPath } from "../utils/localDirectory";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const form = reactive({
  name: "",
  path: "",
  enabled: true,
});
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

async function submitSource() {
  if (!form.name.trim() || !form.path.trim()) {
    return;
  }

  await mediaStore.addMediaSource({ ...form });
  form.name = "";
  form.path = "";
  form.enabled = true;
}

async function loadDirectories(path = "") {
  directoryPicker.loading = true;
  directoryPicker.errorMessage = "";
  try {
    directoryPicker.listing = await fetchLocalDirectories(path);
    directoryPicker.selectedPath = directoryPicker.listing.current_path ?? "";
  } catch (error) {
    directoryPicker.errorMessage = error instanceof Error ? error.message : "读取本地目录失败";
  } finally {
    directoryPicker.loading = false;
  }
}

async function openDirectoryPicker() {
  directoryPicker.visible = true;
  await loadDirectories(form.path);
}

async function enterDirectory(path: string) {
  await loadDirectories(path);
}

function confirmDirectorySelection() {
  if (directoryPicker.selectedPath) {
    form.path = directoryPicker.selectedPath;
  }
  directoryPicker.visible = false;
}

onMounted(() => {
  void mediaStore.loadMediaSources();
});
</script>

<template>
  <section class="workspace-page">
    <div class="page-header">
      <div>
        <h1>媒体源</h1>
        <p>保存本地或已挂载目录，后续扫描任务会从这里选择来源。</p>
      </div>
      <el-button :icon="Refresh" @click="mediaStore.loadMediaSources">刷新</el-button>
    </div>

    <el-form class="source-form" :model="form" label-position="top">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="例如：电影" />
      </el-form-item>
      <el-form-item label="目录路径" required>
        <el-input v-model="form.path" placeholder="例如：D:\\Media\\Movies 或 \\\\NAS\\Movies">
          <template #append>
            <el-button :icon="FolderOpened" @click="openDirectoryPicker">选择目录</el-button>
          </template>
        </el-input>
      </el-form-item>
      <el-form-item label="状态">
        <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
      </el-form-item>
      <el-button type="primary" :icon="FolderAdd" @click="submitSource">保存媒体源</el-button>
    </el-form>

    <el-table :data="pagedMediaSources" class="data-table">
      <el-table-column label="名称" width="220" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.name" :max-length="tableDisplayConfig.mediaSourceNameMaxLength" />
        </template>
      </el-table-column>
      <el-table-column label="路径" min-width="460" align="left" header-align="left">
        <template #default="{ row }">
          <TextCell :value="row.path" :max-length="tableDisplayConfig.pathMaxLength" />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120" align="left" header-align="left">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? "启用" : "停用" }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <TablePagination pagination-key="media-sources" :total="mediaStore.mediaSources.length" />

    <el-dialog v-model="directoryPicker.visible" title="选择本地目录" width="680px">
      <div class="directory-picker">
        <el-alert
          title="当前选择的是服务所在 Windows 主机可访问的目录；网络路径仍可直接输入。"
          type="info"
          show-icon
          :closable="false"
        />
        <div class="directory-current">
          <span>当前路径</span>
          <strong>{{ directoryPicker.selectedPath || "此电脑" }}</strong>
        </div>
        <div class="directory-toolbar">
          <el-button
            :disabled="!canGoToParentDirectory(directoryPicker.listing) || directoryPicker.loading"
            @click="enterDirectory(parentDirectoryPath(directoryPicker.listing))"
          >
            上一级
          </el-button>
          <el-button :loading="directoryPicker.loading" @click="loadDirectories(directoryPicker.selectedPath)">
            刷新
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
            没有可进入的子目录
          </p>
        </div>
      </div>
      <template #footer>
        <el-button @click="directoryPicker.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!directoryPicker.selectedPath" @click="confirmDirectorySelection">
          使用此目录
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
