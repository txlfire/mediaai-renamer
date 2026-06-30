import { defineStore } from "pinia";

import {
  fetchImdbTestResult,
  fetchTmdbTestResult,
  fetchSettings,
  saveImdbTestResult,
  saveTmdbTestResult,
  testImdbSettings,
  testTmdbSettingsChannel,
  testTmdbSettings,
  updateSettings,
  type ImdbConnectionTestHistory,
  type ImdbConnectionTestResult,
  type ImdbStoredConnectionTestResult,
  type SystemSetting,
  type TmdbChannelTestResult,
  type TmdbConnectionTestHistory,
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

    async loadTmdbTestResult(options: { silent?: boolean } = {}): Promise<TmdbConnectionTestHistory> {
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await fetchTmdbTestResult();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.loadTestResultFailed;
        throw error;
      } finally {
        if (!options.silent) {
          this.loading = false;
        }
      }
    },

    async testTmdbSettingsChannel(channel: "v4" | "v3"): Promise<TmdbChannelTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await testTmdbSettingsChannel(channel);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async saveTmdbTestResult(
      v4: TmdbChannelTestResult,
      v3: TmdbChannelTestResult,
    ): Promise<TmdbConnectionTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await saveTmdbTestResult(v4, v3);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async loadImdbTestResult(options: { silent?: boolean } = {}): Promise<ImdbConnectionTestHistory> {
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await fetchImdbTestResult();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.imdb.loadTestResultFailed;
        throw error;
      } finally {
        if (!options.silent) {
          this.loading = false;
        }
      }
    },

    async testImdbSettings(): Promise<ImdbConnectionTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await testImdbSettings();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.imdb.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async saveImdbTestResult(result: ImdbConnectionTestResult): Promise<ImdbStoredConnectionTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await saveImdbTestResult(result);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.imdb.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },
  },
});
