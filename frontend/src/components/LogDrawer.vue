<script setup lang="ts">
import { Download, Refresh } from "@element-plus/icons-vue";

import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";

const mediaStore = useMediaStore();

function exportLogs() {
  window.open("/api/logs/export", "_blank", "noopener,noreferrer");
}
</script>

<template>
  <el-drawer v-model="mediaStore.logDrawerVisible" :title="messages.logs.title" size="46%">
    <div class="drawer-toolbar">
      <el-button :icon="Refresh" @click="mediaStore.loadLogs">{{ messages.common.refresh }}</el-button>
      <el-button type="primary" :icon="Download" @click="exportLogs">{{ messages.logs.exportTxt }}</el-button>
    </div>

    <div class="log-list">
      <p v-if="mediaStore.logItems.length === 0" class="empty-text">{{ messages.common.emptyLogs }}</p>
      <div v-for="(item, index) in mediaStore.logItems" :key="`${item.file}-${index}`" class="log-line">
        <span>{{ item.file }}</span>
        <code>{{ item.message }}</code>
      </div>
    </div>
  </el-drawer>
</template>
