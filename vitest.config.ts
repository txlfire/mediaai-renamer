import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "node",
    css: { include: [/styles\.css/] },
    include: ["frontend/src/**/*.test.ts"],
  },
});
