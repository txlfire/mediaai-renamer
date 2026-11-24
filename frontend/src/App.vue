<script setup lang="ts">
import {
  EditPen,
  FolderOpened,
  Notebook,
  Operation,
  Setting,
  SwitchButton,
  VideoCamera,
  Loading,
  Lock,
  FolderChecked,
} from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import LogDrawer from "./components/LogDrawer.vue";
import { useInterfaceLanguage } from "./locales/interfaceLanguage";
import { useAppStore } from "./stores/app";
import { useAuthStore } from "./stores/auth";
import { routeLoading } from "./router";

import BackgroundJobsButton from "./components/BackgroundJobsButton.vue";
import ThemePreferences from "./components/ThemePreferences.vue";
import CloudProtectionDialog from "./components/CloudProtectionDialog.vue";
import GlobalSearch from "./components/GlobalSearch.vue";
import InterfaceLanguageSelect from "./components/InterfaceLanguageSelect.vue";

const { messages } = useInterfaceLanguage();
const appStore = useAppStore();
const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();
const protectionVisible = ref(false);
const backgroundJobsControl = ref<{ openRequest: (requestId: string) => void }>();
const systemThemeQuery = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

const menuItems = computed(() =>
  [
    { path: "/media-sources", label: messages.value.app.menu.mediaSources, icon: FolderOpened, visible: true },
    { path: "/scan-jobs", label: messages.value.app.menu.scanJobs, icon: Operation, visible: true },
    { path: "/scan-results", label: messages.value.app.menu.scanResults, icon: VideoCamera, visible: true },
    { path: "/rename-previews", label: messages.value.app.menu.renamePreviews, icon: EditPen, visible: true },
    { path: "/cloud-inventory", label: messages.value.app.menu.cloudInventory, icon: FolderChecked, visible: authStore.hasPermission("scan:run") },
    { path: "/tasks", label: messages.value.app.menu.tasks, icon: Notebook, visible: authStore.hasPermission("task:manage") },
    { path: "/settings", label: messages.value.app.menu.settings, icon: Setting, visible: authStore.hasPermission("settings:write") },
  ].filter((item) => item.visible),
);

const isPublicPage = computed(() => route.matched.length === 0 || Boolean(route.meta.public));
const currentMenuItem = computed(() => menuItems.value.find(item => item.path === route.path));
const currentUserName = computed(() => authStore.currentUser?.displayName || authStore.currentUser?.username || "");
const versionText = computed(() => appStore.health?.version
  ? `v${appStore.health.version}` : messages.value.app.versionPending);

const connectionLabel = computed(() => {
  if (appStore.connectionState === "online") {
    return messages.value.app.connection.online;
  }

  if (appStore.connectionState === "loading") {
    return messages.value.app.connection.loading;
  }

  return messages.value.app.connection.offline;
});

const sidebarToggleLabel = computed(() =>
  appStore.sidebarCollapsed ? messages.value.app.sidebar.expandTooltip : messages.value.app.sidebar.collapseTooltip,
);

function updateSystemTheme() {
  if (appStore.themeMode === "system") {
    appStore.applyThemeMode();
  }
}

function updateSidebarViewport() {
  appStore.syncSidebarViewport(window.innerWidth < 1400);
}

async function logout() {
  await authStore.logout();
  await router.replace("/login");
}

function handleAuthExpired() {
  authStore.clearSession(messages.value.auth.sessionExpired);
  if (route.path !== "/login") {
    void router.replace({ path: "/login", query: { redirect: route.fullPath } });
  }
}

function handleAuditPending() {
  ElMessage.warning({ message: messages.value.app.auditPending, grouping: true, duration: 6000 });
}

onMounted(() => {
  appStore.loadSidebarCollapsed();
  updateSidebarViewport();
  appStore.loadThemeMode();
  void appStore.refreshHealth();
  void authStore.loadStoredSession();
  window.addEventListener("mediaai:auth-expired", handleAuthExpired);
  window.addEventListener("mediaai:audit-pending", handleAuditPending);
  systemThemeQuery?.addEventListener("change", updateSystemTheme);
  window.addEventListener("resize", updateSidebarViewport);
});

onUnmounted(() => {
  window.removeEventListener("mediaai:auth-expired", handleAuthExpired);
  window.removeEventListener("mediaai:audit-pending", handleAuditPending);
  systemThemeQuery?.removeEventListener("change", updateSystemTheme);
  window.removeEventListener("resize", updateSidebarViewport);
});
</script>

<template>
  <div v-if="routeLoading" class="route-loading-indicator" role="status" :aria-label="messages.app.pageLoading">
    <el-icon class="is-loading"><Loading /></el-icon>
  </div>
  <RouterView v-if="isPublicPage" />
  <div v-else class="app-layout" :class="{ 'is-collapsed': appStore.sidebarCollapsed }">
    <aside class="app-sidebar">
      <el-tooltip :trigger-keys="[]" :content="sidebarToggleLabel" placement="right">
        <button type="button" class="brand-block" :aria-label="sidebarToggleLabel" @click="appStore.toggleSidebar">
          <div class="brand-logo">MR</div>
          <div v-if="!appStore.sidebarCollapsed" class="brand-copy">
            <strong>{{ messages.app.name }}</strong>
            <span>{{ messages.app.tagline }}</span>
          </div>
        </button>
      </el-tooltip>

      <nav class="side-menu">
        <el-tooltip :trigger-keys="[]"
          v-for="item in menuItems"
          :key="item.path"
          :content="item.label"
          :disabled="!appStore.sidebarCollapsed"
          placement="right"
        >
          <RouterLink :class="{ active: route.path === item.path }" :to="item.path" :aria-label="item.label" :aria-current="route.path === item.path ? 'page' : undefined">
            <el-icon><component :is="item.icon" /></el-icon>
            <span v-if="!appStore.sidebarCollapsed">{{ item.label }}</span>
          </RouterLink>
        </el-tooltip>
      </nav>

      <div class="sidebar-footer">
        <p v-if="!appStore.sidebarCollapsed" class="hint-text">{{ messages.app.hint }}</p>

        <div class="status-language-row">
          <el-tooltip :trigger-keys="[]" :content="connectionLabel" :disabled="!appStore.sidebarCollapsed" placement="right">
            <div class="connection-state" :class="`is-${appStore.connectionState}`">
              <span class="status-dot" />
              <span v-if="!appStore.sidebarCollapsed">{{ connectionLabel }}</span>
            </div>
          </el-tooltip>
          <InterfaceLanguageSelect
            v-if="!appStore.sidebarCollapsed"
            class="language-select"
            show-single
          />
        </div>

        <div class="version-theme-row">
          <span v-if="!appStore.sidebarCollapsed" class="version-text">{{ versionText }}</span>
          <ThemePreferences />
        </div>

        <el-button class="logout-button" :icon="SwitchButton" :aria-label="messages.app.logout" text :loading="authStore.loading" @click="logout">
          <template v-if="!appStore.sidebarCollapsed">
            <span class="logout-text">{{ messages.app.logout }}</span>
            <span class="user-name">{{ currentUserName || messages.auth.loginRequired }}</span>
          </template>
        </el-button>
      </div>
    </aside>

    <main class="app-workbench" :aria-busy="routeLoading">
      <div class="workbench-topbar">
        <div v-if="currentMenuItem" class="workbench-location">
          <el-icon><component :is="currentMenuItem.icon" /></el-icon>
          <span>{{ currentMenuItem.label }}</span>
        </div>
        <GlobalSearch v-if="authStore.currentUser" @open-background="backgroundJobsControl?.openRequest($event)" />
        <el-button v-if="authStore.currentUser" :icon="Lock" @click="protectionVisible = true">{{ messages.cloudProtections.open }}</el-button>
        <BackgroundJobsButton ref="backgroundJobsControl" />
      </div>
      <RouterView />
      <div class="workbench-fullscreen-host" />
    </main>

    <LogDrawer />
    <CloudProtectionDialog v-if="protectionVisible" v-model:visible="protectionVisible" />
  </div>
</template>

<style scoped>
.workbench-fullscreen-host {
  display: contents;
}

.route-loading-indicator {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2100;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--el-color-primary);
  background: var(--el-bg-color);
  border-radius: 6px;
  pointer-events: none;
}
@media (prefers-reduced-motion: reduce) {
  .route-loading-indicator :deep(.is-loading) {
    animation: none;
  }
}
</style>
