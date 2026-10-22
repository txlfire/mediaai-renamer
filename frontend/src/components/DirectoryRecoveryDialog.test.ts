import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectoryRecovery, DirectoryExecution, DirectoryPlan } from "../api/cloudDirectory";
import { invalidateSessionRequests } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ start: vi.fn(), find: vi.fn(), cancel: vi.fn(), commit: vi.fn(), review: vi.fn(), persist: vi.fn(), confirm: vi.fn(), permission: vi.fn() }));
vi.mock("../api/cloudDirectory", () => ({ startDirectoryRecovery: mocks.start, findDirectoryRecovery: mocks.find,
  cancelDirectoryRecovery: mocks.cancel, commitDirectoryRecovery: mocks.commit, reviewDirectoryRecoveryWorker: mocks.review }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: mocks.permission }) }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: mocks.confirm } }));
import DirectoryRecoveryDialog from "./DirectoryRecoveryDialog.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const requestId = "12345678-1234-1234-1234-123456789abc", planId = "22345678-1234-1234-1234-123456789abc", recoveryId = "32345678-1234-1234-1234-123456789abc";
const plan: DirectoryPlan = { id: planId, requestId: planId, inventoryJobId: planId, inventoryRevision: 1, revision: 0,
  status: "prepared", valid: false, invalidReason: "execution_accepted", parentPath: "/cloud/movies", ruleVersion: "synthetic",
  policyHash: "a".repeat(64), rulesHash: "b".repeat(64), createdAt: 1792100000, expiresAt: 1792101800, networkAccess: false, writeAvailable: false,
  items: [{ entryId: 1, generation: 1, remoteId: "id", sourceName: "[推广] 示例", targetName: "示例", sourcePath: "/cloud/movies/[推广] 示例", targetPath: "/cloud/movies/示例" }] };
const execution: DirectoryExecution = { planId, requestId: planId, status: "review", reason: "rpc_unknown", revision: 8,
  workerSlotHeld: false, globalSlotHeld: true, pollingRecommended: false, cancelRequested: false, logId: 1, logAvailable: true,
  usedRpc: 0, maxRpc: 10, receivedBytes: 0, maxBytes: 1048576, createdAt: 1792100000, expiresAt: 1792100180, items: [], events: [], networkAccess: false, replayed: false };
const recovery: DirectoryRecovery = { id: recoveryId, requestId, planId, attempt: 1, revision: 0, status: "accepted", reason: null,
  workerSlotHeld: true, recoverySlotHeld: true, cancelRequested: false, pollingRecommended: true, usedRpc: 0, maxRpc: 6,
  receivedBytes: 0, maxBytes: 1048576, createdAt: 1792100000, expiresAt: 1792100090, updatedAt: 1792100000,
  items: [{ ordinal: 1, sourceName: plan.items[0]!.sourceName, targetName: "示例", position: "not_observed" }], events: [],
  originalOutcomeRewritten: false, networkAccess: false, replayed: false };
const ready: DirectoryRecovery = { ...recovery, revision: 10, status: "ready", workerSlotHeld: false, recoverySlotHeld: false, pollingRecommended: false,
  items: [{ ...recovery.items[0]!, position: "at_target" }] };
type State = { tokenValue: string; maintenance: boolean; remoteIdle: boolean; positionsConfirmed: boolean; requestId: string;
  reviewForm: boolean; clientsStopped: boolean; reviewRemoteIdle: boolean; canReviewWorker: boolean; canSubmitReview: boolean;
  openWorkerReview: () => void; reviewWorker: () => Promise<void>;
  recovery: DirectoryRecovery | null; errorText: string; canStart: boolean; canCommit: boolean; canPrepareNext: boolean;
  start: () => Promise<void>; lookup: () => Promise<void>; act: (kind: "cancel" | "commit") => Promise<void>;
  prepareNext: () => Promise<void>; close: () => void };
let app: ReturnType<typeof renderer.createApp>, state: State;
let grants: { manage: boolean };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick(); };
function mount(overrides = {}) {
  app = renderer.createApp(DirectoryRecoveryDialog, { visible: true, plan, execution, requestId: "", persistRequestId: mocks.persist, ...overrides });
  app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {}; app.mount({ children: [] });
  state = (app._instance as unknown as { setupState: State }).setupState;
}
function fill() { state.tokenValue = "synthetic-read-token"; state.maintenance = true; state.remoteIdle = true; }

describe("independent readonly directory recovery dialog", () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks(); vi.stubGlobal("location", { protocol: "https:" }); vi.stubGlobal("crypto", { randomUUID: () => requestId });
    grants = reactive({ manage: true });
    mocks.permission.mockReset().mockImplementation(name => name === "settings:write" ? grants.manage : true); mocks.persist.mockReset().mockResolvedValue(undefined); mocks.confirm.mockResolvedValue("confirm");
    mocks.review.mockReset().mockResolvedValue({ ...recovery, revision: 1, status: "review", workerSlotHeld: false, recoverySlotHeld: false, pollingRecommended: false });
    mocks.start.mockReset().mockResolvedValue(recovery); mocks.find.mockReset().mockResolvedValue(recovery);
    mocks.commit.mockReset().mockResolvedValue({ ...ready, status: "committed", revision: 11 });
    mocks.cancel.mockReset().mockResolvedValue({ ...recovery, cancelRequested: true, revision: 1 }); mount();
  });
  afterEach(() => { app.unmount(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("requires independent idle confirmation and persists the read request before handoff", async () => {
    fill(); state.remoteIdle = false; await state.start(); expect(mocks.start).not.toHaveBeenCalled(); state.remoteIdle = true;
    await state.start(); expect(mocks.persist).toHaveBeenCalledExactlyOnceWith(requestId);
    expect(mocks.persist.mock.invocationCallOrder[0]).toBeLessThan(mocks.start.mock.invocationCallOrder[0]!);
    expect(mocks.start).toHaveBeenCalledExactlyOnceWith(planId, { requestId, expectedRevision: 8,
      confirmMaintenance: true, confirmRemoteIdle: true, apiToken: "synthetic-read-token" });
    expect(state.tokenValue).toBe(""); await state.start(); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
  it("never hands off after a failed navigation marker and never resends after lost response", async () => {
    mocks.persist.mockRejectedValueOnce(new Error("navigation")); fill(); await state.start(); expect(mocks.start).not.toHaveBeenCalled();
    app.unmount(); mount(); mocks.start.mockRejectedValueOnce(new Error("synthetic-secret-error")); fill(); await state.start();
    await state.start(); await state.lookup(); expect(mocks.start).toHaveBeenCalledTimes(1);
    expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId); expect(state.errorText).not.toContain("synthetic-secret");
  });
  it("does not auto-start or auto-query a previous recovery when reopened", async () => {
    app.unmount(); mount({ execution: { ...execution, latestRecovery: recovery } }); await flush();
    expect(state.requestId).toBe(requestId); expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.find).not.toHaveBeenCalled();
    await state.lookup(); expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId);
  });
  it("bounds polling and preserves original freeze without any write command", async () => {
    fill(); await state.start(); await vi.advanceTimersByTimeAsync(1499); expect(mocks.find).not.toHaveBeenCalled();
    mocks.find.mockResolvedValueOnce(ready); await vi.advanceTimersByTimeAsync(1); expect(mocks.find).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10000); expect(mocks.find).toHaveBeenCalledTimes(1); expect(state.canCommit).toBe(false);
    state.positionsConfirmed = true; await state.act("commit"); expect(mocks.commit).toHaveBeenCalledExactlyOnceWith(recoveryId, 10);
    expect(state.recovery?.status).toBe("committed"); expect(execution.globalSlotHeld).toBe(true);
  });
  it("rejects expanded methods, changed names, rewritten original facts and oversized event lists", async () => {
    app.unmount(); mount({ requestId });
    for (const result of [{ ...recovery, originalOutcomeRewritten: true }, { ...recovery, items: [{ ...recovery.items[0], targetName: "other" }] },
      { ...recovery, usedRpc: 1, events: [{ ordinal: 1, method: "RenameFile", phase: "sending", receivedBytes: 0 }] }, { ...recovery, events: new Array(1000).fill({}) }]) {
      mocks.find.mockResolvedValueOnce(result); await state.lookup(); expect(state.recovery).toBeNull(); expect(state.errorText).toBeTruthy();
    }
    expect(mocks.start).not.toHaveBeenCalled();
  });
  it("new rounds require explicit confirmation and never reuse the old request automatically", async () => {
    mocks.start.mockResolvedValueOnce({ ...ready, status: "review" }); fill(); await state.start();
    expect(state.canPrepareNext).toBe(true); await state.prepareNext();
    expect(mocks.confirm).toHaveBeenCalledOnce(); expect(mocks.persist).toHaveBeenLastCalledWith("");
    expect(state.requestId).toBe(""); expect(state.maintenance).toBe(false); expect(state.remoteIdle).toBe(false); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
  it("reviews an orphan read worker only after two fresh confirmations without a Token", async () => {
    app.unmount(); mount({ requestId }); await state.lookup(); state.openWorkerReview();
    expect(state.reviewForm).toBe(true); await state.reviewWorker(); expect(mocks.review).not.toHaveBeenCalled();
    state.clientsStopped = true; await state.reviewWorker(); expect(mocks.review).not.toHaveBeenCalled();
    state.reviewRemoteIdle = true; await state.reviewWorker();
    expect(mocks.review).toHaveBeenCalledExactlyOnceWith(recoveryId, { expectedRevision: 0, confirmClientsStopped: true, confirmRemoteIdle: true });
    expect(state.recovery?.recoverySlotHeld).toBe(false); expect(execution.globalSlotHeld).toBe(true);
    expect(state.reviewForm).toBe(false); expect(state.clientsStopped).toBe(false); expect(state.reviewRemoteIdle).toBe(false);
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.commit).not.toHaveBeenCalled();
  });
  it("permission loss erases pending worker-review confirmations", async () => {
    app.unmount(); mount({ requestId }); await state.lookup(); state.openWorkerReview();
    state.clientsStopped = true; state.reviewRemoteIdle = true; grants.manage = false; await nextTick();
    await state.reviewWorker(); expect(mocks.review).not.toHaveBeenCalled(); expect(state.reviewForm).toBe(false);
  });
  it("keeps a newer read marker when an old prepare-next persistence fails", async () => {
    mocks.start.mockResolvedValueOnce({ ...ready, status: "review" }); fill(); await state.start();
    let reject!: (error: Error) => void; mocks.persist.mockImplementationOnce(() => new Promise((_done, fail) => { reject = fail; }));
    const pending = state.prepareNext(); await flush();
    const nextRequest = "42345678-1234-1234-1234-123456789abc";
    app._instance!.props.requestId = nextRequest; await nextTick();
    mocks.find.mockResolvedValueOnce({ ...ready, requestId: nextRequest }); await state.lookup();
    reject(new Error("old-persist")); await pending;
    expect(state.requestId).toBe(nextRequest); expect(state.recovery?.requestId).toBe(nextRequest); expect(state.errorText).toBe("");
  });
  it("caps further rounds and never commits ambiguous, unreaped or unconfirmed positions", async () => {
    mocks.start.mockResolvedValueOnce({ ...ready, attempt: 5, status: "review", items: [{ ...ready.items[0], position: "ambiguous" }] });
    fill(); await state.start(); expect(state.canPrepareNext).toBe(false); state.positionsConfirmed = true; await state.act("commit");
    expect(mocks.commit).not.toHaveBeenCalled();
  });
  it("blocks HTTP, missing permission and an actively running original worker", async () => {
    for (const override of [{}, { execution: { ...execution, pollingRecommended: true } }]) {
      vi.stubGlobal("location", { protocol: Object.keys(override).length ? "https:" : "http:" }); app.unmount(); mount(override); fill(); await state.start();
      expect(state.canStart).toBe(false);
    }
    mocks.permission.mockReturnValue(false); app.unmount(); mount(); fill(); await state.start(); expect(mocks.start).not.toHaveBeenCalled();
  });
  it("close and session changes clear secrets and fence late replies", async () => {
    let resolve!: (value: DirectoryRecovery) => void;
    mocks.start.mockImplementationOnce(() => new Promise(done => { resolve = done; })); fill(); const pending = state.start(); await flush();
    state.close(); resolve(recovery); await pending; expect(state.recovery).toBeNull(); expect(state.tokenValue).toBe("");
    app.unmount(); mount(); fill(); invalidateSessionRequests(); await state.start(); expect(state.tokenValue).toBe(""); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
});
