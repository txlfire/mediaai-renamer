import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiParseCandidate, MetadataMatchResult, PendingMoveTask, RenameOperation, RenameRollbackPlan, RenamePreview, RenamePreviewFilters, RenamePreviewPage, RenameSelectionItem } from "../api/client";

const mocks = vi.hoisted(() => ({
  page: vi.fn(), selection: vi.fn(), files: vi.fn(), pending: vi.fn(),
  confirm: vi.fn(), error: vi.fn(), warning: vi.fn(), match: vi.fn(),
  history: vi.fn(), route: { query: {} as Record<string, number | string> }, replace: vi.fn(),
  success: vi.fn(), rollbackExecute: vi.fn(),
  applyMetadata: vi.fn(), applyAi: vi.fn(),
  operation: { currentOperation: null as RenameOperation | null, currentRollbackPlan: null as RenameRollbackPlan | null,
    stopExecutionPolling: vi.fn(), executeRollbackPlan: vi.fn(), rollbackErrorMessage: "" },
}));
vi.mock("../api/client", () => ({ fetchRenamePreviewsPage: mocks.page, fetchRenamePreviewSelection: mocks.selection,
  fetchMediaFilesPage: mocks.files, fetchPendingFilesPage: mocks.pending, fetchAiReadiness: mocks.history }));
vi.mock("element-plus", () => ({ ElMessage: { error: mocks.error, warning: mocks.warning, success: mocks.success },
  ElMessageBox: { confirm: mocks.confirm } }));
vi.mock("vue-router", async () => {
  const { reactive } = await import("vue");
  return { useRoute: () => reactive(mocks.route), useRouter: () => ({ replace: mocks.replace }) };
});
vi.mock("../components/ListPageLayout.vue", () => ({ default: { render: () => null } }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: () => true }) }));
vi.mock("../stores/media", () => ({ useMediaStore: () => ({ mediaFiles: [] }) }));
let operationStore: typeof mocks.operation;
vi.mock("../stores/renameOperation", () => ({ useRenameOperationStore: () => operationStore }));
let preview: { filters: RenamePreviewFilters; previews: RenamePreview[]; loading: boolean; matchMetadataBatch: typeof mocks.match;
  applyMetadataCandidate: typeof mocks.applyMetadata; applyAiCandidate: typeof mocks.applyAi };
let pendingStore: { pendingFiles: { id: number }[]; filters: RenamePreviewFilters; refreshFailed: boolean; stopMovePolling: typeof mocks.replace;
  currentMoveTask: PendingMoveTask | null; moveUncertain: boolean; moveLoading: boolean; moveError: string;
  executePendingMove: ReturnType<typeof vi.fn>; refreshPendingMove: ReturnType<typeof vi.fn> };
vi.mock("../stores/preview", () => ({ usePreviewStore: () => preview }));
vi.mock("../stores/pendingFiles", () => ({ usePendingFileStore: () => pendingStore }));
import RenamePreviewsView from "./RenamePreviewsView.vue";
import { usePaginationStore } from "../stores/pagination";

type HostNode = { children: HostNode[] };
const renderer = createRenderer<HostNode, HostNode>({
  createElement: () => ({ children: [] }), createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null,
});
type State = {
  previewTotal: number; previewStats: Record<string, number>; mediaFileCount: number; batchScopeLoading: boolean;
  loadPreviewPage: (reset?: boolean) => Promise<void>; refreshPreviews: () => Promise<void>;
  handleTmdbDropdownCommand: (command: string) => Promise<void>;
  pendingTotal: number; loadPendingPage: (reset?: boolean) => Promise<void>;
  metadataPreviewId: number | null; aiParsePreviewId: number | null;
  applyMetadataCandidate: (candidate: MetadataMatchResult) => Promise<void>;
  applyAiCandidate: (candidate: AiParseCandidate) => Promise<void>;
  operationResultAlert: { type: string; title: string } | null;
  executeRollbackPlan: () => Promise<void>;
  moveSelectedPendingFiles: () => Promise<void>; selectedPendingFileIds: number[]; pendingMoveTargetDirectory: string;
  activePreviewTab: string;
  pendingMoveStatusText: string; operationProgressText: string;
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function page(id: number, pageNumber = 1): RenamePreviewPage {
  return { items: [{ id } as RenamePreview], total: 1000, page: pageNumber, page_size: 50, stats: { generated: 999, edited: 1 } };
}
let app: ReturnType<typeof renderer.createApp>, state: State;
describe("preview server paging and frozen batch scope", () => {
  it("updates mounted preview scope when source and task query change", async () => {
    reactive(mocks.route).query = { media_source_id: "2", scan_job_id: "20" };
    await nextTick(); await nextTick(); await nextTick();
    expect(preview.filters).toEqual({ media_source_id: 2, scan_job_id: 20 });
    reactive(mocks.route).query = {};
    await nextTick(); await nextTick();
    expect(preview.filters).toEqual({});
    expect(preview.previews).toEqual([]);
  });
  beforeEach(async () => {
    vi.resetAllMocks();
    mocks.route.query = {};
    mocks.operation.currentOperation = null;
    mocks.operation.currentRollbackPlan = null;
    mocks.operation.rollbackErrorMessage = "";
    operationStore = reactive(mocks.operation);
    mocks.history.mockResolvedValue({ ready: false, status: "untested", tested_at: null, message: "AI 连接尚未测试" });
    setActivePinia(createPinia());
    preview = reactive({ filters: { media_source_id: 1, scan_job_id: 10 }, previews: [] as RenamePreview[],
      loading: false, matchMetadataBatch: mocks.match,
      applyMetadataCandidate: mocks.applyMetadata, applyAiCandidate: mocks.applyAi });
    pendingStore = reactive({ pendingFiles: [], filters: {}, refreshFailed: false, stopMovePolling: vi.fn(),
      currentMoveTask: null, moveUncertain: false, moveLoading: false, moveError: "",
      executePendingMove: vi.fn(), refreshPendingMove: vi.fn() });
    mocks.page.mockResolvedValue(page(1));
    mocks.files.mockResolvedValue({ items: [], total: 1000 });
    mocks.pending.mockResolvedValue({ items: [], total: 0, stats: {}, page: 1, page_size: 50 });
    mocks.confirm.mockResolvedValue(undefined);
    mocks.match.mockResolvedValue({ items: [], failed_items: [], total_count: 2, success_count: 2, failed_count: 0 });
    app = renderer.createApp(RenamePreviewsView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await nextTick();
    await vi.waitFor(() => expect(mocks.history).toHaveBeenCalledOnce());
    preview.filters = { media_source_id: 1, scan_job_id: 10 };
    mocks.page.mockClear();
    mocks.files.mockClear();
    mocks.pending.mockClear();
  });
  afterEach(() => { app.unmount(); });
  it("canceling pending migration confirmation neither submits nor raises an error", async () => {
    state.selectedPendingFileIds = [1]; state.pendingMoveTargetDirectory = "synthetic-target";
    mocks.confirm.mockRejectedValueOnce("cancel");
    await expect(state.moveSelectedPendingFiles()).resolves.toBeUndefined();
    expect(pendingStore.executePendingMove).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });
  it("changing pending migration scope during confirmation cancels the frozen request", async () => {
    state.selectedPendingFileIds = [1]; state.pendingMoveTargetDirectory = "synthetic-target";
    const confirmation = deferred<void>(); mocks.confirm.mockReturnValueOnce(confirmation.promise);
    const execution = state.moveSelectedPendingFiles();
    preview.filters.keyword = "changed";
    confirmation.resolve(); await execution;
    expect(pendingStore.executePendingMove).not.toHaveBeenCalled();
    expect(mocks.warning).toHaveBeenCalledOnce();
    expect(mocks.error).not.toHaveBeenCalled();
  });
  it("partial, failed and interrupted migrations never produce a success toast", async () => {
    for (const status of ["partial_failed", "failed", "interrupted"]) {
      state.selectedPendingFileIds = [1]; state.pendingMoveTargetDirectory = "synthetic-target";
      pendingStore.executePendingMove.mockImplementationOnce(async () => {
        pendingStore.currentMoveTask = { id: 1, status, total_count: 1, moved_count: 0, failed_count: 0,
          target_directory: "synthetic-target", error_message: null, created_at: "now", updated_at: "now", items: [] };
      });
      await state.moveSelectedPendingFiles();
      expect(mocks.success).not.toHaveBeenCalled();
    }
    expect(mocks.warning).toHaveBeenCalledTimes(3);
  });
  it("a migration task link restores read-only observation before any old rename link", async () => {
    app.unmount();
    mocks.route.query = { pending_move_task_id: "41", operation_id: "99" };
    app = renderer.createApp(RenamePreviewsView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await vi.waitFor(() => expect(pendingStore.refreshPendingMove).toHaveBeenCalledWith(41, undefined, expect.any(Function)));
    expect(state.activePreviewTab).toBe("pending");
    expect(pendingStore.executePendingMove).not.toHaveBeenCalled();
  });
  it("migration status uses its persisted counts instead of another operation's text", () => {
    pendingStore.currentMoveTask = { id: 1, status: "partial_failed", total_count: 4, moved_count: 1, failed_count: 1,
      target_directory: "synthetic-target", error_message: null, created_at: "now", updated_at: "now", items: [] };
    state.operationProgressText = "synthetic AI progress";
    expect(state.pendingMoveStatusText).toBe("已迁移 1/4 项，失败 1 项，未完成 2 项");
    pendingStore.moveError = "synthetic lookup failure";
    expect(state.pendingMoveStatusText).toBe("synthetic lookup failure");
  });
  it("canceling rollback confirmation neither executes nor raises an error", async () => {
    operationStore.currentOperation = { id: 10, status: "completed" } as RenameOperation;
    operationStore.currentRollbackPlan = { id: 20, operation_id: 10, status: "checked", executable_count: 2 } as RenameRollbackPlan;
    mocks.confirm.mockRejectedValueOnce("cancel");
    await expect(state.executeRollbackPlan()).resolves.toBeUndefined();
    expect(operationStore.executeRollbackPlan).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });
  it("partial or interrupted rollback never produces a success toast", async () => {
    for (const status of ["partial_failed", "failed", "interrupted"]) {
      operationStore.currentOperation = { id: 10, status: "completed" } as RenameOperation;
      operationStore.currentRollbackPlan = { id: 20, operation_id: 10, status: "checked", executable_count: 2 } as RenameRollbackPlan;
      operationStore.executeRollbackPlan.mockImplementationOnce(async () => {
        operationStore.currentRollbackPlan!.status = status;
      });
      await state.executeRollbackPlan();
      expect(mocks.success).not.toHaveBeenCalled();
    }
    expect(mocks.warning).toHaveBeenCalledTimes(3);
  });
  it("does not label interrupted or invalidated zero-failure batches as successful", () => {
    for (const status of ["queued", "running", "interrupted", "invalidated", "failed"]) {
      operationStore.currentOperation = { id: 91, status, total_count: 2, renamed_count: 0, failed_count: 0, conflict_count: 0, items: [] } as unknown as RenameOperation;
      expect(state.operationResultAlert?.type).not.toBe("success");
      if (status === "invalidated") expect(state.operationResultAlert?.title).toBe("已失效");
    }
  });
  it("loads only a bounded page and keeps global counts", async () => {
    usePaginationStore().setPageSize("rename-previews", 0);
    await state.loadPreviewPage();
    expect(mocks.page.mock.calls[0][0].page_size).toBe(50);
    expect(preview.previews.length).toBe(1);
    expect(state.previewTotal).toBe(1000);
    expect(state.previewStats.generated).toBe(999);
  });
  it("ignores old pages and clears immediately when the task changes", async () => {
    const first = deferred<RenamePreviewPage>(), second = deferred<RenamePreviewPage>();
    mocks.page.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const oldRequest = state.loadPreviewPage();
    preview.filters.scan_job_id = 11;
    const newRequest = state.loadPreviewPage();
    second.resolve(page(11)); await newRequest;
    first.resolve(page(10)); await oldRequest;
    expect(preview.previews.map(item => item.id)).toEqual([11]);
    preview.filters.scan_job_id = undefined;
    expect(preview.previews).toEqual([]);
    expect(state.previewTotal).toBe(0);
  });
  it("does not start context requests after a task change during the page request", async () => {
    const response = deferred<RenamePreviewPage>(); mocks.page.mockReturnValueOnce(response.promise);
    const request = state.refreshPreviews();
    preview.filters.scan_job_id = 11;
    response.resolve(page(10)); await request;
    expect(mocks.files).not.toHaveBeenCalled();
    expect(mocks.pending).not.toHaveBeenCalled();
  });
  it("does not repopulate pending files after leaving", async () => {
    const response = deferred<{ items: { id: number }[]; total: number; stats: Record<string, number>; page: number }>(); mocks.pending.mockReturnValueOnce(response.promise);
    const request = state.refreshPreviews();
    await vi.waitFor(() => expect(mocks.pending).toHaveBeenCalledOnce());
    app.unmount(); response.resolve({ items: [{ id: 88 }], total: 1, stats: { pending: 1 }, page: 1 }); await request;
    expect(pendingStore.pendingFiles).toEqual([]);
  });
  it("rejects over-limit batches with one warning and no confirmation or execution", async () => {
    mocks.selection.mockResolvedValue({ items: [], total: 1000, limit: 200, over_limit: true });
    await state.handleTmdbDropdownCommand("all_tmdb");
    expect(mocks.warning).toHaveBeenCalledOnce();
    expect(mocks.confirm).not.toHaveBeenCalled(); expect(mocks.match).not.toHaveBeenCalled();
    expect(state.batchScopeLoading).toBe(false);
  });
  it("freezes all filtered IDs and suppresses duplicate clicks during confirmation", async () => {
    const confirmation = deferred<void>(); mocks.confirm.mockReturnValueOnce(confirmation.promise);
    const items = [{ id: 77 }, { id: 88 }] as RenameSelectionItem[];
    mocks.selection.mockResolvedValue({ items, total: 2, limit: 200, over_limit: false });
    const request = state.handleTmdbDropdownCommand("all_tmdb");
    await vi.waitFor(() => expect(mocks.confirm).toHaveBeenCalledOnce());
    await state.handleTmdbDropdownCommand("all_tmdb");
    expect(mocks.selection).toHaveBeenCalledOnce();
    confirmation.resolve(); await request;
    expect(mocks.match).toHaveBeenCalledWith([77, 88], "parsed_title");
    expect(mocks.page).toHaveBeenCalledOnce();
    expect(state.batchScopeLoading).toBe(false);
  });
  it("cancels execution if filters change while the confirmation is open", async () => {
    const confirmation = deferred<void>(); mocks.confirm.mockReturnValueOnce(confirmation.promise);
    mocks.selection.mockResolvedValue({ items: [{ id: 77 }], total: 1, limit: 200, over_limit: false });
    const request = state.handleTmdbDropdownCommand("all_tmdb");
    await vi.waitFor(() => expect(mocks.confirm).toHaveBeenCalledOnce());
    preview.filters.keyword = "different";
    confirmation.resolve(); await request;
    expect(mocks.match).not.toHaveBeenCalled();
    expect(mocks.warning).toHaveBeenCalledOnce();
    expect(state.batchScopeLoading).toBe(false);
  });
  it("pages pending files independently and rejects an earlier task response", async () => {
    const old = deferred<{ items: { id: number }[]; total: number; stats: Record<string, number>; page: number }>();
    usePaginationStore().setPageSize("pending-files", 0);
    mocks.pending.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ items: [{ id: 22 }], total: 900, stats: { pending: 899 }, page: 2 });
    const oldRequest = state.loadPendingPage();
    preview.filters.scan_job_id = 11;
    usePaginationStore().setPage("pending-files", 2);
    await state.loadPendingPage(false);
    old.resolve({ items: [{ id: 1 }], total: 1000, stats: { pending: 1000 }, page: 1 }); await oldRequest;
    expect(mocks.pending.mock.calls[0][0].page_size).toBe(50);
    expect(pendingStore.pendingFiles).toEqual([{ id: 22 }]);
    expect(state.pendingTotal).toBe(900);
    expect(usePaginationStore().getState("pending-files").currentPage).toBe(2);
  });
  it("does not apply route filters after leaving during initial AI history loading", async () => {
    app.unmount();
    const history = deferred<null>();
    mocks.history.mockReturnValueOnce(history.promise);
    mocks.route.query = { media_source_id: 9, scan_job_id: 99 };
    app = renderer.createApp(RenamePreviewsView);
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    app.unmount(); history.resolve(null); await nextTick();
    expect(preview.filters).toEqual({ media_source_id: 1, scan_job_id: 10 });
    expect(mocks.page).not.toHaveBeenCalled();
  });
  it("refreshes the bounded page and global counts after metadata candidate application", async () => {
    state.metadataPreviewId = 77;
    await state.applyMetadataCandidate({ id: 5 } as unknown as MetadataMatchResult);
    expect(mocks.applyMetadata).toHaveBeenCalledOnce();
    expect(mocks.page).toHaveBeenCalledOnce();
    expect(state.previewTotal).toBe(1000);
  });
  it("refreshes the bounded page after AI candidate application", async () => {
    state.aiParsePreviewId = 77;
    await state.applyAiCandidate({ title: "synthetic" } as AiParseCandidate);
    expect(mocks.applyAi).toHaveBeenCalledOnce();
    expect(mocks.page).toHaveBeenCalledOnce();
    expect(preview.previews).toHaveLength(1);
  });
});
