/**
 * 应用全局状态。
 *
 * 当前负责保存后端连接状态。后续扫描任务、日志状态、配置状态应拆分到独立 store。
 */

import { defineStore } from "pinia";

import { getHealth, type HealthStatus } from "../api/client";

type ConnectionState = "idle" | "loading" | "online" | "offline";

export const useAppStore = defineStore("app", {
  state: () => ({
    connectionState: "idle" as ConnectionState,
    health: null as HealthStatus | null,
    errorMessage: "",
  }),
  actions: {
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
