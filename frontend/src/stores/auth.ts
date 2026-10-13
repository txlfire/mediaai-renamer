import { defineStore, type Pinia } from "pinia";

import {
  bootstrapAdmin,
  changePassword as changePasswordApi,
  clearAuthToken,
  fetchBootstrapStatus,
  fetchCurrentUser,
  getAuthToken,
  login as loginApi,
  logout as logoutApi,
  resetAdminPassword as resetAdminPasswordApi,
  type AuthUser,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { resetWorkspaceState } from "./workspace";
import { getSessionGeneration } from "../utils/sessionGeneration";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    currentUser: null as AuthUser | null,
    bootstrapAvailable: false,
    loading: false,
    errorMessage: "",
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.currentUser && getAuthToken()),
  },
  actions: {
    hasPermission(permission: string) {
      return this.currentUser?.permissions.includes(permission) ?? false;
    },

    async loadBootstrapStatus() {
      try {
        const result = await fetchBootstrapStatus();
        this.bootstrapAvailable = result.available;
      } catch {
        this.bootstrapAvailable = false;
      }
    },

    clearSession(message = "") {
      const pinia = (this as unknown as { _p?: Pinia })._p;
      if (pinia) {
        resetWorkspaceState(pinia);
      }
      clearAuthToken();
      this.currentUser = null;
      this.loading = false;
      this.errorMessage = message;
    },

    async loadStoredSession() {
      const generation = getSessionGeneration();
      if (!getAuthToken()) {
        this.clearSession();
        return;
      }
      this.loading = true;
      this.errorMessage = "";
      try {
        const user = await fetchCurrentUser();
        if (generation !== getSessionGeneration()) return;
        this.currentUser = user;
      } catch (error) {
        if (generation !== getSessionGeneration()) return;
        this.clearSession(error instanceof Error ? error.message : messages.auth.sessionExpired);
      } finally {
        if (generation === getSessionGeneration()) this.loading = false;
      }
    },

    async login(username: string, password: string, rememberLogin = false) {
      let generation = getSessionGeneration();
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await loginApi({ username, password, rememberLogin });
        generation = getSessionGeneration();
        this.currentUser = result.user;
      } catch (error) {
        if (generation !== getSessionGeneration()) throw error;
        this.clearSession(error instanceof Error ? error.message : messages.auth.loginFailed);
        throw error;
      } finally {
        if (generation === getSessionGeneration()) this.loading = false;
      }
    },

    async bootstrapAndLogin(username: string, displayName: string, password: string) {
      this.loading = true;
      this.errorMessage = "";
      try {
        await bootstrapAdmin({ username, displayName, password });
        this.bootstrapAvailable = false;
        const result = await loginApi({ username, password });
        this.currentUser = result.user;
      } catch (error) {
        this.clearSession(error instanceof Error ? error.message : messages.auth.bootstrapFailed);
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async changePassword(currentPassword: string, newPassword: string) {
      this.loading = true;
      this.errorMessage = "";
      try {
        await changePasswordApi({ currentPassword, newPassword });
        this.clearSession();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.auth.changePasswordFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async resetAdminPassword() {
      this.loading = true;
      this.errorMessage = "";
      try {
        await resetAdminPasswordApi();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.auth.resetAdminPasswordFailed;
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async logout() {
      const generation = getSessionGeneration();
      this.loading = true;
      try {
        await logoutApi();
      } catch (error) {
        if (generation !== getSessionGeneration()) return;
        this.errorMessage = error instanceof Error ? error.message : "";
      } finally {
        if (generation === getSessionGeneration() || !getAuthToken()) this.clearSession();
      }
    },
  },
});
