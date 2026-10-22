import { createRenderer, h, nextTick, reactive, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateSessionRequests } from "../utils/sessionGeneration";
import type { InventoryAdviceItem } from "../api/cloudInventory";
const api = vi.hoisted(() => ({ find: vi.fn(), list: vi.fn(), save: vi.fn(), confirm: vi.fn(), csv: vi.fn(() => "synthetic-csv") }));
vi.mock("../api/cloudAdReview", () => ({ findAdReview: api.find, listAdReviews: api.list, saveAdReview: api.save }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: api.confirm } }));
vi.mock("../stores/auth",()=>({useAuthStore:()=>({hasPermission:()=>true})}));
vi.mock("../utils/cloudAdReviewCsv", () => ({ buildAdReviewCsv: api.csv }));
import AdReviewDialog from "./AdReviewDialog.vue";
type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const id = "12345678-1234-1234-1234-123456789abc";
const file = { id: 1, name: "更多资源.txt", isDirectory: false, directoryComplete: true, reasons: ["promotional_phrase"], protection: null } as InventoryAdviceItem;
const record = { id, requestId: id, items: [] };
type Props = { visible: boolean; jobId: string; revision: number; items: InventoryAdviceItem[]; available: boolean; requestId: string;
  initialHistory: boolean; persistRequestId: (value: string) => Promise<void> };
type State = { selected: number[]; confirmed: boolean; maySave: boolean; busy: boolean; result: unknown; errorText: string;
  quarantineSelection:number[];selectQuarantine:(items:unknown[])=>void;prepareQuarantine:()=>void;
  draft: Record<number, { decision: string; reason: string }>; selectionChanged: (items: InventoryAdviceItem[]) => void;
  save: () => Promise<void>; loadRecord: () => Promise<void>; close: () => void; newDraft: () => Promise<void>;
  tab: string; history: { items: { requestId: string }[] } | null; choose: (id: string) => Promise<void>;
  loadHistory: () => Promise<void>; exportRecord: () => void; visibleResult: unknown };
let app: ReturnType<typeof renderer.createApp>, props: Props, state: State;
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); await nextTick(); };
function mount(values: Partial<Props> = {}) {
  props = reactive({ visible: true, jobId: id, revision: 7, items: [file], available: true, requestId: "", initialHistory: false,
    persistRequestId: vi.fn(async (value: string) => { props.requestId = value; }), ...values });
  app = renderer.createApp({ setup: () => () => h(AdReviewDialog, props) }); app.provide(ssrContextKey, { modules: new Set() });
  app.config.warnHandler = () => {}; app.mount({ children: [] });
  state = (app._instance as unknown as { subTree: { component: { setupState: State } } }).subTree.component.setupState;
}
describe("ad review dialog", () => {
  beforeEach(() => { vi.clearAllMocks(); api.save.mockReset(); api.find.mockReset(); api.list.mockReset().mockResolvedValue({ items: [], total: 0 }); api.confirm.mockResolvedValue("confirm"); mount(); });
  afterEach(() => { app.unmount(); vi.unstubAllGlobals(); });
  it("does not select files by default and requires per-item reasons and explicit confirmation", () => {
    expect(state.selected).toEqual([]); expect(state.maySave).toBe(false);
    state.selectionChanged([file]); state.draft[1]!.decision = "suggest_delete"; state.confirmed = true;
    expect(state.maySave).toBe(false); state.draft[1]!.reason = "人工核对理由"; expect(state.maySave).toBe(true);
  });
  it("quarantine selection excludes protected, unreviewed or stale files and is never implicit",async()=>{
    const reviewed={...record,sourceCurrent:true,items:[{entryId:1,decision:"suggest_quarantine",protection:null,remoteId:"id",evidence:["promotional_phrase"]},
      {entryId:2,decision:"keep",protection:null,remoteId:"id2",evidence:["promotional_phrase"]},
      {entryId:3,decision:"suggest_quarantine",protection:"artwork",remoteId:"id3",evidence:["promotional_phrase"]}]};
    api.find.mockResolvedValueOnce(reviewed);props.requestId=id;await flush();expect(state.quarantineSelection).toEqual([]);
    state.selectQuarantine(reviewed.items);expect(state.quarantineSelection).toEqual([1]);
    state.result={...reviewed,sourceCurrent:false};await flush();state.selectQuarantine(reviewed.items);expect(state.quarantineSelection).toEqual([]);
  });
  it("persists a request marker before posting only the selected fixed IDs", async () => {
    api.save.mockImplementationOnce(async (input) => ({ ...record, requestId: input.requestId }));
    state.selectionChanged([file]); state.confirmed = true; await state.save();
    expect(props.persistRequestId).toHaveBeenCalledOnce(); expect(api.save).toHaveBeenCalledOnce();
    expect(api.save.mock.calls[0]![0]).toMatchObject({ jobId: id, expectedRevision: 7, items: [{ entryId: 1, decision: "review", reason: "" }], confirmLocalOnly: true });
  });
  it("never reposts an unknown save and keeps the UUID for explicit lookup", async () => {
    api.save.mockRejectedValueOnce(new Error("synthetic timeout"));
    state.selectionChanged([file]); state.confirmed = true; await state.save(); await state.save();
    expect(props.requestId).toBeTruthy(); expect(api.save).toHaveBeenCalledOnce(); expect(state.errorText).toBeTruthy();
  });
  it("does not post if the router cannot save the request marker", async () => {
    props.persistRequestId = vi.fn().mockRejectedValueOnce(new Error("route failure"));
    await flush();
    state.selectionChanged([file]); state.confirmed = true; await state.save(); expect(api.save).not.toHaveBeenCalled();
  });
  it("uses GET only when reopening an existing request and fences late results on close", async () => {
    app.unmount(); let resolve!: (value: typeof record) => void;
    api.find.mockImplementationOnce(() => new Promise(done => { resolve = done; })); mount({ requestId: id });
    state.close(); resolve(record); await flush(); expect(state.result).toBe(null); expect(api.save).not.toHaveBeenCalled();
  });
  it("does not load while hidden and opens history without a cloud request", async () => {
    app.unmount(); mount({ visible: false, requestId: id }); await flush(); expect(api.find).not.toHaveBeenCalled();
    app.unmount(); mount({ initialHistory: true }); await flush(); expect(api.list).toHaveBeenCalledExactlyOnceWith(1);
  });
  it("blocks submissions after a session change", async () => {
    state.selectionChanged([file]); state.confirmed = true; invalidateSessionRequests(); await state.save();
    expect(api.save).not.toHaveBeenCalled(); expect(props.persistRequestId).not.toHaveBeenCalled();
  });
  it("ignores foreign file objects in selection and respects unavailable observations", async () => {
    state.selectionChanged([{ ...file, id: 999 }]); expect(state.selected).toEqual([]);
    props.available = false; await flush(); state.selectionChanged([file]); state.confirmed = true;
    expect(state.maySave).toBe(false);
  });
  it("invalidates old result when browser navigation changes the request marker", async () => {
    api.find.mockResolvedValue(record); props.requestId = id; await flush(); expect(state.result).toEqual(record);
    const nextId = "22345678-1234-1234-1234-123456789abc";
    api.find.mockResolvedValueOnce({ ...record, requestId: nextId }); props.requestId = nextId; await flush();
    expect(api.find).toHaveBeenLastCalledWith(nextId); expect(state.result).toHaveProperty("requestId", nextId);
  });
  it("clears the old fixed result if switching history fails after saving a new marker", async () => {
    api.find.mockResolvedValueOnce(record); props.requestId = id; await flush();
    expect(state.result).toEqual(record);
    state.tab = "history"; await flush();
    const nextId = "22345678-1234-1234-1234-123456789abc";
    state.history = { items: [{ requestId: nextId }] };
    api.find.mockRejectedValueOnce(new Error("503"));
    await state.choose(nextId);
    expect(props.requestId).toBe(nextId); expect(state.result).toBe(null);
    expect(state.errorText).toBeTruthy();
    state.exportRecord(); expect(api.csv).not.toHaveBeenCalled();
  });
  it("keeps the old result on cancelled history selection but clears it on navigation failure", async () => {
    api.find.mockResolvedValueOnce(record); props.requestId = id; await flush();
    state.tab = "history"; await flush();
    const nextId = "22345678-1234-1234-1234-123456789abc";
    state.history = { items: [{ requestId: nextId }] };
    api.confirm.mockRejectedValueOnce("cancel"); await state.choose(nextId);
    expect(state.result).toEqual(record); expect(props.requestId).toBe(id);
    props.persistRequestId = vi.fn().mockRejectedValueOnce(new Error("route failed")); await flush();
    await state.choose(nextId);
    expect(state.result).toBe(null); expect(state.errorText).toBeTruthy();
    expect(api.find).toHaveBeenCalledTimes(1);
  });
  it("never displays or exports a result whose request ID differs from the current marker", async () => {
    api.find.mockResolvedValueOnce(record); props.requestId = id; await flush();
    state.result = { ...record, requestId: "22345678-1234-1234-1234-123456789abc" };
    expect(state.visibleResult).toBe(null);
    state.exportRecord(); expect(api.csv).not.toHaveBeenCalled();
  });
  it("preserves an in-flight history page when source observations arrive", async () => {
    let resolve!: (value: { items: { requestId: string }[]; total: number }) => void;
    api.list.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    state.tab = "history"; await flush(); expect(state.busy).toBe(true);
    props.items = [{ ...file, id: 2 }]; props.revision += 1; await flush();
    expect(state.busy).toBe(true);
    const history = { items: [{ requestId: id }], total: 1 };
    resolve(history); await flush(); expect(state.history).toEqual(history); expect(state.busy).toBe(false);
  });
  it("fences late history after closing or changing sessions", async () => {
    for (const action of [() => state.close(), () => invalidateSessionRequests()]) {
      app.unmount(); mount();
      let resolve!: (value: { items: { requestId: string }[]; total: number }) => void;
      api.list.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
      state.tab = "history"; await flush(); action();
      resolve({ items: [{ requestId: id }], total: 1 }); await flush(); expect(state.history).toBe(null);
    }
  });
  it.each(["GET", "POST"])("does not overwrite a reopened record with a late %s result", async method => {
    let resolve!: (value: typeof record & { sourceCurrent: boolean }) => void;
    let pending: Promise<void> | undefined;
    if (method === "GET") {
      api.find.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
      props.requestId = id; await flush();
    } else {
      api.save.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
      state.selectionChanged([file]); state.confirmed = true;
      pending = state.save(); await flush();
    }
    const marker = props.requestId;
    props.visible = false; await flush();
    const latest = { ...record, requestId: marker, sourceCurrent: true };
    api.find.mockResolvedValueOnce(latest);
    props.visible = true; await flush(); expect(state.result).toEqual(latest);
    resolve({ ...record, requestId: marker, sourceCurrent: false });
    await pending; await flush(); expect(state.result).toEqual(latest);
  });
  it.each(["close", "session"])("does not persist history selection after %s during confirmation", async action => {
    api.find.mockResolvedValueOnce(record); props.requestId = id; await flush();
    state.tab = "history"; await flush();
    const nextId = "22345678-1234-1234-1234-123456789abc";
    state.history = { items: [{ requestId: nextId }] };
    let confirm!: (value: string) => void;
    api.confirm.mockImplementationOnce(() => new Promise(done => { confirm = done; }));
    const pending = state.choose(nextId); await flush();
    if (action === "close") state.close(); else invalidateSessionRequests();
    confirm("confirm"); await pending;
    expect(props.persistRequestId).not.toHaveBeenCalled(); expect(api.find).toHaveBeenCalledTimes(1);
  });
  it("shows a history error and releases busy if the delayed request rejects after source refresh", async () => {
    let reject!: (error: Error) => void;
    api.list.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    state.tab = "history"; await flush();
    props.items = [{ ...file }]; props.revision += 1; await flush();
    reject(new Error("synthetic-503")); await flush();
    expect(state.history).toBe(null); expect(state.busy).toBe(false); expect(state.errorText).toBeTruthy();
  });
  it("does not let late B overwrite A after own save and A to B to A navigation", async () => {
    api.save.mockImplementationOnce(async input => ({ ...record, requestId: input.requestId }));
    state.selectionChanged([file]); state.confirmed = true; await state.save();
    const marker = props.requestId;
    const nextId = "22345678-1234-1234-1234-123456789abc";
    let resolve!: (value: typeof record) => void;
    api.find.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    props.requestId = nextId; await flush();
    const latest = { ...record, requestId: marker };
    api.find.mockResolvedValueOnce(latest); props.requestId = marker; await flush();
    resolve({ ...record, requestId: nextId }); await flush();
    expect(state.result).toEqual(latest); expect(api.save).toHaveBeenCalledTimes(1);
  });
});
