/**
 * Vite 开发和构建配置。
 *
 * 开发环境通过代理把 /api 请求转发到本地 FastAPI，生产环境由 Nginx 负责反向代理。
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
        target: "http://127.0.0.1:8970",
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
