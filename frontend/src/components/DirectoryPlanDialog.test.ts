import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectoryPlan } from "../api/cloudDirectory";
import { invalidateSessionRequests } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ prepare: vi.fn(), lookup: vi.fn(), cancel: vi.fn(), persist: vi.fn(), confirm: vi.fn(), reverse: vi.fn(), persistReverse: vi.fn() }));
vi.mock("../api/cloudDirectory", () => ({ prepareDirectoryPlan: mocks.prepare, findDirectoryPlan: mocks.lookup,
  cancelDirectoryPlan: mocks.cancel, prepareReverseDirectoryPlan: mocks.reverse }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: mocks.confirm } }));
import DirectoryPlanDialog from "./DirectoryPlanDialog.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const requestId = "12345678-1234-1234-1234-123456789abc";
const jobId = "22345678-1234-1234-1234-123456789abc";
const planId = "32345678-1234-1234-1234-123456789abc";
const plan: DirectoryPlan = { id: planId, requestId, inventoryJobId: jobId, inventoryRevision: 7,
  revision: 0, status: "prepared", valid: true, invalidReason: null, parentPath: "/cloud/movies",
  ruleVersion: "metadata-advice-v1", policyHash: "a".repeat(64), rulesHash: "b".repeat(64),
  createdAt: 1792100000, expiresAt: 1792101800, networkAccess: false, writeAvailable: false,
  items: [{ entryId: 2, generation: 1, remoteId: "test-object", sourcePath: "/cloud/movies/[推广] 示例",
    sourceName: "[推广] 示例", targetName: "示例", targetPath: "/cloud/movies/示例" }] };
type State = { submit: () => Promise<void>; lookup: () => Promise<void>; cancel: () => Promise<void>; acknowledge: () => Promise<void>;
  close: () => void; requestId: string; plan: DirectoryPlan | null; busy: boolean; canCreate: boolean; errorText: string };
type ReverseState = State & { reverse: (id: string, revision: number) => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
function mount(overrides = {}) {
  app = renderer.createApp(DirectoryPlanDialog, { visible: true, requestId: "", jobId, revision: 7,
    entryIds: [2], folderTerms: ["推广"], persistRequestId: mocks.persist, ...overrides });
  app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
  app.mount({ children: [] }); state = (app._instance as unknown as { setupState: State }).setupState;
}

describe("fixed directory plan dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks(); mocks.prepare.mockReset().mockResolvedValue(plan); mocks.lookup.mockReset().mockResolvedValue(plan);
    mocks.cancel.mockReset().mockResolvedValue({ ...plan, revision: 1, status: "cancelled", valid: false, invalidReason: "cancelled" });
    mocks.persist.mockReset().mockResolvedValue(undefined); mocks.confirm.mockResolvedValue("confirm");
    mocks.reverse.mockReset(); mocks.persistReverse.mockReset();
    vi.stubGlobal("crypto", { randomUUID: () => requestId }); mount();
  });
  afterEach(() => { app.unmount(); vi.unstubAllGlobals(); });
  it("persists request marker before fixing a small immutable selection", async () => {
    await state.submit();
    expect(mocks.persist).toHaveBeenCalledWith(requestId);
    expect(mocks.persist.mock.invocationCallOrder[0]).toBeLessThan(mocks.prepare.mock.invocationCallOrder[0]!);
    expect(mocks.prepare).toHaveBeenCalledExactlyOnceWith({ requestId, jobId, expectedRevision: 7, entryIds: [2], folderTerms: ["推广"] });
    expect(state.plan?.id).toBe(planId); expect(state.canCreate).toBe(false);
    await state.submit(); expect(mocks.prepare).toHaveBeenCalledTimes(1);
  });
  it("does not submit if navigation marker fails and retains the identifier", async () => {
    mocks.persist.mockRejectedValueOnce(new Error("navigation")); await state.submit();
    expect(mocks.prepare).not.toHaveBeenCalled(); expect(state.requestId).toBe(requestId); expect(state.errorText).toBeTruthy();
  });
  it("queries the original request after lost response without resubmission", async () => {
    mocks.prepare.mockRejectedValueOnce(new Error("lost-response")); await state.submit();
    expect(state.requestId).toBe(requestId); await state.submit();
    await state.lookup(); expect(mocks.prepare).toHaveBeenCalledTimes(1);
    expect(mocks.lookup).toHaveBeenCalledExactlyOnceWith(requestId); expect(state.plan?.id).toBe(planId);
  });
  it("does not accept changed identities, scope or expanded item selection", async () => {
    mocks.prepare.mockResolvedValueOnce({ ...plan, items: [{ ...plan.items[0], entryId: 99 }] });
    await state.submit(); expect(state.plan).toBeNull(); expect(state.errorText).toBeTruthy();
  });
  it("rejects a lookup that advertises cloud writes or returns the wrong request", async () => {
    app.unmount(); mount({ requestId });
    for (const result of [{ ...plan, writeAvailable: true }, { ...plan, requestId: planId }, { ...plan, expiresAt: Number.NaN }]) {
      mocks.lookup.mockResolvedValueOnce(result); await state.lookup(); expect(state.plan).toBeNull(); expect(state.errorText).toBeTruthy();
    }
    expect(mocks.prepare).not.toHaveBeenCalled();
  });
  it("cancels only the displayed revision and never retries a cancellation", async () => {
    await state.submit(); await state.cancel();
    expect(mocks.cancel).toHaveBeenCalledExactlyOnceWith(planId, 0);
    expect(state.plan?.status).toBe("cancelled"); await state.cancel(); expect(mocks.cancel).toHaveBeenCalledTimes(1);
  });
  it("does not clear a newer plan when an old cancellation rejects late", async () => {
    await state.submit();let reject!:(error:Error)=>void;
    mocks.cancel.mockImplementationOnce(()=>new Promise((_resolve,fail)=>{reject=fail;}));
    const pending=state.cancel();await nextTick();
    const newRequest="62345678-1234-1234-1234-123456789abc",newPlan={...plan,id:jobId,requestId:newRequest};
    app._instance!.props.requestId=newRequest;await nextTick();mocks.lookup.mockResolvedValueOnce(newPlan);await state.lookup();
    reject(new Error("old-cancel"));await pending;
    expect(state.plan?.id).toBe(newPlan.id);expect(state.requestId).toBe(newRequest);expect(state.errorText).toBe("");
  });
  it("does not restore an old marker after a newer plan is opened", async () => {
    await state.submit();let reject!:(error:Error)=>void;
    mocks.persist.mockImplementationOnce(()=>new Promise((_resolve,fail)=>{reject=fail;}));
    const pending=state.acknowledge();await nextTick();await Promise.resolve();
    const newRequest="62345678-1234-1234-1234-123456789abc",newPlan={...plan,id:jobId,requestId:newRequest};
    app._instance!.props.requestId=newRequest;await nextTick();mocks.lookup.mockResolvedValueOnce(newPlan);await state.lookup();
    reject(new Error("old-marker"));await pending;
    expect(state.plan?.id).toBe(newPlan.id);expect(state.requestId).toBe(newRequest);expect(state.errorText).toBe("");
  });
  it("fences a late response after closing and after logout", async () => {
    let resolve!: (value: DirectoryPlan) => void;
    mocks.prepare.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = state.submit(); await nextTick(); await Promise.resolve();
    state.close(); resolve(plan); await pending; expect(state.plan).toBeNull();
    app.unmount(); mount(); invalidateSessionRequests();
    await state.submit(); expect(mocks.prepare).toHaveBeenCalledTimes(1);
  });
  it("clears only the marker after an explicit acknowledgement", async () => {
    await state.submit(); await state.acknowledge();
    expect(mocks.confirm).toHaveBeenCalledOnce(); expect(mocks.persist).toHaveBeenLastCalledWith("");
    expect(mocks.cancel).not.toHaveBeenCalled(); expect(state.requestId).toBe("");
  });
  it("does not prepare an empty, oversized or duplicate selection", async () => {
    for (const entryIds of [[], [2, 2], [1, 2, 3, 4, 5, 6]]) {
      app.unmount(); mount({ entryIds }); await state.submit(); expect(state.canCreate).toBe(false);
    }
    expect(mocks.prepare).not.toHaveBeenCalled();
  });
  it("prepares a separately confirmed inverse plan and never invokes cloud execution", async () => {
    app.unmount(); mount({ persistReverseRequestId: mocks.persistReverse }); await state.submit();
    const reverseRequest = "42345678-1234-1234-1234-123456789abc";
    vi.stubGlobal("crypto", { randomUUID: () => reverseRequest });
    const inverse: DirectoryPlan = { ...plan, id: "52345678-1234-1234-1234-123456789abc", requestId: reverseRequest,
      kind: "reverse", originPlanId: planId, originRevision: 9, basisEpoch: 1,
      items: [{ ...plan.items[0]!, sourceName: plan.items[0]!.targetName, targetName: plan.items[0]!.sourceName,
        sourcePath: plan.items[0]!.targetPath, targetPath: plan.items[0]!.sourcePath }] };
    mocks.reverse.mockResolvedValueOnce(inverse); mocks.persistReverse.mockResolvedValueOnce(undefined);
    await (state as ReverseState).reverse(planId, 9);
    expect(mocks.persistReverse).toHaveBeenCalledWith(reverseRequest);
    expect(mocks.persistReverse.mock.invocationCallOrder[0]).toBeLessThan(mocks.reverse.mock.invocationCallOrder[0]!);
    expect(mocks.reverse).toHaveBeenCalledExactlyOnceWith(planId, { requestId: reverseRequest, expectedRevision: 9 });
    expect(state.plan?.kind).toBe("reverse"); expect(state.plan?.id).toBe(inverse.id);
  });
  it("a lost reverse-plan response leaves only its new marker for local lookup", async () => {
    app.unmount(); mount({ persistReverseRequestId: mocks.persistReverse }); await state.submit();
    const reverseRequest = "42345678-1234-1234-1234-123456789abc"; vi.stubGlobal("crypto", { randomUUID: () => reverseRequest });
    mocks.persistReverse.mockResolvedValueOnce(undefined); mocks.reverse.mockRejectedValueOnce(new Error("lost"));
    await (state as ReverseState).reverse(planId, 9); await state.submit();
    expect(mocks.reverse).toHaveBeenCalledTimes(1); expect(state.requestId).toBe(reverseRequest); expect(state.plan).toBeNull();
    expect(mocks.prepare).toHaveBeenCalledTimes(1);
  });
  it("a switched request marker fences the response from the previous plan", async () => {
    let resolve!: (value: DirectoryPlan) => void;
    mocks.prepare.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = state.submit(); await nextTick(); await Promise.resolve();
    app._instance!.props.requestId = "62345678-1234-1234-1234-123456789abc";
    await nextTick(); resolve(plan); await pending;
    expect(state.plan).toBeNull(); expect(state.requestId).toBe("62345678-1234-1234-1234-123456789abc");
  });
});
