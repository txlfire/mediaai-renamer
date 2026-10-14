/// <reference types="vite/client" />
import html from "../../index.html?raw";
import { describe, expect, it } from "vitest";

const bootstrap = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
if (!bootstrap) throw new Error("Missing theme bootstrap");

function initialTheme(mode: string | null, palette: string | null, prefersDark = false) {
  const document = { documentElement: { dataset: {} as Record<string, string>, style: { colorScheme: "" } } };
  const storage = new Map([["mediaai-theme-mode", mode], ["mediaai-theme-palette", palette]]);
  // 只执行仓库内的启动脚本，显式注入隔离对象，不读取真实浏览器存储。
  const runBootstrap = new Function("document", "localStorage", "matchMedia", bootstrap!);
  runBootstrap(document, { getItem: (key: string) => storage.get(key) ?? null },
    () => ({ matches: prefersDark }));
  expect(document.documentElement.style.colorScheme).toBe(document.documentElement.dataset.scheme);
  return document.documentElement.dataset;
}

describe("theme before first paint", () => {
  it.each(["default", "candy", "contrast"])("restores %s in both modes before app startup", palette => {
    expect(initialTheme("light", palette)).toEqual({ scheme: "light", palette });
    expect(initialTheme("dark", palette)).toEqual({ scheme: "dark", palette });
  });

  it("resolves system preference without dropping the selected theme", () => {
    expect(initialTheme("system", "contrast", true)).toEqual({ scheme: "dark", palette: "contrast" });
    expect(initialTheme("system", "candy", false)).toEqual({ scheme: "light", palette: "candy" });
  });

  it("falls back for empty and unsupported stored values", () => {
    expect(initialTheme(null, null)).toEqual({ scheme: "light", palette: "default" });
    expect(initialTheme("invalid", "invalid", true)).toEqual({ scheme: "dark", palette: "default" });
  });
});
