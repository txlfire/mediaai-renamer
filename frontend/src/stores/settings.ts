import { defineStore } from "pinia";

import {
  fetchSettings,
  testTmdbSettings,
  updateSettings,
  type SystemSetting,
  type TmdbConnectionTestResult,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const useSettingsStore = defineStore("settings", {
  state: () => ({
    settings: [] as SystemSetting[],
    loading: false,
    errorMessage: "",
  }),
  getters: {
    settingMap(state) {
      return Object.fromEntries(state.settings.map((item) => [item.key, item])) as Record<string, SystemSetting>;
    },
  },
  actions: {
    async loadSettings() {
      this.loading = true;
      this.errorMessage = "";
      try {
        this.settings = await fetchSettings();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.loadFailed;
      } finally {
        this.loading = false;
      }
    },

    async saveSettings(values: Record<string, string | number | boolean>) {
      this.loading = true;
      this.errorMessage = "";
      try {
        this.settings = await updateSettings(values);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.saveFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async testTmdbSettings(): Promise<TmdbConnectionTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await testTmdbSettings();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },
  },
});
