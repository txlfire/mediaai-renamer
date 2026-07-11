import { defineStore } from "pinia";

import {
  fetchMetadataProviderConfigs,
  fetchAiTestResult,
  fetchImdbTestResult,
  fetchTmdbTestResult,
  fetchSettings,
  saveImdbTestResult,
  saveTmdbTestResult,
  testMetadataProviderConfig,
  testAiSettings,
  testImdbSettings,
  testTmdbSettingsChannel,
  testTmdbSettings,
  updateMetadataProviderConfig,
  updateSettings,
  type AiConnectionTestHistory,
  type AiConnectionTestResult,
  type ImdbConnectionTestHistory,
  type ImdbConnectionTestResult,
  type ImdbStoredConnectionTestResult,
  type MetadataProviderConfig,
  type MetadataProviderConfigPayload,
  type MetadataProviderKey,
  type MetadataProviderTestResult,
  type SystemSetting,
  type TmdbChannelTestResult,
  type TmdbConnectionTestHistory,
  type TmdbConnectionTestResult,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const useSettingsStore = defineStore("settings", {
  state: () => ({
    settings: [] as SystemSetting[],
    metadataProviders: [] as MetadataProviderConfig[],
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

    async saveSettings(values: Record<string, unknown>) {
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

    async loadMetadataProviders() {
      this.loading = true;
      this.errorMessage = "";
      try {
        this.metadataProviders = await fetchMetadataProviderConfigs();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.loadFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async saveMetadataProvider(
      provider: MetadataProviderKey,
      payload: MetadataProviderConfigPayload,
    ): Promise<MetadataProviderConfig> {
      this.loading = true;
      this.errorMessage = "";
      try {
        const updated = await updateMetadataProviderConfig(provider, payload);
        const index = this.metadataProviders.findIndex((item) => item.provider === updated.provider);
        if (index >= 0) {
          this.metadataProviders[index] = updated;
        } else {
          this.metadataProviders.push(updated);
        }
        this.metadataProviders.sort((a, b) => a.priority - b.priority || a.provider.localeCompare(b.provider));
        return updated;
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.saveFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async testMetadataProvider(provider: MetadataProviderKey): Promise<MetadataProviderTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await testMetadataProviderConfig(provider);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.testFailed;
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

    async loadAiTestResult(options: { silent?: boolean } = {}): Promise<AiConnectionTestHistory> {
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await fetchAiTestResult();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.ai.loadTestResultFailed;
        throw error;
      } finally {
        if (!options.silent) {
          this.loading = false;
        }
      }
    },

    async testAiSettings(): Promise<AiConnectionTestResult> {
      this.loading = true;
      this.errorMessage = "";
      try {
        return await testAiSettings();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.settings.ai.testFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },
  },
});
