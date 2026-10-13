import { createRenderer, nextTick, ssrContextKey } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ page: vi.fn(), error: vi.fn(), create: vi.fn(), success: vi.fn(), remove: vi.fn(), toggle: vi.fn() }));
vi.mock("../api/client", () => ({ fetchMediaSourcesPage: mocks.page, bulkDeleteMediaSources: vi.fn(),
  createMediaSource: mocks.create, deleteMediaSource: mocks.remove, setMediaSourceEnabled: mocks.toggle, updateMediaSource: vi.fn(),
  fetchLocalDirectories: vi.fn(), testMediaSourceConnection: vi.fn(), testMediaSourceConnectionPayload: vi.fn(),
  verifyWebDavConditionalMove: vi.fn() }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => true }) }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("element-plus", () => ({ ElMessage: { error: mocks.error, success: mocks.success }, ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined) } }));
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TablePagination.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TextCell.vue", () => ({ default: { render: () => null } }));
import MediaSourcesView from "./MediaSourcesView.vue";
import { usePaginationStore } from "../stores/pagination";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }),
  createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
type Result = { items: { id: number }[]; total: number; page: number; page_size: number };
type State = { pagedMediaSources: Result["items"]; mediaSourceTotal: number; sourcesLoading: boolean;
  selectedRows: { id: number }[]; loadSources: () => Promise<void>;
  form: { name: string; path: string }; submitSource: () => Promise<void>;
  cleanupDialog: { visible: boolean }; removeSource: (row: never) => Promise<void>; toggleSource: (row: never) => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
function deferred() {
  let resolve!: (result: Result) => void;
  const promise = new Promise<Result>((done) => { resolve = done; });
  return { promise, resolve };
}
describe("media-source server pages", () => {
  beforeEach(async () => {
    setActivePinia(createPinia()); mocks.error.mockReset();
    mocks.create.mockReset().mockResolvedValue({ id: 1 }); mocks.success.mockReset();
    mocks.page.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 });
    app = renderer.createApp(MediaSourcesView);
    app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await nextTick();
  });
  afterEach(() => app.unmount());
  it("keeps latest rows and server totals when requests finish out of order", async () => {
    const first = deferred(), second = deferred();
    mocks.page.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const older = state.loadSources(); usePaginationStore().setPage("media-sources", 2);
    const newer = state.loadSources();
    second.resolve({ items: [{ id: 11 }], total: 1000, page: 2, page_size: 10 }); await newer;
    first.resolve({ items: [{ id: 1 }], total: 1000, page: 1, page_size: 10 }); await older;
    expect(state.pagedMediaSources).toEqual([{ id: 11 }]); expect(state.mediaSourceTotal).toBe(1000);
  });
  it("clears selection when changing pages and normalizes all-rows", async () => {
    state.selectedRows = [{ id: 1 }]; usePaginationStore().setPageSize("media-sources", 0);
    await state.loadSources();
    expect(state.selectedRows).toEqual([]);
    expect(mocks.page.mock.calls[mocks.page.mock.calls.length - 1][0].page_size).toBe(50);
  });
  it("does not mutate rows after unmount", async () => {
    const pending = deferred(); mocks.page.mockReturnValueOnce(pending.promise);
    const request = state.loadSources(); app.unmount();
    pending.resolve({ items: [{ id: 1 }], total: 1, page: 1, page_size: 10 }); await request;
    expect(state.pagedMediaSources).toEqual([]);
  });
  it("clears stale rows on failure", async () => {
    state.pagedMediaSources = [{ id: 1 }]; state.mediaSourceTotal = 100;
    mocks.page.mockRejectedValue(new Error("failed")); await state.loadSources();
    expect(state.pagedMediaSources).toEqual([]); expect(state.mediaSourceTotal).toBe(0);
    expect(state.sourcesLoading).toBe(false);
  });
  it("save refreshes only a bounded page and keeps failed input", async () => {
    state.form.name = "synthetic"; state.form.path = "/synthetic";
    await state.submitSource();
    expect(mocks.create).toHaveBeenCalledOnce();
    expect(mocks.page.mock.calls[mocks.page.mock.calls.length - 1][0].page_size).toBe(10);
    expect(state.form.name).toBe("");
    state.form.name = "keep draft"; state.form.path = "/synthetic";
    mocks.create.mockRejectedValue(new Error("failed"));
    await state.submitSource();
    expect(state.form.name).toBe("keep draft"); expect(mocks.error).toHaveBeenCalled();
  });
  it("reports failed writes and closes cleanup progress", async () => {
    vi.stubGlobal("window", { setTimeout: globalThis.setTimeout });
    mocks.remove.mockRejectedValue(new Error("delete failed"));
    await state.removeSource({ id: 1, enabled: true } as never);
    expect(state.cleanupDialog.visible).toBe(false);
    expect(mocks.error).toHaveBeenCalledWith("delete failed");
    mocks.toggle.mockRejectedValue(new Error("toggle failed"));
    await state.toggleSource({ id: 1, enabled: true } as never);
    expect(mocks.error).toHaveBeenCalledWith("toggle failed");
    vi.unstubAllGlobals();
  });
});
