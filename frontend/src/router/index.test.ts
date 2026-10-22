import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  config: null as null | { routes: { path: string; component?: unknown }[] },
  guard: null as null | ((to: { path: string; fullPath: string; meta: { public?: boolean; permission?: string } }) => Promise<unknown>),
  after: null as null | (() => void),
  error: null as null | (() => void),
  token: null as string | null,
  auth: { isAuthenticated: false, currentUser: null as null | { id: number }, loadStoredSession: vi.fn(), hasPermission: vi.fn() },
  message: vi.fn(),
}));

vi.mock("vue-router", () => ({
  createWebHistory: () => ({}),
  createRouter: (config: typeof state.config) => {
    state.config = config;
    return {
      beforeEach: (guard: typeof state.guard) => { state.guard = guard; },
      afterEach: (after: typeof state.after) => { state.after = after; },
      onError: (error: typeof state.error) => { state.error = error; },
    };
  },
}));
vi.mock("../api/client", () => ({ getAuthToken: () => state.token }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => state.auth }));
vi.mock("element-plus", () => ({ ElMessage: { error: state.message, warning: state.message } }));

import { routeLoading } from "./index";

describe("lazy routes and authentication", () => {
  beforeEach(() => {
    state.token = null;
    state.auth.isAuthenticated = false;
    state.auth.currentUser = null;
    state.auth.loadStoredSession.mockReset();
    state.auth.hasPermission.mockReset();
    state.message.mockReset();
    routeLoading.value = false;
  });

  it("keeps existing paths and loads each view on demand", () => {
    const pages = state.config!.routes.filter((route) => route.component);
    expect(pages.map((route) => route.path)).toEqual([
      "/login", "/media-sources", "/scan-jobs", "/scan-results", "/rename-previews", "/tasks", "/cloud-inventory", "/settings",
    ]);
    expect(pages.every((route) => typeof route.component === "function")).toBe(true);
  });

  it("preserves the login redirect when no token is present", async () => {
    const result = await state.guard!({ path: "/settings", fullPath: "/settings?section=ai", meta: {} });
    expect(result).toEqual({ path: "/login", query: { redirect: "/settings?section=ai" } });
    expect(routeLoading.value).toBe(true);
    state.after!();
    expect(routeLoading.value).toBe(false);
  });

  it("loads a stored session and rejects expired authentication", async () => {
    state.token = "synthetic-token";
    const result = await state.guard!({ path: "/settings", fullPath: "/settings", meta: {} });
    expect(state.auth.loadStoredSession).toHaveBeenCalledOnce();
    expect(result).toEqual({ path: "/login", query: { redirect: "/settings" } });
  });

  it("keeps authenticated navigation and public login behavior", async () => {
    expect(await state.guard!({ path: "/login", fullPath: "/login", meta: { public: true } })).toBe(true);
    state.token = "synthetic-token";
    state.auth.isAuthenticated = true;
    state.auth.currentUser = { id: 1 };
    state.auth.hasPermission.mockReturnValue(true);
    expect(await state.guard!({ path: "/settings", fullPath: "/settings", meta: { permission: "settings:write" } })).toBe(true);
    expect(await state.guard!({ path: "/login", fullPath: "/login", meta: { public: true } })).toBe("/media-sources");
  });

  it("redirects authenticated users without settings permission", async () => {
    state.token = "synthetic-token";
    state.auth.isAuthenticated = true;
    state.auth.currentUser = { id: 2 };
    state.auth.hasPermission.mockReturnValue(false);

    const result = await state.guard!({ path: "/settings", fullPath: "/settings", meta: { permission: "settings:write" } });

    expect(result).toBe("/media-sources");
    expect(state.message).toHaveBeenCalledOnce();
  });

  it("ends loading and reports a failure without auto-reloading or retrying requests", () => {
    routeLoading.value = true;
    state.error!();
    expect(routeLoading.value).toBe(false);
    expect(state.message).toHaveBeenCalledOnce();
  });
});
