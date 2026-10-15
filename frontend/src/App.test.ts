import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  route: { meta: {} as { public?: boolean }, matched: [] as object[] },
  appStore: { resolvedTheme: "light", health: null as null | { version: string }, connectionState: "loading",
    loadSidebarCollapsed: vi.fn(), loadThemeMode: vi.fn(), refreshHealth: vi.fn() },
}));
vi.mock("./stores/app", () => ({ useAppStore: () => reactive(mocks.appStore) }));
vi.mock("./stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => false, currentUser: null, loadStoredSession: vi.fn() }) }));
vi.mock("vue-router", () => ({ useRoute: () => reactive(mocks.route), useRouter: () => ({}) }));
vi.mock("./router", () => ({ routeLoading: false }));
vi.mock("./components/LogDrawer.vue", () => ({ default: { render: () => null } }));
import App from "./App.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }),
  createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });

describe("initial route and version display", () => {
  it("does not show private workspace before route resolution or invent a version", async () => {
    vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn(),
      matchMedia: () => ({ addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
    const app = renderer.createApp(App);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    try {
      const state = (app._instance as unknown as { setupState: { isPublicPage: boolean; versionText: string } }).setupState;
      expect(state.isPublicPage).toBe(true);
      expect(state.versionText).toBe("版本待确认");
      reactive(mocks.route).matched = [{}];
      await nextTick();
      expect(state.isPublicPage).toBe(false);
      reactive(mocks.route).meta.public = true;
      reactive(mocks.appStore).health = { version: "1.0.1" };
      await nextTick();
      expect(state.isPublicPage).toBe(true);
      expect(state.versionText).toBe("v1.0.1");
    } finally {
      app.unmount();
      vi.unstubAllGlobals();
    }
  });
});
