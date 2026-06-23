/**
 * Vite 开发和构建配置。
 *
 * 开发环境通过代理把 /api 请求转发到本地 FastAPI，生产环境由 Nginx 负责反向代理。
 */

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
