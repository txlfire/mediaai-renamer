import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InventoryAdvicePage, InventoryDirectoryPage, InventorySummary } from "../api/cloudInventory";

const mocks = vi.hoisted(() => ({ jobs: vi.fn(), summary: vi.fn(), advice: vi.fn(), directories: vi.fn(), cancel: vi.fn(), keep: vi.fn(), clear: vi.fn(), confirm: vi.fn(), info: vi.fn(),
  route: { query: {} as Record<string, string> }, replace: vi.fn() }));
vi.mock("../api/cloudInventory", () => ({ fetchInventoryJobs: mocks.jobs, fetchInventorySummary: mocks.summary,
  fetchInventoryAdvice: mocks.advice, fetchInventoryDirectories: mocks.directories, cancelInventoryJob: mocks.cancel, setInventoryKeep: mocks.keep, clearInventoryCache: mocks.clear,
  inventoryRequestId: (value: unknown) => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value) ? value : "" }));
vi.mock("vue-router", () => ({ useRoute: () => mocks.route, useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => true }) }));
vi.mock("element-plus", () => ({ ElMessage: { info: mocks.info }, ElMessageBox: { confirm: mocks.confirm } }));
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
import CloudInventoryView from "./CloudInventoryView.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const jobId = "12345678-1234-1234-1234-123456789abc";
const otherId = "22345678-1234-1234-1234-123456789abc";
const summary: InventorySummary = { id: jobId, status: "completed", revision: 1, mode: "overview", approvedRoot: "/cloud/movies",
  selectedPaths: ["/cloud/movies/category"], excludedPaths: [], storedEntries: 2, receivedEntries: 2,
  completedDirectories: 1, workerSlotHeld: false, cancelRequested: false, reason: "scope_complete", freshness: "unknown",
  receivedBytes: 1000, directoryStates: { complete: 1 }, estimatedTotalEntries: null,
  limits: { entries: 10000, bytes: 33554432, directories: 200, depth: 0 }, writeAvailable: false, executionAvailable: false, scopeComplete: true,
  cacheState: "available", clearedAt: null, cacheClearAvailable: true, createdAt: 1792100000, updatedAt: 1792100100,
  storedBytes: 500, remainingEntries: 9998, remainingReceiveBytes: 33553432,
  budget: { usedRpc: 2, maxRpc: 10, remainingRpc: 8, status: "active", expiresAt: 1792100900, expired: false } };
const page: InventoryAdvicePage = { items: [{ id: 1, name: "[论坛推广] 电影 (2024)", relativePath: "category/movie", isDirectory: true,
  directoryComplete: true, decision: "keep", folderSuggestion: { status: "unselected", proposedName: null, reason: "explicit_media_folder_required" },
  size: 0, protection: null, evidenceLevel: "none", reasons: [], partialObservation: false, selected: false, actionAvailable: false },
  { id: 2, name: "更多资源.txt", relativePath: "category/更多资源.txt", isDirectory: false, directoryComplete: true,
    decision: "review", folderSuggestion: null, size: 10, protection: null, evidenceLevel: "low", reasons: ["promotional_phrase"],
    partialObservation: false, selected: false, actionAvailable: false }], hasMore: true, nextId: 2, networkAccess: false,
  jobRevision: 1, ruleVersion: "metadata-advice-v1", rulesHash: "synthetic", freshness: "unknown", contentReadBytes: 0,
  externalSubmission: false, writeAvailable: false, keepExceptionsPersisted: false };
type State = { selectedJobId: string; summary: InventorySummary | null; pageData: InventoryAdvicePage | null;
  folderTerms: string[]; selectedFolders: number[]; afterIds: number[]; activeTab: string; rows: InventoryAdvicePage["items"];
  errorText: string; dirty: boolean; loading: boolean; cancelling: boolean; detailVisible: boolean;
  loadCurrent: (analyze?: boolean) => Promise<void>; analyzePage: () => Promise<void>; changePage: (next: boolean) => void;
  cancelTask: () => Promise<void>; pollSummary: () => Promise<void>; clearCache: () => Promise<void>;
  toggleKeep: () => Promise<void>; detail: InventoryAdvicePage["items"][number] | null; clearing: boolean; keeping: boolean;
  scopeVisible: boolean; scopeTab: string; directoryPage: InventoryDirectoryPage | null; directoryAfterIds: number[];
  directoryError: string; openScope: () => void; loadDirectories: () => Promise<void>; changeDirectoryPage: (next: boolean) => void;
  reasonText: (reason: string | null | undefined) => string; exportPage: () => void;
  applySummary: (value: InventorySummary) => void; persistAdReviewRequest: (id: string) => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
const flush = async () => { for (let index = 0; index < 10; index++) await Promise.resolve(); await nextTick(); };

describe("local cloud inventory workspace", () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.route.query = {};
    mocks.replace.mockReset().mockImplementation(async ({ query }) => { mocks.route.query = query; });
    mocks.jobs.mockReset().mockResolvedValue({ items: [summary], total: 1, page: 1, pageSize: 20 });
    mocks.summary.mockReset().mockResolvedValue(summary);
    mocks.advice.mockReset().mockResolvedValue(page);
    mocks.cancel.mockReset().mockResolvedValue({ ...summary, status: "cancelled", revision: 2 });
    mocks.keep.mockReset().mockResolvedValue({ ...summary, revision: 2 });
    mocks.clear.mockReset().mockResolvedValue({ ...summary, cacheState: "cleared", cacheClearAvailable: false, storedEntries: 0, revision: 2 });
    mocks.directories.mockReset().mockResolvedValue({ items: [{ id: 1, relativePath: "category", depth: 0, status: "complete" }],
      nextId: 1, hasMore: true, jobRevision: 1, networkAccess: false });
    mocks.confirm.mockReset().mockResolvedValue("confirm");
    app = renderer.createApp(CloudInventoryView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await flush();
  });
  afterEach(() => { app.unmount(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("loads only local approved jobs and bounded advice", () => {
    expect(state.summary?.id).toBe(jobId);
    expect(mocks.advice).toHaveBeenCalledWith(jobId, { afterId: 0, limit: 100, folderTerms: [], folderEntryIds: [] });
    expect(state.rows).toHaveLength(2);
    state.activeTab = "review";
    expect(state.rows.map(item => item.id)).toEqual([2]);
  });
  it("accepts unchanged ad review markers without duplicated navigation", async () => {
    mocks.replace.mockResolvedValue({ type: 16 });
    await expect(state.persistAdReviewRequest("")).resolves.toBeUndefined();
    mocks.route.query = { ad_review_request_id: jobId, other: "keep" };
    await expect(state.persistAdReviewRequest(jobId)).resolves.toBeUndefined();
    expect(mocks.replace).not.toHaveBeenCalled();
  });
  it("rejects actual marker navigation failures and preserves unrelated query values", async () => {
    mocks.route.query = { other: "keep" };
    mocks.replace.mockResolvedValueOnce({ type: 4 });
    await expect(state.persistAdReviewRequest(jobId)).rejects.toThrow("ad_review_marker_not_saved");
    await state.persistAdReviewRequest(jobId);
    expect(mocks.replace).toHaveBeenLastCalledWith({ query: { other: "keep", ad_review_request_id: jobId } });
    await state.persistAdReviewRequest("");
    expect(mocks.route.query).toEqual({ other: "keep" });
  });
  it("localizes fixed remote failure codes without losing unknown fallback", () => {
    for (const code of ["not_found", "invalid_argument", "failed_precondition", "unimplemented", "internal", "data_loss", "unknown"]) {
      expect(state.reasonText(code)).not.toBe(code);
      expect(state.reasonText(code)).toMatch(/CD2/);
    }
    expect(state.reasonText("worker_failed")).toContain("日志");
    expect(state.reasonText(null)).toBe("");
    expect(state.reasonText("future-local-reason")).toBe("future-local-reason");
  });
  it("loads coverage only on explicit tab activation and uses local keyset cursors", async () => {
    state.openScope(); await flush();
    expect(mocks.directories).not.toHaveBeenCalled();
    state.scopeTab = "budget"; await flush();
    expect(mocks.directories).not.toHaveBeenCalled();
    state.scopeTab = "coverage"; await flush();
    expect(mocks.directories).toHaveBeenLastCalledWith(jobId, 0);
    state.changeDirectoryPage(true); await flush();
    expect(mocks.directories).toHaveBeenLastCalledWith(jobId, 1);
    expect(state.directoryAfterIds).toEqual([0, 1]);
  });
  it("ignores late coverage after closing or switching and never queries cleared observations", async () => {
    state.openScope();
    let resolve!: (value: InventoryDirectoryPage) => void;
    mocks.directories.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const pending = state.loadDirectories(); await flush();
    state.scopeVisible = false; await flush();
    resolve({ items: [], nextId: 99, hasMore: false, jobRevision: 1, networkAccess: false }); await pending;
    expect(state.directoryPage).toBe(null);
    state.summary = { ...summary, cacheState: "cleared" };
    state.openScope(); state.scopeTab = "coverage"; await flush();
    expect(mocks.directories).toHaveBeenCalledTimes(1);
  });
  it("shows local coverage errors without automatic retries", async () => {
    mocks.directories.mockRejectedValueOnce(new Error("synthetic"));
    state.openScope(); state.scopeTab = "coverage"; await flush();
    expect(state.directoryPage).toBe(null);
    expect(state.directoryError).toBeTruthy();
    await vi.advanceTimersByTimeAsync(5000);
    expect(mocks.directories).toHaveBeenCalledTimes(1);
  });
  it("requires media-folder confirmation and marks changed rules stale", async () => {
    state.folderTerms = ["论坛推广"];
    state.selectedFolders = [1];
    expect(state.dirty).toBe(true);
    await state.analyzePage();
    expect(mocks.confirm).toHaveBeenCalledOnce();
    expect(mocks.advice).toHaveBeenLastCalledWith(jobId, expect.objectContaining({ folderEntryIds: [1], folderTerms: ["论坛推广"] }));
    expect(state.dirty).toBe(false);
    state.folderTerms = ["另一个词"];
    expect(state.dirty).toBe(true);
  });
  it("does not analyze when confirmation is cancelled", async () => {
    state.folderTerms = ["论坛推广"]; state.selectedFolders = [1];
    mocks.confirm.mockRejectedValueOnce("cancel");
    mocks.advice.mockClear();
    await state.analyzePage();
    expect(mocks.advice).not.toHaveBeenCalled();
  });
  it("keeps observations editable when a draft term is invalid or analysis fails", async () => {
    state.folderTerms = ["x"];
    mocks.advice.mockClear();
    await state.analyzePage();
    expect(mocks.advice).not.toHaveBeenCalled();
    expect(state.errorText).toBeTruthy();
    expect(state.pageData?.items).toHaveLength(2);
    state.folderTerms = ["合法词"];
    mocks.advice.mockRejectedValueOnce(new Error("synthetic"));
    await state.analyzePage();
    expect(state.pageData?.items).toHaveLength(2);
    expect(state.dirty).toBe(true);
  });
  it("uses keyset cursors without growing selected scope across pages", async () => {
    state.selectedFolders = [1];
    state.changePage(true); await flush();
    expect(state.afterIds).toEqual([0, 2]);
    expect(state.selectedFolders).toEqual([]);
    expect(mocks.advice).toHaveBeenLastCalledWith(jobId, expect.objectContaining({ afterId: 2, folderEntryIds: [] }));
    state.changePage(false); await flush();
    expect(state.afterIds).toEqual([0]);
  });
  it("ignores a late page response after switching jobs", async () => {
    let resolve!: (value: InventoryAdvicePage) => void;
    mocks.advice.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const pending = state.loadCurrent(); await flush();
    state.selectedJobId = otherId; await flush();
    resolve({ ...page, items: [] }); await pending;
    expect(state.pageData?.items).toHaveLength(2);
  });
  it("does not apply an old response after unmount", async () => {
    let resolve!: (value: InventoryAdvicePage) => void;
    mocks.advice.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const pending = state.loadCurrent(); await flush();
    app.unmount();
    resolve({ ...page, items: [] }); await pending;
    expect(state.pageData?.items).toHaveLength(2);
  });
  it("polls summary only and does not fetch another directory page", async () => {
    state.summary = { ...summary, status: "running", workerSlotHeld: true };
    mocks.summary.mockResolvedValue(state.summary);
    mocks.advice.mockClear(); mocks.summary.mockClear();
    await state.pollSummary();
    await vi.advanceTimersByTimeAsync(1500);
    expect(mocks.summary).toHaveBeenCalledTimes(2);
    expect(mocks.advice).not.toHaveBeenCalled();
  });
  it("stops polling when local summary observation fails", async () => {
    state.summary = { ...summary, status: "running", workerSlotHeld: true };
    mocks.summary.mockRejectedValueOnce(new Error("synthetic"));
    await state.pollSummary();
    expect(state.errorText).toBeTruthy();
    await vi.advanceTimersByTimeAsync(3000);
    expect(mocks.summary).toHaveBeenCalledTimes(2);
  });
  it("cancel requires confirmation and keeps the returned pending state", async () => {
    state.summary = { ...summary, status: "running", workerSlotHeld: true };
    mocks.cancel.mockResolvedValueOnce({ ...state.summary, revision: 2, cancelRequested: true });
    await state.cancelTask();
    expect(mocks.cancel).toHaveBeenCalledWith(jobId);
    expect(state.summary?.cancelRequested).toBe(true);
    expect(state.summary?.workerSlotHeld).toBe(true);
    expect(mocks.info).toHaveBeenCalledOnce();
  });
  it("saves a current-entry keep and reloads local advice", async () => {
    state.detail = page.items[1]!;
    await state.toggleKeep();
    expect(mocks.keep).toHaveBeenCalledWith(jobId, 2, true, 1);
    expect(mocks.advice).toHaveBeenCalledTimes(2);
    expect(state.keeping).toBe(false);
  });
  it("does not read or export old observations after clearing", async () => {
    await state.clearCache();
    expect(mocks.clear).toHaveBeenCalledWith(jobId, 1);
    expect(state.summary?.cacheState).toBe("cleared");
    expect(state.rows).toHaveLength(0);
    mocks.summary.mockResolvedValue(state.summary);
    mocks.advice.mockClear();
    await state.loadCurrent();
    expect(mocks.advice).not.toHaveBeenCalled();
    expect(state.pageData).toBe(null);
  });
  it.each(["clearing", "cleared"] as const)("invalidates observed rows and disables actions when polling reports %s", async cacheState => {
    state.summary = { ...summary, status: "running", workerSlotHeld: true };
    state.folderTerms = ["论坛推广"]; state.selectedFolders = [1];
    state.detail = page.items[0]!; state.detailVisible = true;
    state.openScope(); state.scopeTab = "coverage"; await flush();
    mocks.summary.mockResolvedValueOnce({ ...summary, status: "cancelled", cacheState, revision: 3,
      storedEntries: 0, workerSlotHeld: false });
    await state.pollSummary();
    expect(state.summary?.cacheState).toBe(cacheState);
    expect(state.pageData).toBe(null);
    expect(state.rows).toHaveLength(0);
    expect(state.directoryPage).toBe(null);
    expect(state.detailVisible).toBe(false);
    expect(state.detail).toBe(null);
    expect(state.selectedFolders).toEqual([]);
    expect(state.folderTerms).toEqual([]);
    expect(state.dirty).toBe(false);
    const download = vi.fn();
    vi.stubGlobal("document", { createElement: () => { download(); throw new Error("旧观察不得导出"); } });
    // 即使旧观察引用被意外保留，入口本身也必须拒绝继续操作。
    state.pageData = page; state.detail = page.items[0]!;
    mocks.advice.mockClear(); mocks.keep.mockClear(); mocks.confirm.mockClear();
    state.exportPage(); await state.analyzePage(); await state.toggleKeep();
    expect(download).not.toHaveBeenCalled();
    expect(mocks.advice).not.toHaveBeenCalled();
    expect(mocks.keep).not.toHaveBeenCalled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });
  it("ignores advice and coverage received after cache invalidation without sticking loading", async () => {
    let resolveAdvice!: (value: InventoryAdvicePage) => void, resolveDirectories!: (value: InventoryDirectoryPage) => void;
    mocks.advice.mockReturnValueOnce(new Promise(done => { resolveAdvice = done; }));
    const loading = state.loadCurrent(true); await flush();
    state.openScope();
    mocks.directories.mockReturnValueOnce(new Promise(done => { resolveDirectories = done; }));
    const directories = state.loadDirectories(); await flush();
    state.applySummary({ ...summary, revision: 3, cacheState: "cleared", storedEntries: 0 });
    resolveAdvice(page); resolveDirectories({ items: [], hasMore: false, nextId: 1, jobRevision: 1, networkAccess: false });
    await loading; await directories;
    expect(state.pageData).toBe(null);
    expect(state.directoryPage).toBe(null);
    expect(state.loading).toBe(false);
    mocks.summary.mockResolvedValueOnce(summary);
    mocks.advice.mockClear();
    await state.loadCurrent();
    expect(state.summary?.cacheState).toBe("cleared");
    expect(mocks.advice).not.toHaveBeenCalled();
  });
  it("does not send a keep after its confirmation outlives cache invalidation", async () => {
    let confirm!: (value: string) => void;
    mocks.confirm.mockReturnValueOnce(new Promise(done => { confirm = done; }));
    state.detail = page.items[0]!;
    const pending = state.toggleKeep(); await flush();
    state.applySummary({ ...summary, revision: 3, cacheState: "cleared", storedEntries: 0 });
    confirm("confirm"); await pending;
    expect(mocks.keep).not.toHaveBeenCalled();
    expect(state.keeping).toBe(false);
  });
  it("ignores a late advice failure after observations have been invalidated", async () => {
    let reject!: (reason: Error) => void;
    mocks.advice.mockReturnValueOnce(new Promise((_done, fail) => { reject = fail; }));
    const pending = state.loadCurrent(true); await flush();
    state.applySummary({ ...summary, revision: 3, cacheState: "cleared", storedEntries: 0 });
    reject(new Error("synthetic stale request")); await pending;
    expect(state.errorText).toBe("");
    expect(state.pageData).toBe(null);
    expect(state.loading).toBe(false);
  });
  it("continues bounded local cleanup in sequence and stops on failures", async () => {
    mocks.clear.mockResolvedValueOnce({ ...summary, cacheState: "clearing", storedEntries: 1, revision: 2 });
    const pending = state.clearCache(); await flush();
    expect(state.clearing).toBe(true);
    expect(mocks.clear).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(100); await pending;
    expect(mocks.clear).toHaveBeenNthCalledWith(2, jobId, 2);
    state.summary = summary;
    mocks.clear.mockRejectedValueOnce(new Error("synthetic"));
    await state.clearCache();
    expect(state.errorText).toBeTruthy();
    expect(state.clearing).toBe(false);
    expect(mocks.clear).toHaveBeenCalledTimes(3);
  });
  it("does not maintain unconfirmed, worker-held or switched tasks", async () => {
    state.summary = { ...summary, cacheClearAvailable: false, workerSlotHeld: true };
    await state.clearCache();
    expect(mocks.clear).not.toHaveBeenCalled();
    state.summary = summary;
    mocks.confirm.mockRejectedValueOnce("cancel");
    await state.clearCache();
    expect(mocks.clear).not.toHaveBeenCalled();
    let resolve!: (value: InventorySummary) => void;
    mocks.clear.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const pending = state.clearCache(); await flush();
    state.selectedJobId = otherId; await flush();
    resolve({ ...summary, cacheState: "clearing", revision: 2 }); await pending;
    await vi.advanceTimersByTimeAsync(1000);
    expect(mocks.clear).toHaveBeenCalledTimes(1);
    expect(state.summary?.cacheState).toBe("available");
  });
  it("does not let an old cleanup reset a new operation after switching away and back", async () => {
    let resolveOld!: (value: InventorySummary) => void, resolveNew!: (value: InventorySummary) => void;
    mocks.clear.mockReturnValueOnce(new Promise(done => { resolveOld = done; }));
    const old = state.clearCache(); await flush();
    state.selectedJobId = otherId; await flush();
    state.selectedJobId = jobId; await flush();
    mocks.clear.mockReturnValueOnce(new Promise(done => { resolveNew = done; }));
    const current = state.clearCache(); await flush();
    expect(state.clearing).toBe(true);
    resolveOld({ ...summary, cacheState: "cleared", revision: 2 }); await old;
    expect(state.clearing).toBe(true);
    resolveNew({ ...summary, cacheState: "cleared", revision: 2 }); await current;
    expect(state.clearing).toBe(false);
  });
  it("keeps old observations visible but refuses new analysis after a path revision", async () => {
    const before = mocks.advice.mock.calls.length;
    state.summary = { ...summary, pathCurrent: false };
    state.pageData = page; state.folderTerms = ["推广"]; state.selectedFolders = [1];
    await state.analyzePage();
    expect(mocks.advice).toHaveBeenCalledTimes(before);
    expect(state.pageData?.items).toEqual(page.items);
    expect(mocks.jobs).toHaveBeenCalledTimes(1);
  });
});
