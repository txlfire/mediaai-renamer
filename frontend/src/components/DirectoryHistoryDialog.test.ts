import { createRenderer, nextTick, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectoryPlanHistoryItem } from "../api/cloudDirectory";
import { invalidateSessionRequests } from "../utils/sessionGeneration";
const api = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("../api/cloudDirectory", () => ({ fetchDirectoryPlanHistory: api.list }));
import DirectoryHistoryDialog from "./DirectoryHistoryDialog.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const id = "12345678-1234-1234-1234-123456789abc";
const item: DirectoryPlanHistoryItem = { id, requestId: id, kind: "inventory", parentPath: "/cloud/movies", status: "prepared",
  createdAt: 1792100000, expiresAt: 1792101800, itemCount: 2, executionRequestId: null, executionStatus: null, executionReason: null,
  recoveryRequestId: null, usedRpc: null, maxRpc: null };
const data = { items: [item], page: 1, pageSize: 20, total: 1, networkAccess: false, replayed: false };
type State = { rows: DirectoryPlanHistoryItem[]; page: number; errorText: string; busy: boolean;
  load: () => Promise<void>; choose: (value: DirectoryPlanHistoryItem) => void; close: () => void };
let app: ReturnType<typeof renderer.createApp>, state: State;
const selected = vi.fn();
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick(); };
function mount(visible = true) {
  app = renderer.createApp(DirectoryHistoryDialog, { visible, onSelected: selected }); app.provide(ssrContextKey, { modules: new Set() });
  app.config.warnHandler = () => {}; app.mount({ children: [] }); state = (app._instance as unknown as { setupState: State }).setupState;
}
describe("local directory plan history", () => {
  beforeEach(async () => { vi.clearAllMocks(); api.list.mockReset().mockResolvedValue(data); mount(); await flush(); });
  afterEach(() => { app.unmount(); });
  it("queries only a bounded history page and chooses an already displayed lookup marker", () => {
    expect(api.list).toHaveBeenCalledExactlyOnceWith(1, 20); expect(state.rows).toEqual([item]);
    state.choose(item); expect(selected).toHaveBeenCalledExactlyOnceWith(item);
    state.choose({ ...item, id: "22345678-1234-1234-1234-123456789abc" }); expect(selected).toHaveBeenCalledTimes(1);
  });
  it("does not load while closed and fences old responses after close", async () => {
    app.unmount(); mount(false); await flush(); expect(api.list).toHaveBeenCalledTimes(1);
    app.unmount(); let resolve!: (value: typeof data) => void;
    api.list.mockImplementationOnce(() => new Promise(done => { resolve = done; })); mount(); state.close(); resolve(data); await flush();
    expect(state.rows).toEqual([]);
  });
  it("rejects duplicate markers, huge dates, foreign response claims and excessive rows", async () => {
    for (const response of [{ ...data, items: [item, item] }, { ...data, items: [{ ...item, createdAt: 1e20 }] },
      { ...data, networkAccess: true }, { ...data, items: new Array(1000).fill(item) }]) {
      api.list.mockResolvedValueOnce(response); await state.load(); expect(state.rows).toEqual([]); expect(state.errorText).toBeTruthy();
    }
  });
  it("session changes cannot show or navigate old-user history", async () => {
    invalidateSessionRequests(); state.choose(item); await state.load(); expect(selected).not.toHaveBeenCalled(); expect(api.list).toHaveBeenCalledTimes(1);
  });
});
