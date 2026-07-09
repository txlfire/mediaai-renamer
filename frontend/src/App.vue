<script setup lang="ts">
import {
  EditPen,
  FolderOpened,
  Moon,
  Operation,
  Search,
  Setting,
  SwitchButton,
  Sunny,
  VideoCamera,
} from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";

import LogDrawer from "./components/LogDrawer.vue";
import { zhCnMessages as messages } from "./locales/zh-CN";
import { useAppStore } from "./stores/app";
import { useAuthStore } from "./stores/auth";

const appStore = useAppStore();
const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();

const menuItems = [
  { path: "/media-sources", label: messages.app.menu.mediaSources, icon: FolderOpened },
  { path: "/scan-jobs", label: messages.app.menu.scanJobs, icon: Operation },
  { path: "/scan-results", label: messages.app.menu.scanResults, icon: VideoCamera },
  { path: "/rename-previews", label: messages.app.menu.renamePreviews, icon: EditPen },
  { path: "/settings", label: messages.app.menu.settings, icon: Setting },
];

const isDarkTheme = computed(() => appStore.resolvedTheme === "dark");
const isPublicPage = computed(() => Boolean(route.meta.public));
const currentUserName = computed(() => authStore.currentUser?.displayName || authStore.currentUser?.username || "");
const versionText = computed(() => `${messages.app.previewVersion} v${appStore.health?.version ?? "0.1.0"}`);

const connectionLabel = computed(() => {
  if (appStore.connectionState === "online") {
    return messages.app.connection.online;
  }

  if (appStore.connectionState === "loading") {
    return messages.app.connection.loading;
  }

  return messages.app.connection.offline;
});

const sidebarToggleLabel = computed(() =>
  appStore.sidebarCollapsed ? messages.app.sidebar.expandTooltip : messages.app.sidebar.collapseTooltip,
);

const themeLabel = computed(() => (isDarkTheme.value ? messages.app.theme.toLight : messages.app.theme.toDark));

function updateSystemTheme() {
  if (appStore.themeMode === "system") {
    appStore.applyThemeMode();
  }
}

function toggleLightDarkTheme() {
  appStore.setThemeMode(isDarkTheme.value ? "light" : "dark");
}

async function logout() {
  await authStore.logout();
  await router.replace("/login");
}

function handleAuthExpired() {
  authStore.clearSession(messages.auth.sessionExpired);
  if (route.path !== "/login") {
    void router.replace({ path: "/login", query: { redirect: route.fullPath } });
  }
}

onMounted(() => {
  appStore.loadSidebarCollapsed();
  appStore.loadThemeMode();
  void appStore.refreshHealth();
  void authStore.loadStoredSession();
  window.addEventListener("mediaai:auth-expired", handleAuthExpired);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateSystemTheme);
});

onUnmounted(() => {
  window.removeEventListener("mediaai:auth-expired", handleAuthExpired);
  window.matchMedia("(prefers-color-scheme: dark)").removeEventListener("change", updateSystemTheme);
});
</script>

<template>
  <RouterView v-if="isPublicPage" />
  <div v-else class="app-layout" :class="{ 'is-collapsed': appStore.sidebarCollapsed }">
    <aside class="app-sidebar">
      <el-tooltip :content="sidebarToggleLabel" placement="right">
        <button type="button" class="brand-block" :aria-label="sidebarToggleLabel" @click="appStore.toggleSidebar">
          <div class="brand-logo">MR</div>
          <div v-if="!appStore.sidebarCollapsed" class="brand-copy">
            <strong>{{ messages.app.name }}</strong>
            <span>{{ messages.app.tagline }}</span>
          </div>
        </button>
      </el-tooltip>

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
        <p v-if="!appStore.sidebarCollapsed" class="hint-text">{{ messages.app.hint }}</p>

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
            <el-option :label="messages.app.languageName" value="zh-CN" />
          </el-select>
        </div>

        <div class="version-theme-row">
          <span v-if="!appStore.sidebarCollapsed" class="version-text">{{ versionText }}</span>
          <el-tooltip :content="themeLabel" placement="top">
            <el-button class="theme-toggle" circle @click="toggleLightDarkTheme">
              <el-icon><component :is="isDarkTheme ? Sunny : Moon" /></el-icon>
            </el-button>
          </el-tooltip>
        </div>

        <el-button class="logout-button" :icon="SwitchButton" text :loading="authStore.loading" @click="logout">
          <template v-if="!appStore.sidebarCollapsed">
            <span class="logout-text">{{ messages.app.logout }}</span>
            <span class="user-name">{{ currentUserName || messages.auth.loginRequired }}</span>
          </template>
        </el-button>
      </div>
    </aside>

    <main class="app-workbench">
      <div class="workbench-topbar">
        <el-input class="global-search" :placeholder="messages.app.globalSearchPlaceholder" :prefix-icon="Search" clearable />
      </div>
      <RouterView />
    </main>

    <LogDrawer />
  </div>
</template>
