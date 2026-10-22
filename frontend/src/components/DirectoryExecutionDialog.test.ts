import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectoryExecution, DirectoryExecutionPolicy, DirectoryPlan } from "../api/cloudDirectory";
import { invalidateSessionRequests } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ policy: vi.fn(), start: vi.fn(), find: vi.fn(), cancel: vi.fn(), commit: vi.fn(),
  persist: vi.fn(), permission: vi.fn(), confirm: vi.fn() }));
vi.mock("../api/cloudDirectory", () => ({ fetchDirectoryPolicy: mocks.policy, startDirectoryExecution: mocks.start,
  findDirectoryExecution: mocks.find, cancelDirectoryExecution: mocks.cancel, commitDirectoryLocally: mocks.commit }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: mocks.permission }) }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: mocks.confirm } }));
vi.mock("./OperationLogDrawer.vue", () => ({ default: { render: () => null } }));
import DirectoryExecutionDialog from "./DirectoryExecutionDialog.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const requestId = "12345678-1234-1234-1234-123456789abc";
const planId = "22345678-1234-1234-1234-123456789abc";
const plan: DirectoryPlan = { id: planId, requestId: planId, inventoryJobId: planId, inventoryRevision: 1,
  revision: 0, status: "prepared", valid: true, invalidReason: null, parentPath: "/cloud/movies", ruleVersion: "synthetic",
  policyHash: "a".repeat(64), rulesHash: "b".repeat(64), createdAt: 1792100000, expiresAt: 1792101800,
  networkAccess: false, writeAvailable: false, items: [{ entryId: 1, generation: 1, remoteId: "synthetic-id",
    sourceName: "[推广] 示例", targetName: "示例", sourcePath: "/cloud/movies/[推广] 示例", targetPath: "/cloud/movies/示例" }] };
const policy: DirectoryExecutionPolicy = { available: true, reason: null, policyHash: plan.policyHash,
  limits: { items: 5, baseRpc: 3, rpcPerItem: 7, seconds: 180, bytes: 1048576 }, controlledMode: true,
  atomicIdentityGuaranteed: false, oldPlansGloballyInvalidated: true, automaticRescan: false, networkAccess: false };
const execution: DirectoryExecution = { planId, requestId, status: "accepted", reason: null, revision: 0,
  cancelRequested: false, workerSlotHeld: true, globalSlotHeld: true, pollingRecommended: true,
  logId: 1, logAvailable: true,
  usedRpc: 0, maxRpc: 10, receivedBytes: 0, maxBytes: 1048576, createdAt: 1792100000, expiresAt: 1792100180,
  items: [{ ordinal: 1, sourceName: plan.items[0]!.sourceName, targetName: "示例", state: "not_started", reason: null,
    sentAt: null, verifiedAt: null }], events: [], networkAccess: false, replayed: false };
type State = { tokenValue: string; maintenance: boolean; globalFence: boolean; requestId: string;
  execution: DirectoryExecution | null; errorText: string; canStart: boolean; busy: boolean;
  canViewLogs: boolean;
  start: () => Promise<void>; lookup: () => Promise<void>; loadPolicy: () => Promise<void>; close: () => void;
  hidden: () => void; acknowledge: () => Promise<void>; act: (kind: "cancel" | "commit") => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); await nextTick(); };
function mount(overrides = {}) {
  app = renderer.createApp(DirectoryExecutionDialog, { visible: true, plan, requestId: "", persistRequestId: mocks.persist, ...overrides });
  app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
  app.mount({ children: [] }); state = (app._instance as unknown as { setupState: State }).setupState;
}
function fill() { state.tokenValue = "synthetic-limited-secret"; state.maintenance = true; state.globalFence = true; }

describe("controlled directory execution dialog", () => {
  beforeEach(async () => {
    vi.useFakeTimers(); vi.clearAllMocks(); vi.stubGlobal("location", { protocol: "https:" });
    vi.stubGlobal("crypto", { randomUUID: () => requestId });
    mocks.policy.mockReset().mockResolvedValue(policy); mocks.start.mockReset().mockResolvedValue(execution);
    mocks.find.mockReset().mockResolvedValue(execution); mocks.cancel.mockReset().mockResolvedValue({ ...execution, cancelRequested: true, revision: 1 });
    mocks.commit.mockReset().mockResolvedValue({ ...execution, status: "completed", revision: 2, workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    mocks.persist.mockReset().mockResolvedValue(undefined); mocks.permission.mockReset().mockReturnValue(true); mocks.confirm.mockResolvedValue("confirm");
    mount(); await flush();
  });
  afterEach(() => { app.unmount(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("requires two confirmations and saves an independent marker before one secret handoff", async () => {
    fill(); state.globalFence = false; await state.start(); expect(mocks.start).not.toHaveBeenCalled();
    state.globalFence = true; await state.start();
    expect(mocks.persist).toHaveBeenCalledExactlyOnceWith(requestId);
    expect(mocks.persist.mock.invocationCallOrder[0]).toBeLessThan(mocks.start.mock.invocationCallOrder[0]!);
    expect(mocks.start).toHaveBeenCalledExactlyOnceWith(planId, { requestId, expectedRevision: 0, policyHash: plan.policyHash,
      confirmMaintenance: true, confirmGlobalFence: true, apiToken: "synthetic-limited-secret" });
    expect(state.tokenValue).toBe(""); expect(state.canStart).toBe(false); await state.start(); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
  it("never submits after marker persistence fails", async () => {
    mocks.persist.mockRejectedValueOnce(new Error("navigation")); fill(); await state.start();
    expect(mocks.start).not.toHaveBeenCalled(); expect(state.requestId).toBe(requestId); expect(state.tokenValue).toBe("");
  });
  it("keeps a newer execution marker when an old acknowledgement fails", async () => {
    mocks.start.mockResolvedValueOnce({ ...execution, status: "completed", workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    fill(); await state.start();
    let reject!: (error: Error) => void; mocks.persist.mockImplementationOnce(() => new Promise((_done, fail) => { reject = fail; }));
    const pending = state.acknowledge(); await flush();
    const nextRequest = "62345678-1234-1234-1234-123456789abc";
    app._instance!.props.requestId = nextRequest; await nextTick();
    mocks.find.mockResolvedValueOnce({ ...execution, requestId: nextRequest, status: "completed", workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    await state.lookup(); reject(new Error("old-persist")); await pending;
    expect(state.requestId).toBe(nextRequest); expect(state.execution?.requestId).toBe(nextRequest); expect(state.errorText).toBe("");
  });
  it("queries only the original identifier after a lost response", async () => {
    mocks.start.mockRejectedValueOnce(new Error("synthetic-secret-in-error")); fill(); await state.start();
    expect(state.errorText).not.toContain("synthetic-secret"); await state.start(); await state.lookup();
    expect(mocks.start).toHaveBeenCalledTimes(1); expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId);
  });
  it("rejects HTTP, missing execute permission, changed policy and unsafe capability claims", async () => {
    vi.stubGlobal("location", { protocol: "http:" }); app.unmount(); mount(); await flush(); fill(); await state.start();
    expect(state.canStart).toBe(false); expect(mocks.start).not.toHaveBeenCalled();
    vi.stubGlobal("location", { protocol: "https:" }); mocks.permission.mockReturnValue(false); app.unmount(); mount(); await flush(); fill(); await state.start();
    expect(mocks.start).not.toHaveBeenCalled();
    mocks.permission.mockReturnValue(true); mocks.policy.mockResolvedValueOnce({ ...policy, policyHash: "c".repeat(64) });
    app.unmount(); mount(); await flush(); fill(); await state.start(); expect(mocks.start).not.toHaveBeenCalled();
    mocks.policy.mockResolvedValueOnce({ ...policy, atomicIdentityGuaranteed: true }); await state.loadPolicy(); fill(); await state.start();
    expect(state.errorText).toBeTruthy(); expect(mocks.start).not.toHaveBeenCalled();
  });
  it("does not automatically resume a persisted request on mount", async () => {
    app.unmount(); mount({ requestId }); await flush();
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.find).not.toHaveBeenCalled();
    await state.lookup(); expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId);
  });
  it("bounds local event polling to 1500ms and stops on completion or malformed response", async () => {
    fill(); await state.start(); await vi.advanceTimersByTimeAsync(1499); expect(mocks.find).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1); expect(mocks.find).toHaveBeenCalledTimes(1);
    mocks.find.mockResolvedValueOnce({ ...execution, status: "completed", revision: 1, pollingRecommended: false, globalSlotHeld: false, workerSlotHeld: false });
    await vi.advanceTimersByTimeAsync(1500); await vi.advanceTimersByTimeAsync(10000); expect(mocks.find).toHaveBeenCalledTimes(2);
    mocks.find.mockResolvedValueOnce({ ...execution, events: new Array(1000).fill({}) }); await state.lookup();
    expect(state.execution).toBeNull(); expect(state.errorText).toBeTruthy();
  });
  it("preserves a freeze without polling after expiry", async () => {
    mocks.start.mockResolvedValueOnce({ ...execution, pollingRecommended: false }); fill(); await state.start();
    await vi.advanceTimersByTimeAsync(10000); expect(mocks.find).not.toHaveBeenCalled(); expect(state.execution?.globalSlotHeld).toBe(true);
  });
  it("only commits local state after a reaped worker and does not repeat the write", async () => {
    mocks.start.mockResolvedValueOnce({ ...execution, status: "review", reason: "awaiting_path_sync", workerSlotHeld: false, pollingRecommended: false });
    fill(); await state.start(); await state.act("commit");
    expect(mocks.commit).toHaveBeenCalledExactlyOnceWith(planId, 0); expect(mocks.start).toHaveBeenCalledTimes(1);
    expect(state.execution?.status).toBe("completed");
  });
  it("requests cancellation once but never assumes the remote write was undone", async () => {
    fill(); await state.start(); await state.act("cancel"); await state.act("cancel");
    expect(mocks.cancel).toHaveBeenCalledExactlyOnceWith(planId, 0); expect(state.execution?.globalSlotHeld).toBe(true);
  });
  it("clears secrets and fences late replies when closed or the session changes", async () => {
    let resolve!: (value: DirectoryExecution) => void;
    mocks.start.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    fill(); const pending = state.start(); await flush(); state.close(); resolve(execution); await pending;
    expect(state.tokenValue).toBe(""); expect(state.execution).toBeNull(); await vi.advanceTimersByTimeAsync(3000); expect(mocks.find).not.toHaveBeenCalled();
    app.unmount(); mount(); await flush(); fill(); invalidateSessionRequests(); await state.start();
    expect(state.tokenValue).toBe(""); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
  it("clears a terminal marker only after explicit acknowledgement and never deletes evidence", async () => {
    mocks.start.mockResolvedValueOnce({ ...execution, status: "completed", workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    fill(); await state.start(); await state.acknowledge();
    expect(mocks.confirm).toHaveBeenCalledOnce(); expect(mocks.persist).toHaveBeenLastCalledWith("");
    expect(mocks.cancel).not.toHaveBeenCalled(); expect(mocks.commit).not.toHaveBeenCalled();
  });
  it("disables cleaned ordinary logs without hiding execution evidence", async () => {
    mocks.start.mockResolvedValueOnce({ ...execution, status: "completed", logAvailable: false,
      workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    fill(); await state.start();
    expect(state.canViewLogs).toBe(false); expect(state.execution?.items).toHaveLength(1);
    expect(state.execution?.status).toBe("completed");
  });
});
