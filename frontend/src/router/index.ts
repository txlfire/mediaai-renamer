/**
 * 前端路由配置。
 *
 * M1 阶段提供媒体源、扫描任务和扫描结果三个操作台页面。
 */

import { createRouter, createWebHistory } from "vue-router";

import { getAuthToken } from "../api/client";
import { useAuthStore } from "../stores/auth";
import LoginView from "../views/LoginView.vue";
import MediaSourcesView from "../views/MediaSourcesView.vue";
import RenamePreviewsView from "../views/RenamePreviewsView.vue";
import ScanJobsView from "../views/ScanJobsView.vue";
import ScanResultsView from "../views/ScanResultsView.vue";
import SettingsView from "../views/SettingsView.vue";
import TaskGovernanceView from "../views/TaskGovernanceView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      redirect: "/media-sources",
    },
    {
      path: "/login",
      name: "login",
      component: LoginView,
      meta: { public: true },
    },
    {
      path: "/media-sources",
      name: "media-sources",
      component: MediaSourcesView,
    },
    {
      path: "/scan-jobs",
      name: "scan-jobs",
      component: ScanJobsView,
    },
    {
      path: "/scan-results",
      name: "scan-results",
      component: ScanResultsView,
    },
    {
      path: "/rename-previews",
      name: "rename-previews",
      component: RenamePreviewsView,
    },
    {
      path: "/tasks",
      name: "tasks",
      component: TaskGovernanceView,
    },
    {
      path: "/settings",
      name: "settings",
      component: SettingsView,
    },
  ],
});

router.beforeEach(async (to) => {
  const authStore = useAuthStore();
  if (to.meta.public) {
    if (authStore.isAuthenticated && to.path === "/login") {
      return "/media-sources";
    }
    return true;
  }

  if (!getAuthToken()) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }

  if (!authStore.currentUser) {
    await authStore.loadStoredSession();
  }

  if (!authStore.isAuthenticated) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }

  return true;
});

export default router;
