/**
 * 应用全局状态测试。
 */

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveTheme, useAppStore, type ThemeMode, type ThemePalette } from "./app";

function installLocalStorageMock() {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    clear: () => storage.clear(),
    getItem: (key: string) => storage.get(key) ?? null,
    removeItem: (key: string) => storage.delete(key),
    setItem: (key: string, value: string) => storage.set(key, value),
  });
}

describe("theme state", () => {
  beforeEach(() => {
    installLocalStorageMock();
    vi.stubGlobal("document", { documentElement: { dataset: {} as Record<string, string>, style: { colorScheme: "" } } });
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("uses system theme mode by default", () => {
    const store = useAppStore();

    expect(store.themeMode).toBe("system");
  });

  it("persists selected theme mode", () => {
    const store = useAppStore();

    store.setThemeMode("dark");

    expect(store.themeMode).toBe("dark");
    expect(localStorage.getItem("mediaai-theme-mode")).toBe("dark");
    expect(document.documentElement.dataset.scheme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("persists palette independently and preserves it across scheme changes", () => {
    const store = useAppStore();
    store.setThemePalette("candy");
    store.setThemeMode("dark");
    expect(document.documentElement.dataset).toMatchObject({ scheme: "dark", palette: "candy" });
    store.$reset();
    store.loadThemeMode();
    expect(store.themePalette).toBe("candy");
    expect(store.themeMode).toBe("dark");
    localStorage.setItem("mediaai-theme-palette", "__invalid__");
    store.loadThemeMode();
    expect(store.themePalette).toBe("default");
  });

  it("resolves system theme through media query", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });

    expect(resolveTheme("system", matchMedia)).toBe("dark");
    expect(resolveTheme("light", matchMedia)).toBe("light");
    expect(resolveTheme("dark", matchMedia)).toBe("dark");
  });

  it.each<ThemePalette>(["default", "candy", "contrast"])("restores complete %s theme without resetting workspace storage", palette => {
    localStorage.setItem("mediaai-auth-token", "synthetic-session");
    localStorage.setItem("mediaai-filter-fixture", "keep");
    localStorage.setItem("mediaai-sidebar-collapsed", "true");
    const store = useAppStore();
    store.setThemePalette(palette);
    store.setThemeMode("dark");
    store.$reset();
    store.loadThemeMode();
    store.loadSidebarCollapsed();
    expect(store.themePalette).toBe(palette);
    expect(document.documentElement.dataset).toMatchObject({ palette, scheme: "dark" });
    expect(store.sidebarCollapsed).toBe(true);
    expect(localStorage.getItem("mediaai-auth-token")).toBe("synthetic-session");
    expect(localStorage.getItem("mediaai-filter-fixture")).toBe("keep");
  });

  it("system preference changes preserve contrast theme and independent mode storage", () => {
    let prefersDark = true;
    vi.stubGlobal("window", { matchMedia: () => ({ matches: prefersDark }) });
    const store = useAppStore();
    store.setThemePalette("contrast");
    store.setThemeMode("system");
    expect(store.resolvedTheme).toBe("dark");
    prefersDark = false;
    store.applyThemeMode();
    expect(store.resolvedTheme).toBe("light");
    expect(store.themePalette).toBe("contrast");
    expect(localStorage.getItem("mediaai-theme-mode")).toBe("system");
    expect(localStorage.getItem("mediaai-theme-palette")).toBe("contrast");
    vi.unstubAllGlobals();
  });

  it("ignores invalid stored theme modes", () => {
    localStorage.setItem("mediaai-theme-mode", "blue");
    const store = useAppStore();

    store.loadThemeMode();

    expect(store.themeMode satisfies ThemeMode).toBe("system");
  });

  it("persists sidebar collapsed state", () => {
    const store = useAppStore();

    store.setSidebarCollapsed(true);

    expect(store.sidebarCollapsed).toBe(true);
    expect(localStorage.getItem("mediaai-sidebar-collapsed")).toBe("true");
  });

  it("loads and toggles sidebar collapsed state through one path", () => {
    localStorage.setItem("mediaai-sidebar-collapsed", "true");
    const store = useAppStore();

    store.loadSidebarCollapsed();
    expect(store.sidebarCollapsed).toBe(true);

    store.toggleSidebar();
    expect(store.sidebarCollapsed).toBe(false);
    expect(localStorage.getItem("mediaai-sidebar-collapsed")).toBe("false");
  });
});
