<script setup lang="ts">
/**
 * 操作日志抽屉。
 *
 * 用于弹出查看后端运行日志，并提供 TXT 导出入口。
 */

import { Download, Refresh } from "@element-plus/icons-vue";

import { useMediaStore } from "../stores/media";

const mediaStore = useMediaStore();

function exportLogs() {
  window.open("/api/logs/export", "_blank", "noopener,noreferrer");
}
</script>

<template>
  <el-drawer v-model="mediaStore.logDrawerVisible" title="操作日志" size="46%">
    <div class="drawer-toolbar">
      <el-button :icon="Refresh" @click="mediaStore.loadLogs">刷新</el-button>
      <el-button type="primary" :icon="Download" @click="exportLogs">导出 TXT</el-button>
    </div>

    <div class="log-list">
      <p v-if="mediaStore.logItems.length === 0" class="empty-text">暂无日志</p>
      <div v-for="(item, index) in mediaStore.logItems" :key="`${item.file}-${index}`" class="log-line">
        <span>{{ item.file }}</span>
        <code>{{ item.message }}</code>
      </div>
    </div>
  </el-drawer>
</template>
