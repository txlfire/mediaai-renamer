import { createRenderer, nextTick, ssrContextKey } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ page: vi.fn(), remotePage: vi.fn(), auditPage: vi.fn(), capacity: vi.fn(), error: vi.fn(),
  store: { mediaSources: [], loadMediaSources: vi.fn() } }));
vi.mock("../api/client", () => ({ fetchTasksPage: mocks.page, archiveTask: vi.fn(),
  fetchRemoteOperations: mocks.remotePage, recoverRemoteOperation: vi.fn(),
  fetchFileActionAuditIntents: mocks.auditPage, fetchAuditIntentCapacity: mocks.capacity,
  closeFileActionAuditIntent: vi.fn() }));
vi.mock("../stores/media", () => ({ useMediaStore: () => mocks.store }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => true }) }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("element-plus", () => ({ ElMessage: { error: mocks.error }, ElMessageBox: {} }));
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/OperationLogDrawer.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TablePagination.vue", () => ({ default: { render: () => null } }));
vi.mock("../components/TextCell.vue", () => ({ default: { render: () => null } }));

import TaskGovernanceView from "./TaskGovernanceView.vue";
import { usePaginationStore } from "../stores/pagination";

type HostNode = { children: HostNode[] };
const renderer = createRenderer<HostNode, HostNode>({
  createElement: () => ({ children: [] }), createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null,
});
type Result = { items: { task_id: number }[]; total: number; page: number; page_size: number };
type ViewState = { tasks: Result["items"]; taskTotal: number; loading: boolean;
  remoteOperations: { id: number }[]; remoteTotal: number;
  auditIntents: { id: number }[]; auditIntentTotal: number; auditIntentCapacity: { warning: boolean } | null;
  loadTasks: () => Promise<void>; loadRemoteOperations: () => Promise<void>;
  queryTasks: () => void; handleTabChange: (tab: string) => void };
let app: ReturnType<typeof renderer.createApp>;
let state: ViewState;
function deferred() {
  let resolve!: (value: Result) => void;
  const promise = new Promise<Result>((done) => { resolve = done; });
  return { promise, resolve };
}
describe("task-governance server pagination", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    mocks.page.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 });
    mocks.remotePage.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10, pages: 1 });
    mocks.error.mockReset();
    mocks.auditPage.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10, pages: 1 });
    mocks.capacity.mockReset().mockResolvedValue({ records: 0, max_records: 100, approx_bytes: 0,
      max_bytes: 1024, usage_ratio: 0, warning: false, capacity_exhausted: false,
      pending: 0, review_required: 0, manually_closed: 0 });
    mocks.store.loadMediaSources.mockResolvedValue(undefined);
    app = renderer.createApp(TaskGovernanceView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: ViewState }).setupState;
    await nextTick(); await nextTick();
  });
  afterEach(() => { app.unmount(); });
  it("uses server totals and ignores an older page response", async () => {
    const older = deferred(), newer = deferred();
    mocks.page.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const first = state.loadTasks();
    usePaginationStore().setPage("task-governance", 2);
    const second = state.loadTasks();
    newer.resolve({ items: [{ task_id: 11 }], total: 1000, page: 2, page_size: 10 }); await second;
    older.resolve({ items: [{ task_id: 1 }], total: 1000, page: 1, page_size: 10 }); await first;
    expect(state.tasks).toEqual([{ task_id: 11 }]);
    expect(state.taskTotal).toBe(1000);
    expect(usePaginationStore().getState("task-governance").currentPage).toBe(2);
  });
  it("normalizes all-rows and honors server-clamped pages", async () => {
    usePaginationStore().setPageSize("task-governance", 0);
    mocks.page.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    await state.loadTasks();
    expect(mocks.page.mock.calls[mocks.page.mock.calls.length - 1]?.[0].page_size).toBe(50);
    expect(usePaginationStore().getState("task-governance").pageSize).toBe(50);
  });
  it("unmount rejects late responses", async () => {
    const pending = deferred(); mocks.page.mockReturnValueOnce(pending.promise);
    const request = state.loadTasks(); app.unmount();
    pending.resolve({ items: [{ task_id: 1 }], total: 100, page: 1, page_size: 10 }); await request;
    expect(state.tasks).toEqual([]);
    expect(state.taskTotal).toBe(0);
  });
  it("query resets the page and failure clears stale rows", async () => {
    usePaginationStore().setPage("task-governance", 5);
    state.queryTasks(); await nextTick();
    expect(mocks.page.mock.calls[mocks.page.mock.calls.length - 1]?.[0].page).toBe(1);
    state.tasks = [{ task_id: 1 }]; state.taskTotal = 100;
    mocks.page.mockRejectedValue(new Error("failed"));
    await state.loadTasks();
    expect(state.tasks).toEqual([]); expect(state.taskTotal).toBe(0); expect(state.loading).toBe(false);
  });
  it("loads audit intent capacity and rows when the tab is opened", async () => {
    mocks.auditPage.mockResolvedValue({ items: [{ id: 7 }], total: 1, page: 1, page_size: 10, pages: 1 });
    mocks.capacity.mockResolvedValue({ records: 80, max_records: 100, approx_bytes: 100,
      max_bytes: 1024, usage_ratio: 0.8, warning: true, capacity_exhausted: false,
      pending: 79, review_required: 1, manually_closed: 0 });
    state.handleTabChange("auditIntents");
    await nextTick(); await nextTick();
    expect(state.auditIntents).toEqual([{ id: 7 }]);
    expect(state.auditIntentTotal).toBe(1);
    expect(state.auditIntentCapacity?.warning).toBe(true);
  });
  it("ignores an older WebDAV operation response and applies the server page", async () => {
    const older = deferred(), newer = deferred();
    mocks.remotePage.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const first = state.loadRemoteOperations();
    usePaginationStore().setPage("webdav-recovery", 3);
    const second = state.loadRemoteOperations();
    newer.resolve({ items: [{ task_id: 22 }], total: 30, page: 2, page_size: 10 }); await second;
    older.resolve({ items: [{ task_id: 11 }], total: 30, page: 1, page_size: 10 }); await first;
    expect(state.remoteOperations).toEqual([{ task_id: 22 }]);
    expect(state.remoteTotal).toBe(30);
    expect(usePaginationStore().getState("webdav-recovery").currentPage).toBe(2);
  });
});
