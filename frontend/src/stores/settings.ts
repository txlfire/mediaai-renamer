import { getSessionGeneration, sessionRequest } from "../utils/sessionGeneration";
import { defineStore } from "pinia";

import {
  fetchMetadataProviderConfigs,
  fetchAiTestResult,
  fetchImdbTestResult,
  fetchTmdbTestResult,
  fetchSettings,
  testMetadataProviderConfig,
  testAiSettings,
  testImdbSettings,
  testTmdbSettings,
  updateMetadataProviderConfig,
  updateSettings,
  type AiConnectionTestHistory,
  type AiConnectionTestResult,
  type ImdbConnectionTestHistory,
  type ImdbConnectionTestResult,
  type MetadataProviderConfig,
  type MetadataProviderConfigPayload,
  type MetadataProviderKey,
  type MetadataProviderTestResult,
  type SystemSetting,
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
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        this.settings = await sessionRequest(fetchSettings(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.loadFailed;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async saveSettings(values: Record<string, unknown>) {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        this.settings = await sessionRequest(updateSettings(values), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.saveFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async loadMetadataProviders() {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        this.metadataProviders = await sessionRequest(fetchMetadataProviderConfigs(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.loadFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async saveMetadataProvider(
      provider: MetadataProviderKey,
      payload: MetadataProviderConfigPayload,
    ): Promise<MetadataProviderConfig> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const updated = await sessionRequest(updateMetadataProviderConfig(provider, payload), sessionGeneration);
        const index = this.metadataProviders.findIndex((item) => item.provider === updated.provider);
        if (index >= 0) {
          this.metadataProviders[index] = updated;
        } else {
          this.metadataProviders.push(updated);
        }
        this.metadataProviders.sort((a, b) => a.priority - b.priority || a.provider.localeCompare(b.provider));
        return updated;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.saveFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async testMetadataProvider(provider: MetadataProviderKey): Promise<MetadataProviderTestResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(testMetadataProviderConfig(provider), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.metadataProviders.testFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async testTmdbSettings(): Promise<TmdbConnectionTestResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(testTmdbSettings(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.testFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async loadTmdbTestResult(options: { silent?: boolean } = {}): Promise<TmdbConnectionTestHistory> {
      const sessionGeneration = getSessionGeneration();
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await sessionRequest(fetchTmdbTestResult(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.tmdb.loadTestResultFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          if (!options.silent) {
            this.loading = false;
          }
        }
      }
    },

    async loadImdbTestResult(options: { silent?: boolean } = {}): Promise<ImdbConnectionTestHistory> {
      const sessionGeneration = getSessionGeneration();
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await sessionRequest(fetchImdbTestResult(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.imdb.loadTestResultFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          if (!options.silent) {
            this.loading = false;
          }
        }
      }
    },

    async testImdbSettings(): Promise<ImdbConnectionTestResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(testImdbSettings(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.imdb.testFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },

    async loadAiTestResult(options: { silent?: boolean } = {}): Promise<AiConnectionTestHistory> {
      const sessionGeneration = getSessionGeneration();
      if (!options.silent) {
        this.loading = true;
      }
      this.errorMessage = "";
      try {
        return await sessionRequest(fetchAiTestResult(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.ai.loadTestResultFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          if (!options.silent) {
            this.loading = false;
          }
        }
      }
    },

    async testAiSettings(): Promise<AiConnectionTestResult> {
      const sessionGeneration = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        return await sessionRequest(testAiSettings(), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        this.errorMessage = error instanceof Error ? error.message : messages.settings.ai.testFailed;
        throw error;
      } finally {
        if (sessionGeneration === getSessionGeneration()) {
          this.loading = false;
        }
      }
    },
  },
});
