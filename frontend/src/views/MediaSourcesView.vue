<script setup lang="ts">
/**
 * 媒体源页面。
 *
 * 用于保存本地或已挂载媒体目录。
 */

import { FolderAdd, Refresh } from "@element-plus/icons-vue";
import { computed, onMounted, reactive } from "vue";

import TablePagination from "../components/TablePagination.vue";
import TextCell from "../components/TextCell.vue";
import { tableDisplayConfig } from "../config/tableDisplayConfig";
import { useMediaStore } from "../stores/media";
import { usePaginationStore } from "../stores/pagination";

const mediaStore = useMediaStore();
const paginationStore = usePaginationStore();
const form = reactive({
  name: "",
  path: "",
  enabled: true,
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
        <el-input v-model="form.path" placeholder="例如：D:\\Media\\Movies" />
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
  </section>
</template>
