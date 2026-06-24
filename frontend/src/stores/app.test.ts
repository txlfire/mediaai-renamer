/**
 * 应用全局状态测试。
 */

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveTheme, useAppStore, type ThemeMode } from "./app";

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
  });

  it("resolves system theme through media query", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });

    expect(resolveTheme("system", matchMedia)).toBe("dark");
    expect(resolveTheme("light", matchMedia)).toBe("light");
    expect(resolveTheme("dark", matchMedia)).toBe("dark");
  });

  it("ignores invalid stored theme modes", () => {
    localStorage.setItem("mediaai-theme-mode", "blue");
    const store = useAppStore();

    store.loadThemeMode();

    expect(store.themeMode satisfies ThemeMode).toBe("system");
  });
});
