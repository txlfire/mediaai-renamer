import { createRenderer, nextTick, ssrContextKey } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  page: vi.fn(), jobs: vi.fn(), error: vi.fn(),
  store: { mediaFiles: [] as { id: number }[], mediaSources: [], scanJobs: [], loadMediaSources: vi.fn() },
}));
vi.mock("../api/client", () => ({ fetchMediaFilesPage: mocks.page, fetchScanJobs: mocks.jobs }));
vi.mock("../stores/media", () => ({ useMediaStore: () => mocks.store }));
vi.mock("vue-router", () => ({ useRoute: () => ({ query: {} }) }));
vi.mock("element-plus", () => ({ ElMessage: { error: mocks.error } }));
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/FullscreenTablePanel.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TablePagination.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TextCell.vue", () => ({ default: { render: () => null } }));

import ScanResultsView from "./ScanResultsView.vue";
import { usePaginationStore } from "../stores/pagination";

type HostNode = { children: HostNode[] };
const renderer = createRenderer<HostNode, HostNode>({
  createElement: () => ({ children: [] }), createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null,
});
type ViewState = {
  selectedScanJobId: number | undefined;
  mediaFilesLoading: boolean;
  mediaFileTotal: number;
  queryMediaFiles: (reset?: boolean) => Promise<void>;
  clearMediaFiles: () => void;
};
let app: ReturnType<typeof renderer.createApp>;
let state: ViewState;

function deferredPage() {
  let resolve!: (value: { items: { id: number }[]; total: number; page: number; page_size: number }) => void;
  const promise = new Promise<Parameters<typeof resolve>[0]>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("server scan-result request isolation", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    mocks.store.mediaFiles = [];
    mocks.store.mediaSources = [];
    mocks.store.scanJobs = [];
    mocks.store.loadMediaSources.mockResolvedValue(undefined);
    mocks.page.mockReset();
    mocks.error.mockReset();
    app = renderer.createApp(ScanResultsView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: ViewState }).setupState;
    await nextTick();
  });

  afterEach(() => { app.unmount(); });

  it("keeps the newest page when responses arrive out of order", async () => {
    const first = deferredPage(), second = deferredPage();
    mocks.page.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    state.selectedScanJobId = 1;
    const older = state.queryMediaFiles();
    usePaginationStore().setPage("scan-results", 2);
    const newer = state.queryMediaFiles(false);
    second.resolve({ items: [{ id: 11 }], total: 1000, page: 2, page_size: 10 });
    await newer;
    first.resolve({ items: [{ id: 1 }], total: 1000, page: 1, page_size: 10 });
    await older;
    expect(mocks.store.mediaFiles).toEqual([{ id: 11 }]);
    expect(usePaginationStore().getState("scan-results").currentPage).toBe(2);
    expect(state.mediaFileTotal).toBe(1000);
  });

  it("clearing a filter prevents an old response from repopulating the table", async () => {
    const response = deferredPage();
    mocks.page.mockReturnValueOnce(response.promise);
    state.selectedScanJobId = 1;
    const request = state.queryMediaFiles();
    state.clearMediaFiles();
    response.resolve({ items: [{ id: 1 }], total: 1000, page: 1, page_size: 10 });
    await request;
    expect(mocks.store.mediaFiles).toEqual([]);
    expect(state.mediaFileTotal).toBe(0);
    expect(state.mediaFilesLoading).toBe(false);
  });

  it("unmount prevents a late response from changing shared rows", async () => {
    const response = deferredPage();
    mocks.page.mockReturnValueOnce(response.promise);
    state.selectedScanJobId = 1;
    const request = state.queryMediaFiles();
    app.unmount();
    response.resolve({ items: [{ id: 1 }], total: 1000, page: 1, page_size: 10 });
    await request;
    expect(mocks.store.mediaFiles).toEqual([]);
  });

  it("normalizes old all-rows settings and honors the server-clamped page", async () => {
    usePaginationStore().setPageSize("scan-results", 0);
    mocks.page.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    state.selectedScanJobId = 1;
    await state.queryMediaFiles();
    expect(mocks.page.mock.calls[0][0].page_size).toBe(50);
    expect(usePaginationStore().getState("scan-results").pageSize).toBe(50);
    expect(usePaginationStore().getState("scan-results").currentPage).toBe(1);
  });
});
