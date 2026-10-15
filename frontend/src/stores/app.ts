import { defineStore } from "pinia";

import { getHealth, type HealthStatus } from "../api/client";
import { isThemePalette, type ThemePalette } from "../config/themeRegistry";
import { zhCnMessages as messages } from "../locales/zh-CN";

type ConnectionState = "idle" | "loading" | "online" | "offline";
export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";
export type { ThemePalette } from "../config/themeRegistry";

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
      const storedPalette = localStorage.getItem(PALETTE_STORAGE_KEY);
      this.themePalette = isThemePalette(storedPalette) ? storedPalette : "default";
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
        // 完整主题声明成对基础值，显示模式只解析语义别名，不重置主题或工作区。
        document.documentElement.dataset.scheme = this.resolvedTheme;
        document.documentElement.dataset.palette = this.themePalette;
        if (document.documentElement.style) document.documentElement.style.colorScheme = this.resolvedTheme;
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
