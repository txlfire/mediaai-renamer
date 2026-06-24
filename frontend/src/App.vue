<script setup lang="ts">
/**
 * 根组件。
 *
 * 提供 M1 主界面框架：左侧可伸缩侧栏、左下状态区和右侧操作台。
 */

import {
  FolderOpened,
  Moon,
  Operation,
  Search,
  SwitchButton,
  Sunny,
  VideoCamera,
} from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted } from "vue";
import { useRoute } from "vue-router";

import LogDrawer from "./components/LogDrawer.vue";
import { useAppStore } from "./stores/app";

const appStore = useAppStore();
const route = useRoute();

const menuItems = [
  { path: "/media-sources", label: "媒体源", icon: FolderOpened },
  { path: "/scan-jobs", label: "扫描任务", icon: Operation },
  { path: "/scan-results", label: "扫描结果", icon: VideoCamera },
];

const isDarkTheme = computed(() => appStore.resolvedTheme === "dark");
const versionText = computed(() => `预览版 v${appStore.health?.version ?? "0.1.0"}`);

const connectionLabel = computed(() => {
  if (appStore.connectionState === "online") {
    return "后端连接正常";
  }

  if (appStore.connectionState === "loading") {
    return "正在检测后端";
  }

  return "后端连接异常";
});

function updateSystemTheme() {
  if (appStore.themeMode === "system") {
    appStore.applyThemeMode();
  }
}

function toggleLightDarkTheme() {
  appStore.setThemeMode(isDarkTheme.value ? "light" : "dark");
}

onMounted(() => {
  appStore.loadThemeMode();
  void appStore.refreshHealth();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateSystemTheme);
});

onUnmounted(() => {
  window.matchMedia("(prefers-color-scheme: dark)").removeEventListener("change", updateSystemTheme);
});
</script>

<template>
  <div class="app-layout" :class="{ 'is-collapsed': appStore.sidebarCollapsed }">
    <aside class="app-sidebar">
      <div class="brand-block">
        <div class="brand-logo">MR</div>
        <div v-if="!appStore.sidebarCollapsed" class="brand-copy">
          <strong>MediaAI Renamer</strong>
          <span>智能影视重命名工具</span>
        </div>
        <el-tooltip :content="appStore.sidebarCollapsed ? '展开菜单' : '收起菜单'" placement="right">
          <button
            type="button"
            class="collapse-button"
            :aria-label="appStore.sidebarCollapsed ? '展开菜单' : '收起菜单'"
            @click="appStore.toggleSidebar"
          >
            <span class="collapse-triangle" />
          </button>
        </el-tooltip>
      </div>

      <nav class="side-menu">
        <el-tooltip
          v-for="item in menuItems"
          :key="item.path"
          :content="item.label"
          :disabled="!appStore.sidebarCollapsed"
          placement="right"
        >
          <RouterLink :class="{ active: route.path === item.path }" :to="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span v-if="!appStore.sidebarCollapsed">{{ item.label }}</span>
          </RouterLink>
        </el-tooltip>
      </nav>

      <div class="sidebar-footer">
        <p v-if="!appStore.sidebarCollapsed" class="hint-text">先扫描目录，再生成命名预览</p>

        <div class="status-language-row">
          <el-tooltip :content="connectionLabel" :disabled="!appStore.sidebarCollapsed" placement="right">
            <div class="connection-state" :class="`is-${appStore.connectionState}`">
              <span class="status-dot" />
              <span v-if="!appStore.sidebarCollapsed">{{ connectionLabel }}</span>
            </div>
          </el-tooltip>
          <el-select
            v-if="!appStore.sidebarCollapsed"
            model-value="zh-CN"
            class="language-select"
            size="small"
            disabled
          >
            <el-option label="中文" value="zh-CN" />
          </el-select>
        </div>

        <div class="version-theme-row">
          <span v-if="!appStore.sidebarCollapsed" class="version-text">{{ versionText }}</span>
          <el-tooltip :content="isDarkTheme ? '切换亮色' : '切换暗色'" placement="top">
            <el-button class="theme-toggle" circle @click="toggleLightDarkTheme">
              <el-icon><component :is="isDarkTheme ? Sunny : Moon" /></el-icon>
            </el-button>
          </el-tooltip>
        </div>

        <el-button class="logout-button" :icon="SwitchButton" text>
          <template v-if="!appStore.sidebarCollapsed">
            <span class="logout-text">退出登录</span>
            <span class="user-name">admin</span>
          </template>
        </el-button>
      </div>
    </aside>

    <main class="app-workbench">
      <div class="workbench-topbar">
        <el-input class="global-search" placeholder="搜索媒体源、任务、结果" :prefix-icon="Search" clearable />
      </div>
      <RouterView />
    </main>

    <LogDrawer />
  </div>
</template>
