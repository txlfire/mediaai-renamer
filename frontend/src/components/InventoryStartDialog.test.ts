import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InventoryPolicy, InventorySummary } from "../api/cloudInventory";
import { invalidateSessionRequests, SessionChangedError } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ policy: vi.fn(), prepare: vi.fn(), start: vi.fn(), lookup: vi.fn(),
  persist: vi.fn(), observed: vi.fn(), visible: vi.fn(), info: vi.fn(), confirm: vi.fn() }));
vi.mock("../api/cloudInventory", async original => ({ ...await original<object>(),
  fetchInventoryPolicy: mocks.policy, prepareInventoryJob: mocks.prepare, startInventoryJob: mocks.start, findInventoryRequest: mocks.lookup }));
vi.mock("element-plus", () => ({ ElMessage: { info: mocks.info }, ElMessageBox: { confirm: mocks.confirm } }));
import { InventoryRequestError } from "../api/cloudInventory";
import InventoryStartDialog from "./InventoryStartDialog.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const request = "12345678-1234-1234-1234-123456789abc";
const jobId = "22345678-1234-1234-1234-123456789abc";
const policy: InventoryPolicy = { available: true, reason: null, approvedRoot: "/cloud/movies", policyHash: "a".repeat(64),
  limits: { rpc: 4, seconds: 30, entries: 100, bytes: 1048576, directories: 1, depth: 0 }, networkAccess: false, writeAvailable: false };
const prepared = (): InventorySummary => ({ id: jobId, status: "queued", revision: 0, mode: "overview", approvedRoot: "/cloud/movies/category",
  selectedPaths: ["/cloud/movies/category"], excludedPaths: [], workerSlotHeld: false, writeAvailable: false,
  reason: null, storedEntries: 0, receivedEntries: 0, receivedBytes: 0, completedDirectories: 0, directoryStates: { pending: 1 },
  cancelRequested: false, estimatedTotalEntries: null, scopeComplete: false, executionAvailable: false, cacheState: "available",
  clearedAt: null, cacheClearAvailable: false, createdAt: Date.now() / 1000, updatedAt: Date.now() / 1000,
  storedBytes: 0, remainingEntries: 100, remainingReceiveBytes: 1048576, freshness: "unknown",
  limits: { entries: 100, bytes: 1048576, directories: 1, depth: 0 },
  budget: { maxRpc: 4, usedRpc: 0, remainingRpc: 4, status: "active", expired: false, expiresAt: Date.now() / 1000 + 30 } });
type State = { tokenValue: string; selectedPath: string; confirmed: boolean; requestId: string; canSubmit: boolean;
  phase: string; errorText: string; policy: InventoryPolicy | null; querying: boolean; missingRequest: boolean;
  submit: () => Promise<void>; lookup: () => Promise<void>; loadPolicy: () => Promise<void>;
  closeDialog: () => void; acknowledgeMissing: () => Promise<void>; viewQueriedJob: () => void };
let app: ReturnType<typeof renderer.createApp>, state: State;
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); await nextTick(); };
function fill() { state.selectedPath = "/cloud/movies/category"; state.tokenValue = "synthetic-local-token"; state.confirmed = true; }

describe("controlled inventory secret and intent lifecycle", () => {
  beforeEach(async () => {
    vi.stubGlobal("location", { protocol: "https:" });
    vi.stubGlobal("crypto", { randomUUID: () => request });
    vi.stubGlobal("window", new EventTarget());
    vi.stubGlobal("document", Object.assign(new EventTarget(), { hidden: false }));
    vi.clearAllMocks();
    mocks.policy.mockReset().mockResolvedValue(policy);
    mocks.prepare.mockReset().mockImplementation(async () => prepared());
    mocks.start.mockReset().mockImplementation(async () => ({ ...prepared(), status: "running", revision: 1, workerSlotHeld: true }));
    mocks.lookup.mockReset().mockImplementation(async () => ({ ...prepared(), status: "completed", revision: 2 }));
    mocks.persist.mockReset().mockResolvedValue(undefined);
    mocks.confirm.mockReset().mockResolvedValue("confirm");
    app = renderer.createApp(InventoryStartDialog, { visible: true, requestId: "", persistRequestId: mocks.persist,
      onObserved: mocks.observed, "onUpdate:visible": mocks.visible });
    app.provide(ssrContextKey, { modules: new Set() });
    app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { setupState: State }).setupState;
    await flush();
  });
  afterEach(() => { app.unmount(); vi.unstubAllGlobals(); });

  it("loads local policy only and requires a valid root, token and confirmation", async () => {
    expect(mocks.policy).toHaveBeenCalledOnce(); expect(mocks.prepare).not.toHaveBeenCalled();
    expect(state.canSubmit).toBe(false);
    fill(); expect(state.canSubmit).toBe(true);
    for (const path of ["/cloud/movies2", "/cloud/movies/../other", "/cloud/movies/%2f", "/cloud/movies//x", "/cloud/movies/\ud800"]) {
      state.selectedPath = path; expect(state.canSubmit).toBe(false);
    }
    fill(); state.tokenValue = "token with spaces"; expect(state.canSubmit).toBe(false);
    fill(); state.confirmed = false; await state.submit(); expect(mocks.prepare).not.toHaveBeenCalled();
  });
  it("persists a non-secret id before preparing, sends one handoff and clears the input immediately", async () => {
    fill();
    const run = state.submit();
    expect(state.tokenValue).toBe("");
    expect(mocks.persist).toHaveBeenCalledWith(request);
    await run;
    expect(mocks.prepare).toHaveBeenCalledWith({ requestId: request, selectedPath: "/cloud/movies/category", policyHash: policy.policyHash, confirmRead: true });
    expect(mocks.start).toHaveBeenCalledOnce();
    expect(mocks.start).toHaveBeenCalledWith(jobId, 0, policy.policyHash, "synthetic-local-token");
    expect(mocks.observed).toHaveBeenCalledWith(expect.objectContaining({ id: jobId, status: "running" }));
    expect(mocks.persist).toHaveBeenLastCalledWith("");
    expect(state.requestId).toBe(""); expect(state.confirmed).toBe(false);
  });
  it("does not create anything when the request marker cannot be saved", async () => {
    mocks.persist.mockRejectedValueOnce(new Error("navigation"));
    fill(); await state.submit();
    expect(mocks.prepare).not.toHaveBeenCalled(); expect(mocks.start).not.toHaveBeenCalled();
    expect(state.phase).toBe("unknown"); expect(state.requestId).toBe(request);
  });
  it("refuses HTTP before collecting or submitting secrets", async () => {
    vi.stubGlobal("location", { protocol: "http:" });
    app.unmount();
    app = renderer.createApp(InventoryStartDialog, { visible: true, requestId: "", persistRequestId: mocks.persist });
    app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
    app.mount({ children: [] }); state = (app._instance as unknown as { setupState: State }).setupState;
    await flush(); fill(); await state.submit();
    expect(state.canSubmit).toBe(false); expect(mocks.prepare).not.toHaveBeenCalled(); expect(mocks.start).not.toHaveBeenCalled();
  });
  it("never starts after prepare becomes unknown and lookup only observes a queued intent", async () => {
    mocks.prepare.mockRejectedValueOnce(new Error("synthetic-local-token"));
    fill(); await state.submit();
    expect(state.phase).toBe("unknown"); expect(state.errorText).not.toContain("synthetic-local-token");
    fill(); await state.submit(); expect(mocks.prepare).toHaveBeenCalledOnce();
    mocks.lookup.mockResolvedValueOnce(prepared()); await state.lookup();
    expect(mocks.lookup).toHaveBeenCalledWith(request); expect(mocks.start).not.toHaveBeenCalled();
    expect(state.requestId).toBe(request); expect(state.errorText).toContain("待执行");
    state.viewQueriedJob(); expect(mocks.observed).toHaveBeenCalledWith(expect.objectContaining({ id: jobId }));
  });
  it("never resends an unknown start and explicit lookup resolves the original result", async () => {
    mocks.start.mockRejectedValueOnce(new Error("synthetic-local-token"));
    fill(); await state.submit(); expect(state.phase).toBe("unknown");
    fill(); await state.submit(); expect(mocks.start).toHaveBeenCalledOnce();
    await state.lookup(); expect(mocks.start).toHaveBeenCalledOnce();
    expect(mocks.observed).toHaveBeenLastCalledWith(expect.objectContaining({ status: "completed" }));
    expect(state.requestId).toBe("");
  });
  it.each(["close", "unmount", "session"])("late prepare cannot hand off after %s", async kind => {
    let resolve!: (value: InventorySummary) => void;
    mocks.prepare.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    fill(); const pending = state.submit(); await flush();
    if (kind === "close") state.closeDialog();
    else if (kind === "unmount") app.unmount();
    else invalidateSessionRequests();
    resolve(prepared()); await pending;
    expect(mocks.start).not.toHaveBeenCalled(); expect(state.tokenValue).toBe("");
    expect(state.requestId).toBe(request);
  });
  it("late start after closing cannot clear the recovery id or apply an old result", async () => {
    let resolve!: (value: InventorySummary) => void;
    mocks.start.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    fill(); const pending = state.submit(); await flush();
    state.closeDialog(); resolve({ ...prepared(), status: "running" }); await pending;
    expect(mocks.observed).not.toHaveBeenCalled(); expect(state.requestId).toBe(request);
    expect(mocks.persist).toHaveBeenCalledTimes(1);
  });
  it("rejects mismatched, expired or incomplete preparation before sending secrets", async () => {
    mocks.prepare.mockResolvedValueOnce({ ...prepared(), budget: { ...prepared().budget, expired: true } });
    fill(); await state.submit();
    expect(mocks.start).not.toHaveBeenCalled(); expect(state.phase).toBe("unknown");
  });
  it("revokes old confirmation and token on policy refresh or invalid policy", async () => {
    fill(); await state.loadPolicy(); expect(state.tokenValue).toBe(""); expect(state.confirmed).toBe(false);
    mocks.policy.mockResolvedValueOnce({ ...policy, limits: { ...policy.limits, depth: 1 } });
    await state.loadPolicy(); expect(state.policy).toBe(null); expect(state.canSubmit).toBe(false);
  });
  it("keeps unknown requests across a failed lookup and acknowledges 404 only explicitly", async () => {
    mocks.prepare.mockRejectedValueOnce(new Error("synthetic")); fill(); await state.submit();
    mocks.lookup.mockRejectedValueOnce(new InventoryRequestError("safe", { response: { status: 404 } }));
    await state.lookup(); expect(state.missingRequest).toBe(true); expect(state.requestId).toBe(request);
    expect(mocks.start).not.toHaveBeenCalled();
    mocks.confirm.mockRejectedValueOnce("cancel"); await state.acknowledgeMissing(); expect(state.requestId).toBe(request);
    await state.acknowledgeMissing(); expect(state.requestId).toBe("");
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.prepare).toHaveBeenCalledOnce();
  });
  it("does not lose the request marker if clearing it fails after known acceptance", async () => {
    mocks.persist.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("navigation"));
    fill(); await state.submit(); expect(state.requestId).toBe(request); expect(state.phase).toBe("unknown");
    expect(mocks.start).toHaveBeenCalledOnce();
  });
  it("blocks a stale session before preparation and suppresses stale-session details", async () => {
    fill(); invalidateSessionRequests(); await state.submit();
    expect(state.tokenValue).toBe(""); expect(mocks.prepare).not.toHaveBeenCalled();
    expect(state.errorText).toContain("登录会话");
    mocks.lookup.mockRejectedValueOnce(new SessionChangedError());
  });
  it("restores a request marker without preparing, starting or querying automatically", async () => {
    app._instance!.props.requestId = request; await flush();
    expect(state.requestId).toBe(request); expect(state.phase).toBe("unknown");
    expect(mocks.prepare).not.toHaveBeenCalled(); expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.lookup).not.toHaveBeenCalled();
    await state.lookup(); expect(mocks.lookup).toHaveBeenCalledWith(request); expect(mocks.start).not.toHaveBeenCalled();
  });
  it("handles its own URL updates without cancelling the accepted result", async () => {
    mocks.persist.mockImplementation(async (id: string) => { app._instance!.props.requestId = id; });
    fill(); await state.submit();
    expect(mocks.start).toHaveBeenCalledOnce(); expect(mocks.observed).toHaveBeenCalledOnce();
    expect(state.phase).toBe("idle"); expect(state.requestId).toBe("");
  });
  it.each(["pagehide", "visibilitychange", "mediaai:auth-expired"])("purges secrets and fences late prepare on %s", async name => {
    let resolve!: (value: InventorySummary) => void;
    mocks.prepare.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    fill(); const pending = state.submit(); await flush();
    if (name === "visibilitychange") {
      (document as unknown as { hidden: boolean }).hidden = true;
      document.dispatchEvent(new Event(name));
    } else window.dispatchEvent(new Event(name));
    resolve(prepared()); await pending;
    expect(state.tokenValue).toBe(""); expect(state.phase).toBe("unknown");
    expect(mocks.start).not.toHaveBeenCalled(); expect(state.requestId).toBe(request);
  });
});
