import { createRenderer, h, nextTick, reactive, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PagedRecordSelect from "./PagedRecordSelect.vue";

type Item = { id: number; name: string };
type Page = { items: Item[]; total: number; page: number };
type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }),
  createText: () => ({ children: [] }), createComment: () => ({ children: [] }),
  insert: (node, parent) => { parent.children.push(node); }, remove: () => {}, patchProp: () => {},
  setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const load = vi.fn(), selectedLoad = vi.fn(), changed = vi.fn(), selectedEvent = vi.fn();
let app: ReturnType<typeof renderer.createApp>;
let props: { modelValue?: number; contextKey: string; loadPage: typeof load; loadSelected: typeof selectedLoad;
  optionLabel: (item: Item) => string; "onUpdate:modelValue": (value?: number) => void; onChange: typeof changed; onSelected: typeof selectedEvent };
type State = { rows: Item[]; selected: Item | null; total: number; page: number; loading: boolean;
  error: string; loadPage: (page?: number) => Promise<void>; resolveSelection: () => Promise<void>;
  setValue: (value: unknown) => void; search: (value: string) => void; openDropdown: (open: boolean) => void };
let state: State;
function deferred<T>() {
  let resolve!: (result: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
describe("bounded dropdown requests", () => {
  beforeEach(async () => {
    load.mockReset().mockResolvedValue({ items: [], total: 0, page: 1 });
    selectedLoad.mockReset().mockResolvedValue({ id: 99999, name: "historic" });
    changed.mockReset(); selectedEvent.mockReset();
    props = reactive({ modelValue: undefined as number | undefined, contextKey: "source-1", loadPage: load,
      loadSelected: selectedLoad, optionLabel: (item: Item) => item.name,
      "onUpdate:modelValue": (value?: number) => { props.modelValue = value; }, onChange: changed, onSelected: selectedEvent });
    app = renderer.createApp({ render: () => h(PagedRecordSelect<Item>, props) });
    app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
    app.mount({ children: [] });
    state = (app._instance as unknown as { subTree: { component: { setupState: State } } }).subTree.component.setupState;
    await nextTick();
  });
  afterEach(() => app.unmount());
  it("does not preload lists and replaces each page instead of accumulating", async () => {
    expect(load).not.toHaveBeenCalled();
    load.mockImplementation((page) => Promise.resolve({ page, total: 100000,
      items: Array.from({ length: 50 }, (_, index) => ({ id: page * 50 + index, name: String(index) })) }));
    await state.loadPage(1); await state.loadPage(2);
    expect(state.rows).toHaveLength(50); expect(state.rows[0].id).toBe(100); expect(state.total).toBe(100000);
  });
  it("keeps the latest search response", async () => {
    const first = deferred<Page>(), second = deferred<Page>();
    load.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const old = state.loadPage(); state.search("new");
    second.resolve({ page: 1, total: 1, items: [{ id: 2, name: "new" }] }); await nextTick(); await nextTick();
    first.resolve({ page: 1, total: 1, items: [{ id: 1, name: "old" }] }); await old;
    expect(state.rows.map(item => item.id)).toEqual([2]); expect(load).toHaveBeenLastCalledWith(1, "new");
  });
  it("context changes invalidate an old source page", async () => {
    const pending = deferred<Page>(); load.mockReturnValueOnce(pending.promise);
    const request = state.loadPage(); props.contextKey = "source-2"; await nextTick();
    pending.resolve({ page: 1, total: 1, items: [{ id: 1, name: "wrong source" }] }); await request;
    expect(state.rows).toEqual([]); expect(state.loading).toBe(false);
  });
  it("pins the selected historical record with a point lookup and normalizes clear", async () => {
    props.modelValue = 99999; await nextTick(); await nextTick();
    expect(selectedLoad).toHaveBeenCalledWith(99999); expect(load).not.toHaveBeenCalled();
    expect(state.selected?.name).toBe("historic");
    state.setValue(""); await nextTick();
    expect(props.modelValue).toBeUndefined(); expect(changed).toHaveBeenLastCalledWith(undefined);
  });
  it("rejects late selected records when the model changes", async () => {
    const first = deferred<Item>(), second = deferred<Item>();
    selectedLoad.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    props.modelValue = 1; await nextTick(); props.modelValue = 2; await nextTick();
    second.resolve({ id: 2, name: "new" }); await nextTick(); await nextTick();
    first.resolve({ id: 1, name: "old" }); await nextTick(); await nextTick();
    expect(state.selected?.id).toBe(2);
  });
  it("shows failure, retries explicitly and ignores unmounted responses", async () => {
    load.mockRejectedValueOnce(new Error("secret raw failure")); await state.loadPage();
    expect(state.error).toBe("选项加载失败"); expect(state.rows).toEqual([]);
    await state.loadPage(); expect(state.error).toBe("");
    const pending = deferred<Page>(); load.mockReturnValueOnce(pending.promise);
    const request = state.loadPage(); app.unmount();
    pending.resolve({ page: 1, total: 1, items: [{ id: 1, name: "late" }] }); await request;
    expect(state.rows).toEqual([]);
  });
});
