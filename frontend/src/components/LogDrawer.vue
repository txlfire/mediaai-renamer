<script setup lang="ts">
import { isSessionChangedError } from "../utils/sessionGeneration";
import { Download, Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { ref } from "vue";
import { exportRuntimeLogs } from "../api/client";

import { zhCnMessages as messages } from "../locales/zh-CN";
import { useMediaStore } from "../stores/media";

const mediaStore = useMediaStore();
const exporting = ref(false);

async function exportLogs() {
  if (exporting.value) return;
  exporting.value = true;
  try {
    const text = await exportRuntimeLogs();
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "mediaai-runtime-logs.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    if (isSessionChangedError(error)) return;
    ElMessage.error(messages.logs.exportFailed);
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <el-drawer v-model="mediaStore.logDrawerVisible" :title="messages.logs.title" size="min(720px, calc(100vw - 24px))">
    <div class="drawer-toolbar">
      <el-button :icon="Refresh" @click="mediaStore.loadLogs">{{ messages.common.refresh }}</el-button>
      <el-button type="primary" :icon="Download" :loading="exporting" @click="exportLogs">{{ messages.logs.exportTxt }}</el-button>
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
