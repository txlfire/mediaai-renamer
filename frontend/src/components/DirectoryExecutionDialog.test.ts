import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectoryExecution, DirectoryExecutionPolicy, DirectoryPlan, DirectoryExecutionInput } from "../api/cloudDirectory";
import { invalidateSessionRequests } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ policy: vi.fn(), start: vi.fn(), find: vi.fn(), cancel: vi.fn(), commit: vi.fn(),
  persist: vi.fn(), permission: vi.fn(), confirm: vi.fn(), boundPolicy: vi.fn(), boundStart: vi.fn() }));
vi.mock("../api/cloudDirectory", () => ({ fetchDirectoryPolicy: mocks.policy, startDirectoryExecution: mocks.start,
  findDirectoryExecution: mocks.find, cancelDirectoryExecution: mocks.cancel, commitDirectoryLocally: mocks.commit }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => ({ hasPermission: mocks.permission }) }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: mocks.confirm } }));
vi.mock("./OperationLogDrawer.vue", () => ({ default: { render: () => null } }));
vi.mock("../api/cloudBoundActions", () => ({ fetchBoundDirectoryPolicy: mocks.boundPolicy, startBoundDirectoryExecution: mocks.boundStart, startBoundDirectoryRecovery: vi.fn() }));
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
const source = { connectionId: "42345678-1234-1234-1234-123456789abc", directoryProfileId: "52345678-1234-1234-1234-123456789abc", selectedPath: plan.parentPath,
  connectionName: "原公司", directoryName: "原电影", currentConnectionName: "公司新名", currentDirectoryName: "电影新名" };
const nativeContext = "62345678-1234-1234-1234-123456789abc";
const boundPlan: DirectoryPlan = { ...plan, bindingState: "bound", contextId: planId, source };
const boundExecution: DirectoryExecution = { ...execution, contextId: nativeContext, source };
type State = { tokenValue: string; maintenance: boolean; globalFence: boolean; requestId: string;
  execution: DirectoryExecution | null; errorText: string; canStart: boolean; busy: boolean;
  canViewLogs: boolean;
  start: () => Promise<void>; lookup: () => Promise<void>; loadPolicy: () => Promise<void>; close: () => void;
  hidden: () => void; acknowledge: () => Promise<void>; act: (kind: "cancel" | "commit") => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
let sentStart: DirectoryExecutionInput | null;
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
    sentStart = null;
    mocks.policy.mockReset().mockResolvedValue(policy); mocks.start.mockReset().mockImplementation(async (_id, input) => { sentStart = { ...input }; return execution; });
    mocks.find.mockReset().mockResolvedValue(execution); mocks.cancel.mockReset().mockResolvedValue({ ...execution, cancelRequested: true, revision: 1 });
    mocks.commit.mockReset().mockResolvedValue({ ...execution, status: "completed", revision: 2, workerSlotHeld: false, globalSlotHeld: false, pollingRecommended: false });
    mocks.persist.mockReset().mockResolvedValue(undefined); mocks.permission.mockReset().mockReturnValue(true); mocks.confirm.mockResolvedValue("confirm");
    mocks.boundPolicy.mockReset().mockResolvedValue({ ...policy, ...source, revisions: {}, writeAvailable: false });
    mocks.boundStart.mockReset().mockResolvedValue({ id: planId, contextId: nativeContext, requestId, resourceId: planId, resourceKind: "directory_execute", revision: 0, replayed: false, source, status: "accepted", networkAccess: null });
    mount(); await flush();
  });
  afterEach(() => { app.unmount(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("requires two confirmations and saves an independent marker before one secret handoff", async () => {
    fill(); state.globalFence = false; await state.start(); expect(mocks.start).not.toHaveBeenCalled();
    state.globalFence = true; await state.start();
    expect(mocks.persist).toHaveBeenCalledExactlyOnceWith(requestId);
    expect(mocks.persist.mock.invocationCallOrder[0]).toBeLessThan(mocks.start.mock.invocationCallOrder[0]!);
    expect(mocks.start).toHaveBeenCalledExactlyOnceWith(planId, { requestId, expectedRevision: 0, policyHash: plan.policyHash,
      confirmMaintenance: true, confirmGlobalFence: true, apiToken: "" });
    expect(sentStart).toEqual({ requestId, expectedRevision: 0, policyHash: plan.policyHash,
      confirmMaintenance: true, confirmGlobalFence: true, apiToken: "synthetic-limited-secret" });
    expect(state.tokenValue).toBe(""); expect(state.canStart).toBe(false); await state.start(); expect(mocks.start).toHaveBeenCalledTimes(1);
  });
  it("never submits after marker persistence fails", async () => {
    mocks.persist.mockRejectedValueOnce(new Error("navigation")); fill(); await state.start();
    expect(mocks.start).not.toHaveBeenCalled(); expect(state.requestId).toBe(requestId); expect(state.tokenValue).toBe("");
  });
  it("多连接政策取自原计划，受理后查询原UUID而非直接解释小回执", async () => {
    app.unmount(); mocks.policy.mockClear(); mocks.find.mockResolvedValue(boundExecution); mount({ plan: boundPlan }); await flush(); fill();
    let sent: Record<string, unknown> | null = null;
    mocks.boundStart.mockImplementationOnce(async (_id, _source, input) => { sent = { ...input }; return { id: planId, contextId: nativeContext }; });
    await state.start(); expect(mocks.policy).not.toHaveBeenCalled(); expect(mocks.boundPolicy).toHaveBeenCalledExactlyOnceWith(source);
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.boundStart).toHaveBeenCalledWith(planId, source, expect.objectContaining({ requestId, apiToken: "" }));
    expect(sent).toMatchObject({ requestId, apiToken: "synthetic-limited-secret", expectedRevision: 0 });
    expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId); expect(state.execution?.contextId).toBe(nativeContext);
  });
  it("未绑定或部分绑定计划不读取单例政策、不交接令牌", async () => {
    for (const value of [{ ...plan, bindingState: "legacy_unbound", contextId: null, source: null }, { ...plan, bindingState: "bound", contextId: planId }]) {
      app.unmount(); mocks.policy.mockClear(); mocks.boundPolicy.mockClear(); mount({ plan: value }); await flush(); fill(); await state.start();
      expect(state.canStart).toBe(false); expect(mocks.policy).not.toHaveBeenCalled(); expect(mocks.boundPolicy).not.toHaveBeenCalled();
    }
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.boundStart).not.toHaveBeenCalled();
  });
  it("原来源政策返回另一目录时拒绝执行", async () => {
    app.unmount(); mocks.boundPolicy.mockResolvedValueOnce({ ...policy, ...source, directoryProfileId: nativeContext }); mount({ plan: boundPlan }); await flush(); fill(); await state.start();
    expect(state.canStart).toBe(false); expect(mocks.boundStart).not.toHaveBeenCalled();
  });
  it("原请求状态的context或来源不对应受理回执时拒绝展示为成功", async () => {
    app.unmount(); mocks.find.mockResolvedValueOnce({ ...boundExecution, contextId: planId }); mount({ plan: boundPlan }); await flush(); fill(); await state.start();
    expect(state.execution).toBeNull(); expect(state.errorText).not.toBe(""); expect(state.requestId).toBe(requestId);
    mocks.find.mockResolvedValueOnce({ ...boundExecution, source: { ...source, connectionId: nativeContext } }); await state.lookup(); expect(state.execution).toBeNull();
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
  it("reopening reads the original request, then observes it without any write", async () => {
    app.unmount(); mocks.policy.mockClear(); mount({ visible: false, requestId });
    await flush(); expect(mocks.find).not.toHaveBeenCalled();
    app._instance!.props.visible = true; await flush();
    expect(mocks.find).toHaveBeenCalledExactlyOnceWith(requestId);
    expect(state.execution?.status).toBe("accepted");
    await vi.advanceTimersByTimeAsync(1500); expect(mocks.find).toHaveBeenCalledTimes(2);
    expect(mocks.policy).not.toHaveBeenCalled(); expect(mocks.start).not.toHaveBeenCalled();
    expect(mocks.boundStart).not.toHaveBeenCalled(); expect(mocks.persist).not.toHaveBeenCalled();
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
