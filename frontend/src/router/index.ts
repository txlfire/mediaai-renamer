/**
 * 前端路由配置。
 *
 * 页面按路由加载，认证守卫与现有路径保持不变。
 */

import { createRouter, createWebHistory } from "vue-router";
import { ref } from "vue";
import { ElMessage } from "element-plus";

import { getAuthToken } from "../api/client";
import { useAuthStore } from "../stores/auth";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const routeLoading = ref(true);

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
      component: () => import("../views/LoginView.vue"),
      meta: { public: true },
    },
    {
      path: "/media-sources",
      name: "media-sources",
      component: () => import("../views/MediaSourcesView.vue"),
    },
    {
      path: "/scan-jobs",
      name: "scan-jobs",
      component: () => import("../views/ScanJobsView.vue"),
    },
    {
      path: "/scan-results",
      name: "scan-results",
      component: () => import("../views/ScanResultsView.vue"),
    },
    {
      path: "/rename-previews",
      name: "rename-previews",
      component: () => import("../views/RenamePreviewsView.vue"),
    },
    {
      path: "/tasks",
      name: "tasks",
      component: () => import("../views/TaskGovernanceView.vue"),
    },
    {
      path: "/settings",
      name: "settings",
      component: () => import("../views/SettingsView.vue"),
      meta: { permission: "settings:write" },
    },
  ],
});

router.beforeEach(async (to) => {
  routeLoading.value = true;
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

  const requiredPermission = typeof to.meta.permission === "string" ? to.meta.permission : "";
  if (requiredPermission && !authStore.hasPermission(requiredPermission)) {
    ElMessage.warning({ message: messages.auth.permissionDenied, grouping: true });
    return "/media-sources";
  }

  return true;
});

router.afterEach(() => {
  routeLoading.value = false;
});

router.onError(() => {
  routeLoading.value = false;
  ElMessage.error({ message: messages.app.pageLoadFailed, grouping: true });
});

export default router;
