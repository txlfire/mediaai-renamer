<script setup lang="ts">
/**
 * 首页视图。
 *
 * M0 阶段用于展示应用基础状态和后端连接结果，后续会扩展为扫描入口和任务概览。
 */

import { Connection, FolderOpened, Refresh, Setting } from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";

import { useAppStore } from "../stores/app";

const appStore = useAppStore();

// 将内部连接状态映射为 Element Plus 标签类型。
const statusType = computed(() => {
  if (appStore.connectionState === "online") {
    return "success";
  }

  if (appStore.connectionState === "loading") {
    return "warning";
  }

  return "danger";
});

// 将内部连接状态映射为用户可读的中文文案。
const statusLabel = computed(() => {
  if (appStore.connectionState === "online") {
    return "后端已连接";
  }

  if (appStore.connectionState === "loading") {
    return "正在检测";
  }

  return "后端未连接";
});

onMounted(() => {
  // 页面打开后立即检测后端连接，避免用户进入后看到空状态。
  void appStore.refreshHealth();
});
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <h1>MediaAI Renamer</h1>
        <p>智能影视重命名工具</p>
      </div>
      <el-button :icon="Refresh" :loading="appStore.connectionState === 'loading'" @click="appStore.refreshHealth">
        刷新
      </el-button>
    </header>

    <section class="status-panel">
      <div class="status-title">
        <el-icon><Connection /></el-icon>
        <span>服务状态</span>
      </div>
      <el-tag :type="statusType" effect="dark">{{ statusLabel }}</el-tag>
      <p v-if="appStore.health">
        {{ appStore.health.app }} v{{ appStore.health.version }}
      </p>
      <p v-else-if="appStore.errorMessage" class="error-text">
        {{ appStore.errorMessage }}
      </p>
    </section>

    <section class="workspace-grid">
      <article>
        <el-icon><FolderOpened /></el-icon>
        <h2>媒体目录</h2>
        <p>下一里程碑将接入目录保存、全量扫描和增量扫描。</p>
      </article>
      <article>
        <el-icon><Setting /></el-icon>
        <h2>命名规则</h2>
        <p>当前目标是输出符合 TMDB、Plex、Kodi、Emby 的标准文件名。</p>
      </article>
    </section>
  </main>
</template>
