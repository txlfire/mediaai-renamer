import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ page: vi.fn(), error: vi.fn(), observe: vi.fn(),
  route: { query: {} as Record<string, string> },
  store: { mediaSources: [{ id: 1, name: "synthetic", enabled: true }], loadMediaSources: vi.fn(),
    scanModeSuggestion: null, scanRefreshFailed: false } }));
vi.mock("../api/client", () => ({ fetchScanJobsPage: mocks.page, fetchScanJob: vi.fn(),
  fetchMediaSource: vi.fn().mockResolvedValue({ id: 1, name: "synthetic", enabled: true }),
  fetchScanModeSuggestion: vi.fn().mockResolvedValue({ recommended_mode: "full" }) }));
vi.mock("../utils/scanTaskObserver", () => ({ isScanActive: (status: string) => ["queued", "running"].includes(status),
  createScanTaskObserver: () => ({ start: mocks.observe, stop: vi.fn() }) }));
vi.mock("../stores/media", () => ({ useMediaStore: () => mocks.store }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => true }) }));
vi.mock("vue-router", async () => {
  const { reactive } = await import("vue");
  return { useRoute: () => reactive(mocks.route), useRouter: () => ({ push: vi.fn() }) };
});
vi.mock("element-plus", () => ({ ElMessage: { error: mocks.error }, ElMessageBox: { confirm: vi.fn() } }));
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/OperationProgressLog.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/OperationLogDrawer.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TablePagination.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TextCell.vue", () => ({ default: { render: () => null } }));
import ScanJobsView from "./ScanJobsView.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
type Page = { items: { id: number; media_source_id: number; status: string }[]; total: number; page: number; page_size: number };
const empty: Page = { items: [], total: 0, page: 1, page_size: 10 };
type State = { selectedSourceId?: number; pagedScanJobs: Page["items"]; canStartScan: boolean;
  queryScanJobs: (reset?: boolean) => Promise<void>; refreshActiveScan: () => Promise<void>; resetScanJobs: () => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;

describe("scan-job paging and independent activity", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    mocks.route.query = {};
    vi.clearAllMocks();
    mocks.page.mockReset().mockResolvedValue(empty);
    mocks.store.loadMediaSources.mockResolvedValue(undefined);
    app = renderer.createApp(ScanJobsView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await nextTick(); await state.queryScanJobs();
    state.selectedSourceId = 1;
    await nextTick(); await state.refreshActiveScan();
  });
  afterEach(() => app.unmount());

  it("blocks start for a running job outside the historical page", async () => {
    mocks.page.mockImplementation((filters) => Promise.resolve(filters.active_only
      ? { ...empty, total: 1, items: [{ id: 1, media_source_id: 1, status: "running" }] }
      : { ...empty, total: 100, items: [{ id: 100, media_source_id: 1, status: "completed" }] }));
    await state.queryScanJobs();
    expect(state.pagedScanJobs.map((job) => job.id)).toEqual([100]);
    expect(state.canStartScan).toBe(false);
    expect(mocks.observe).toHaveBeenCalledWith(1);
  });
  it("allows start only after source and independent activity are confirmed", async () => {
    await nextTick(); await state.refreshActiveScan();
    expect(state.canStartScan).toBe(true);
  });
  it("keeps start disabled when activity cannot be checked", async () => {
    mocks.page.mockRejectedValue(new Error("synthetic read failure"));
    await state.refreshActiveScan();
    expect(state.canStartScan).toBe(false);
    expect(mocks.error).toHaveBeenCalled();
  });
  it("rejects an old historical response after reset", async () => {
    let resolve!: (page: Page) => void;
    mocks.page.mockReturnValueOnce(new Promise<Page>((done) => { resolve = done; }));
    const request = state.queryScanJobs();
    await state.resetScanJobs(); await nextTick();
    resolve({ ...empty, total: 100, items: [{ id: 100, media_source_id: 1, status: "completed" }] });
    await request;
    expect(state.pagedScanJobs).toEqual([]);
    expect(state.canStartScan).toBe(false);
  });
  it("ignores a response after leaving the page", async () => {
    let resolve!: (page: Page) => void;
    mocks.page.mockReturnValueOnce(new Promise<Page>((done) => { resolve = done; }));
    const request = state.queryScanJobs();
    app.unmount();
    resolve({ ...empty, total: 100, items: [{ id: 100, media_source_id: 1, status: "completed" }] });
    await request;
    expect(state.pagedScanJobs).toEqual([]);
  });
  it("requeries when the same mounted route changes its source", async () => {
    reactive(mocks.route).query = { media_source_id: "2" };
    await nextTick(); await nextTick(); await nextTick();
    expect(state.selectedSourceId).toBe(2);
    expect(mocks.page).toHaveBeenCalledWith(expect.objectContaining({ media_source_id: 2 }));
  });
});
