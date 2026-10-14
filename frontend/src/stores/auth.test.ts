import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "./auth";
import { useMediaStore } from "./media";
import { usePaginationStore } from "./pagination";
import { usePendingFileStore } from "./pendingFiles";
import { usePreviewStore } from "./preview";
import { useRenameOperationStore } from "./renameOperation";
import { useTableSortStore } from "./tableSort";
import { invalidateSessionRequests, SessionChangedError } from "../utils/sessionGeneration";

const api = vi.hoisted(() => ({
  bootstrapAdmin: vi.fn(),
  changePassword: vi.fn(),
  clearAuthToken: vi.fn(),
  fetchBootstrapStatus: vi.fn(),
  fetchCurrentUser: vi.fn(),
  getAuthToken: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  resetAdminPassword: vi.fn(),
}));

vi.mock("../api/client", () => api);

function installLocalStorageMock() {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    clear: () => storage.clear(),
    getItem: (key: string) => storage.get(key) ?? null,
    removeItem: (key: string) => storage.delete(key),
    setItem: (key: string, value: string) => storage.set(key, value),
  });
}

const user = {
  id: 1,
  username: "admin",
  displayName: "系统管理员",
  enabled: true,
  permissions: ["settings:write", "source:write"],
  mustChangePassword: false,
  passwordChangedAt: null,
  lastLoginAt: null,
  createdAt: "2026-07-09T00:00:00+00:00",
  updatedAt: "2026-07-09T00:00:00+00:00",
};

describe("auth store", () => {
  beforeEach(() => {
    installLocalStorageMock();
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("clears loading immediately when the session is cleared", () => {
    const store = useAuthStore();
    store.loading = true;
    store.currentUser = user;
    store.clearSession();
    expect(store.loading).toBe(false);
    expect(store.currentUser).toBeNull();
  });

  it.each(["bootstrap", "change", "reset"])("ignores a late %s failure without changing the newer session", async action => {
    let reject!: (error: Error) => void;
    const pending = new Promise<void>((_, fail) => { reject = fail; });
    const store = useAuthStore();
    const requests = {
      bootstrap: () => { api.bootstrapAdmin.mockReturnValueOnce(pending); return store.bootstrapAndLogin("synthetic", "synthetic", "SyntheticPassword!"); },
      change: () => { api.changePassword.mockReturnValueOnce(pending); return store.changePassword("SyntheticPassword!", "SyntheticNewPassword!"); },
      reset: () => { api.resetAdminPassword.mockReturnValueOnce(pending); return store.resetAdminPassword(); },
    };
    const request = requests[action as keyof typeof requests]();
    const rejected = expect(request).rejects.toBeInstanceOf(SessionChangedError);
    invalidateSessionRequests();
    store.currentUser = user;
    store.loading = true;
    store.errorMessage = "新会话状态";
    reject(new Error("旧请求失败"));
    await rejected;
    expect(store.currentUser).toEqual(user);
    expect(store.errorMessage).toBe("新会话状态");
    expect(store.loading).toBe(true);
    expect(api.clearAuthToken).not.toHaveBeenCalled();
  });

  it("does not clear a newer session when an earlier login fails", async () => {
    let rejectRequest!: (error: Error) => void;
    api.login.mockReturnValueOnce(new Promise((_, reject) => { rejectRequest = reject; }));
    const store = useAuthStore();
    const request = store.login("previous-user", "SyntheticPassword!").catch(error => error);
    invalidateSessionRequests();
    store.currentUser = user;
    rejectRequest(new Error("旧登录请求失败"));
    await request;
    expect(store.currentUser).toEqual(user);
    expect(api.clearAuthToken).not.toHaveBeenCalled();
  });

  it("does not clear a newer session after an earlier logout completes", async () => {
    api.getAuthToken.mockReturnValue("new-session");
    let resolveRequest!: () => void;
    api.logout.mockReturnValueOnce(new Promise<void>(resolve => { resolveRequest = resolve; }));
    const store = useAuthStore();
    const request = store.logout();
    invalidateSessionRequests();
    store.currentUser = user;
    resolveRequest();
    await request;
    expect(store.currentUser).toEqual(user);
    expect(api.clearAuthToken).not.toHaveBeenCalled();
  });

  it("logs in and exposes user permissions", async () => {
    api.login.mockResolvedValue({
      accessToken: "token-123",
      tokenType: "bearer",
      expiresAt: "2026-07-09T00:00:00+00:00",
      user,
    });
    api.getAuthToken.mockReturnValue("token-123");
    const store = useAuthStore();

    await store.login("admin", "ChangeMe123!", true);

    expect(api.login).toHaveBeenCalledWith({
      username: "admin",
      password: "ChangeMe123!",
      rememberLogin: true,
    });
    expect(store.currentUser?.username).toBe("admin");
    expect(store.isAuthenticated).toBe(true);
    expect(store.hasPermission("settings:write")).toBe(true);
    expect(store.hasPermission("rename:execute")).toBe(false);
  });

  it("bootstraps the first admin and then logs in", async () => {
    api.bootstrapAdmin.mockResolvedValue(user);
    api.login.mockResolvedValue({
      accessToken: "token-123",
      tokenType: "bearer",
      expiresAt: "2026-07-09T00:00:00+00:00",
      user,
    });
    const store = useAuthStore();
    store.bootstrapAvailable = true;

    await store.bootstrapAndLogin("admin", "系统管理员", "ChangeMe123!");

    expect(api.bootstrapAdmin).toHaveBeenCalledWith({
      username: "admin",
      displayName: "系统管理员",
      password: "ChangeMe123!",
    });
    expect(store.currentUser?.displayName).toBe("系统管理员");
    expect(store.bootstrapAvailable).toBe(false);
  });

  it("loads bootstrap availability and hides the entry when the request fails", async () => {
    api.fetchBootstrapStatus
      .mockResolvedValueOnce({ available: true })
      .mockRejectedValueOnce(new Error("network down"));
    const store = useAuthStore();

    await store.loadBootstrapStatus();
    expect(store.bootstrapAvailable).toBe(true);

    await store.loadBootstrapStatus();
    expect(store.bootstrapAvailable).toBe(false);
  });

  it("loads stored token session and clears invalid sessions", async () => {
    api.getAuthToken.mockReturnValueOnce("token-123").mockReturnValueOnce("token-123");
    api.fetchCurrentUser.mockResolvedValueOnce(user).mockRejectedValueOnce(new Error("请先登录"));
    const store = useAuthStore();

    await store.loadStoredSession();
    expect(store.currentUser?.username).toBe("admin");

    await store.loadStoredSession();
    expect(store.currentUser).toBe(null);
    expect(api.clearAuthToken).toHaveBeenCalled();
  });

  it("logs out and clears local session", async () => {
    api.login.mockResolvedValue({
      accessToken: "token-123",
      tokenType: "bearer",
      expiresAt: "2026-07-09T00:00:00+00:00",
      user,
    });
    const store = useAuthStore();
    const mediaStore = useMediaStore();
    const previewStore = usePreviewStore();
    const pendingStore = usePendingFileStore();
    const renameStore = useRenameOperationStore();
    const paginationStore = usePaginationStore();
    const tableSortStore = useTableSortStore();
    await store.login("admin", "ChangeMe123!");
    mediaStore.mediaFiles = [{ id: 1 } as never];
    previewStore.previews = [{ id: 2 } as never];
    previewStore.filters = { scan_job_id: 9 };
    pendingStore.pendingFiles = [{ id: 3 } as never];
    pendingStore.moveVersion = 4;
    renameStore.currentOperation = { id: 5 } as never;
    renameStore.executionVersion = 6;
    renameStore.rollbackVersion = 7;
    paginationStore.setPage("rename-previews", 3);
    tableSortStore.setSort("rename-previews", "id", "descending");

    await store.logout();

    expect(api.logout).toHaveBeenCalled();
    expect(api.clearAuthToken).toHaveBeenCalled();
    expect(store.currentUser).toBe(null);
    expect(mediaStore.mediaFiles).toEqual([]);
    expect(previewStore.previews).toEqual([]);
    expect(previewStore.filters).toEqual({});
    expect(pendingStore.pendingFiles).toEqual([]);
    expect(pendingStore.moveVersion).toBe(5);
    expect(renameStore.currentOperation).toBe(null);
    expect(renameStore.executionVersion).toBe(7);
    expect(renameStore.rollbackVersion).toBe(8);
    expect(paginationStore.pages).toEqual({});
    expect(tableSortStore.sorts).toEqual({});
  });

  it("changes password and clears the revoked session", async () => {
    api.changePassword.mockResolvedValue({
      ...user,
      mustChangePassword: false,
      passwordChangedAt: "2026-07-09T00:10:00+00:00",
    });
    const store = useAuthStore();

    await store.changePassword("123456", "ChangeMe123!");

    expect(api.changePassword).toHaveBeenCalledWith({
      currentPassword: "123456",
      newPassword: "ChangeMe123!",
    });
    expect(store.currentUser).toBe(null);
    expect(api.clearAuthToken).toHaveBeenCalled();
  });

  it("keeps the existing session when password validation fails", async () => {
    api.changePassword.mockRejectedValue(new Error("invalid password"));
    const store = useAuthStore();
    store.currentUser = user;

    await expect(store.changePassword("wrong", "ChangeMe123!")).rejects.toThrow("invalid password");

    expect(store.currentUser).toEqual(user);
    expect(api.clearAuthToken).not.toHaveBeenCalled();
    expect(store.loading).toBe(false);
  });

  it("calls hidden admin password reset endpoint", async () => {
    api.resetAdminPassword.mockResolvedValue({ ...user, mustChangePassword: true });
    const store = useAuthStore();

    await store.resetAdminPassword();

    expect(api.resetAdminPassword).toHaveBeenCalled();
  });
});
