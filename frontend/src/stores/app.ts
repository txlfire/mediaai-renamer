import { defineStore } from "pinia";

import { getHealth, type HealthStatus } from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

type ConnectionState = "idle" | "loading" | "online" | "offline";
export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";
export type ThemePalette = "default" | "candy";

const THEME_STORAGE_KEY = "mediaai-theme-mode";
const PALETTE_STORAGE_KEY = "mediaai-theme-palette";
const SIDEBAR_STORAGE_KEY = "mediaai-sidebar-collapsed";

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
    themePalette: "default" as ThemePalette,
  }),
  actions: {
    loadSidebarCollapsed() {
      this.sidebarCollapsed = localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
    },

    setSidebarCollapsed(collapsed: boolean) {
      this.sidebarCollapsed = collapsed;
      localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed));
    },

    loadThemeMode() {
      const storedMode = localStorage.getItem(THEME_STORAGE_KEY);
      this.themeMode = isThemeMode(storedMode) ? storedMode : "system";
      this.themePalette = localStorage.getItem(PALETTE_STORAGE_KEY) === "candy" ? "candy" : "default";
      this.applyThemeMode();
    },

    setThemeMode(mode: ThemeMode) {
      this.themeMode = mode;
      localStorage.setItem(THEME_STORAGE_KEY, mode);
      this.applyThemeMode();
    },

    applyThemeMode() {
      this.resolvedTheme = resolveTheme(this.themeMode);
      if (typeof document !== "undefined") {
        // 方案轴（亮/暗）写入 data-scheme。配色轴（palette）为独立属性 data-palette，
        // 两轴正交：styles.css 的 :root 与 :root[data-scheme="dark"] 承担中性色，
        // :root[data-palette="…"] 承担点缀色。详见 design.md「主题架构」。
        document.documentElement.dataset.scheme = this.resolvedTheme;
        document.documentElement.dataset.palette = this.themePalette;
      }
    },

    setThemePalette(palette: ThemePalette) {
      this.themePalette = palette;
      localStorage.setItem(PALETTE_STORAGE_KEY, palette);
      this.applyThemeMode();
    },

    toggleSidebar() {
      this.setSidebarCollapsed(!this.sidebarCollapsed);
    },

    async refreshHealth() {
      this.connectionState = "loading";
      this.errorMessage = "";

      try {
        this.health = await getHealth();
        this.connectionState = "online";
      } catch (error) {
        this.health = null;
        this.connectionState = "offline";
        this.errorMessage = error instanceof Error ? error.message : messages.errors.backendConnectionFailed;
      }
    },
  },
});
