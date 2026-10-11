/**
 * Vite 开发和构建配置。
 *
 * 开发环境代理 /api 到 FastAPI；统一生产镜像由 FastAPI 承载前端静态资源。
 */

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

function isVueUseInvalidAnnotationWarning(log: { code?: string; id?: string; message?: string }) {
  const source = `${log.id ?? ""} ${log.message ?? ""}`.replaceAll("\\", "/");

  return log.code === "INVALID_ANNOTATION" && source.includes("node_modules/@vueuse/core/dist/index.js");
}

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL || "http://127.0.0.1:8970",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rolldownOptions: {
      onLog(level, log, defaultHandler) {
        if (level === "warn" && isVueUseInvalidAnnotationWarning(log)) {
          return;
        }

        defaultHandler(level, log);
      },
    },
  },
});
