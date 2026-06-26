<script setup lang="ts">
import { Connection, FolderOpened, Refresh, Setting } from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";

import { zhCnMessages as messages } from "../locales/zh-CN";
import { useAppStore } from "../stores/app";

const appStore = useAppStore();

const statusType = computed(() => {
  if (appStore.connectionState === "online") {
    return "success";
  }

  if (appStore.connectionState === "loading") {
    return "warning";
  }

  return "danger";
});

const statusLabel = computed(() => {
  if (appStore.connectionState === "online") {
    return messages.home.connected;
  }

  if (appStore.connectionState === "loading") {
    return messages.home.checking;
  }

  return messages.home.disconnected;
});

onMounted(() => {
  void appStore.refreshHealth();
});
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <h1>{{ messages.app.name }}</h1>
        <p>{{ messages.app.tagline }}</p>
      </div>
      <el-button :icon="Refresh" :loading="appStore.connectionState === 'loading'" @click="appStore.refreshHealth">
        {{ messages.common.refresh }}
      </el-button>
    </header>

    <section class="status-panel">
      <div class="status-title">
        <el-icon><Connection /></el-icon>
        <span>{{ messages.home.serviceStatus }}</span>
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
        <h2>{{ messages.home.mediaDirectories }}</h2>
        <p>{{ messages.home.mediaDirectoriesDescription }}</p>
      </article>
      <article>
        <el-icon><Setting /></el-icon>
        <h2>{{ messages.home.namingRules }}</h2>
        <p>{{ messages.home.namingRulesDescription }}</p>
      </article>
    </section>
  </main>
</template>
