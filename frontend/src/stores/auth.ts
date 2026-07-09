import { defineStore } from "pinia";

import {
  bootstrapAdmin,
  changePassword as changePasswordApi,
  clearAuthToken,
  fetchCurrentUser,
  getAuthToken,
  login as loginApi,
  logout as logoutApi,
  resetAdminPassword as resetAdminPasswordApi,
  type AuthUser,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    currentUser: null as AuthUser | null,
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

    clearSession(message = "") {
      clearAuthToken();
      this.currentUser = null;
      this.errorMessage = message;
    },

    async loadStoredSession() {
      if (!getAuthToken()) {
        this.clearSession();
        return;
      }
      this.loading = true;
      this.errorMessage = "";
      try {
        this.currentUser = await fetchCurrentUser();
      } catch (error) {
        this.clearSession(error instanceof Error ? error.message : messages.auth.sessionExpired);
      } finally {
        this.loading = false;
      }
    },

    async login(username: string, password: string) {
      this.loading = true;
      this.errorMessage = "";
      try {
        const result = await loginApi({ username, password });
        this.currentUser = result.user;
      } catch (error) {
        this.clearSession(error instanceof Error ? error.message : messages.auth.loginFailed);
        throw error;
      } finally {
        this.loading = false;
      }
    },

    async bootstrapAndLogin(username: string, displayName: string, password: string) {
      this.loading = true;
      this.errorMessage = "";
      try {
        await bootstrapAdmin({ username, displayName, password });
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
        this.currentUser = await changePasswordApi({ currentPassword, newPassword });
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
      this.loading = true;
      try {
        await logoutApi();
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : "";
      } finally {
        this.clearSession();
        this.loading = false;
      }
    },
  },
});
