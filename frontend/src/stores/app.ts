/**
 * 应用全局状态。
 *
 * 当前负责保存后端连接状态。后续扫描任务、日志状态、配置状态应拆分到独立 store。
 */

import { defineStore } from "pinia";

import { getHealth, type HealthStatus } from "../api/client";

type ConnectionState = "idle" | "loading" | "online" | "offline";
export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const THEME_STORAGE_KEY = "mediaai-theme-mode";

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}

export function resolveTheme(
  mode: ThemeMode,
  matchMedia?: (query: string) => Pick<MediaQueryList, "matches">,
): ResolvedTheme {
  if (mode === "light" || mode === "dark") {
    return mode;
  }

  const query =
    matchMedia ??
    (typeof window === "undefined" ? undefined : window.matchMedia.bind(window));

  return query?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const useAppStore = defineStore("app", {
  state: () => ({
    connectionState: "idle" as ConnectionState,
    health: null as HealthStatus | null,
    errorMessage: "",
    sidebarCollapsed: false,
    themeMode: "system" as ThemeMode,
    resolvedTheme: "light" as ResolvedTheme,
  }),
  actions: {
    /**
     * 从本地存储加载主题模式，无效值回退到跟随系统。
     */
    loadThemeMode() {
      const storedMode = localStorage.getItem(THEME_STORAGE_KEY);
      this.themeMode = isThemeMode(storedMode) ? storedMode : "system";
      this.applyThemeMode();
    },

    /**
     * 设置并持久化主题模式。
     */
    setThemeMode(mode: ThemeMode) {
      this.themeMode = mode;
      localStorage.setItem(THEME_STORAGE_KEY, mode);
      this.applyThemeMode();
    },

    /**
     * 将主题模式解析为实际主题，并同步到页面根节点。
     */
    applyThemeMode() {
      this.resolvedTheme = resolveTheme(this.themeMode);
      if (typeof document !== "undefined") {
        document.documentElement.dataset.theme = this.resolvedTheme;
      }
    },

    /**
     * 切换侧栏展开状态。
     */
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed;
    },

    /**
     * 刷新后端健康状态，并将结果转换为页面可展示的连接状态。
     */
    async refreshHealth() {
      this.connectionState = "loading";
      this.errorMessage = "";

      try {
        this.health = await getHealth();
        this.connectionState = "online";
      } catch (error) {
        // 健康检查失败不阻塞页面渲染，只记录为离线状态并展示错误信息。
        this.health = null;
        this.connectionState = "offline";
        this.errorMessage = error instanceof Error ? error.message : "后端连接失败";
      }
    },
  },
});
