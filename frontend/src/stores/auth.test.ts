import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "./auth";

const api = vi.hoisted(() => ({
  bootstrapAdmin: vi.fn(),
  clearAuthToken: vi.fn(),
  fetchCurrentUser: vi.fn(),
  getAuthToken: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
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

  it("logs in and exposes user permissions", async () => {
    api.login.mockResolvedValue({
      accessToken: "token-123",
      tokenType: "bearer",
      expiresAt: "2026-07-09T00:00:00+00:00",
      user,
    });
    api.getAuthToken.mockReturnValue("token-123");
    const store = useAuthStore();

    await store.login("admin", "ChangeMe123!");

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

    await store.bootstrapAndLogin("admin", "系统管理员", "ChangeMe123!");

    expect(api.bootstrapAdmin).toHaveBeenCalledWith({
      username: "admin",
      displayName: "系统管理员",
      password: "ChangeMe123!",
    });
    expect(store.currentUser?.displayName).toBe("系统管理员");
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
    await store.login("admin", "ChangeMe123!");

    await store.logout();

    expect(api.logout).toHaveBeenCalled();
    expect(api.clearAuthToken).toHaveBeenCalled();
    expect(store.currentUser).toBe(null);
  });
});
