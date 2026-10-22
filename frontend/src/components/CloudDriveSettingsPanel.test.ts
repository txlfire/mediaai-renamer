import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CloudDriveSettingsSnapshot } from "../api/cloudDriveSettings";
import { invalidateSessionRequests } from "../utils/sessionGeneration";

const mocks = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn(), confirm: vi.fn(), success: vi.fn(), auth: null as any }));
vi.mock("../api/cloudDriveSettings", () => ({ fetchCloudDriveSettings: mocks.load, saveCloudDriveSettings: mocks.save }));
vi.mock("../stores/auth", () => ({ useAuthStore: () => mocks.auth }));
vi.mock("element-plus", () => ({ ElMessageBox: { confirm: mocks.confirm }, ElMessage: { success: mocks.success } }));
import Panel from "./CloudDriveSettingsPanel.vue";

type Node = { children: Node[] };
const renderer = createRenderer<Node, Node>({ createElement: () => ({ children: [] }), createText: () => ({ children: [] }),
  createComment: () => ({ children: [] }), insert: (node, parent) => { parent.children.push(node); }, remove: () => {},
  patchProp: () => {}, setText: () => {}, setElementText: () => {}, parentNode: () => null, nextSibling: () => null });
const saved: CloudDriveSettingsSnapshot = { revision: 3, updatedAt: 0, changeBlocked: false, reason: null, caSha256: null,
  source: "database", networkAccess: false, values: { endpoint: "https://cd2.example:19799", approvedRoot: "/cloud/movies",
    caCertificatePem: "", minIntervalSeconds: 2.2, failureCooldownSeconds: 300, webExecutionEnabled: false,
    directoryRenameEnabled: false, directoryMappingRevision: "test-v1", quarantineEnabled: false, quarantineFolderName: "_MediaAI_Quarantine" } };
type State = { snapshot: CloudDriveSettingsSnapshot | null; form: CloudDriveSettingsSnapshot["values"]; busy: boolean;
  error: string; load: () => Promise<void>; save: () => Promise<void> };
let app: ReturnType<typeof renderer.createApp>, state: State;
async function flush() { await Promise.resolve(); await nextTick(); await Promise.resolve(); }
function mount() {
  app = renderer.createApp(Panel); app.provide(ssrContextKey, { modules: new Set() }); app.config.warnHandler = () => {};
  app.mount({ children: [] }); state = (app._instance as unknown as { setupState: State }).setupState;
}
describe("数据库 CD2 设置面板", () => {
  beforeEach(async () => {
    vi.clearAllMocks(); mocks.load.mockReset().mockResolvedValue(structuredClone(saved));
    mocks.save.mockReset().mockResolvedValue({ ...structuredClone(saved), revision: 4 }); mocks.confirm.mockReset().mockResolvedValue("confirm");
    mocks.auth = reactive({ currentUser: { id: 1 }, permitted: true, hasPermission() { return this.permitted; } });
    mount(); await flush();
  });
  afterEach(() => app.unmount());
  it("加载不保存，不自动开启三个开关", () => {
    expect(mocks.load).toHaveBeenCalledOnce(); expect(mocks.save).not.toHaveBeenCalled();
    expect(state.form.endpoint).toBe(saved.values.endpoint); expect(state.form.webExecutionEnabled).toBe(false);
    expect(state.form.directoryRenameEnabled).toBe(false); expect(state.form.quarantineEnabled).toBe(false);
  });
  it("确认后以当前修订保存，不附带 Token 或私钥路径", async () => {
    state.form.webExecutionEnabled = true; await state.save();
    expect(mocks.confirm).toHaveBeenCalledOnce(); expect(mocks.save).toHaveBeenCalledExactlyOnceWith({ ...saved.values, webExecutionEnabled: true }, 3);
    expect(state.snapshot?.revision).toBe(4); expect(mocks.success).toHaveBeenCalledOnce();
  });
  it("取消确认不发送请求", async () => {
    mocks.confirm.mockRejectedValueOnce("cancel"); await state.save(); expect(mocks.save).not.toHaveBeenCalled(); expect(state.error).toBe("");
  });
  it("保护状态不允许保存", async () => {
    mocks.load.mockResolvedValueOnce({ ...saved, changeBlocked: true }); await state.load(); await state.save();
    expect(mocks.confirm).not.toHaveBeenCalled(); expect(mocks.save).not.toHaveBeenCalled();
  });
  it("保存失败保留编辑内容，但必须重新加载版本", async () => {
    mocks.save.mockRejectedValueOnce(new Error("配置已变更")); await state.save();
    expect(state.snapshot).toBeNull(); expect(state.error).toBe("配置已变更"); expect(state.form.endpoint).toBe(saved.values.endpoint);
    await state.save(); expect(mocks.save).toHaveBeenCalledOnce();
  });
  it("等待确认期间权限撤销不保存", async () => {
    let resolve!: (value: string) => void;
    mocks.confirm.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = state.save(); await flush(); mocks.auth.permitted = false; await flush(); resolve("confirm"); await pending;
    expect(mocks.save).not.toHaveBeenCalled(); expect(state.snapshot).toBeNull();
  });
  it("同权限切换用户仍丢弃旧响应", async () => {
    let resolve!: (value: CloudDriveSettingsSnapshot) => void;
    mocks.save.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = state.save(); await flush(); invalidateSessionRequests(); mocks.auth.currentUser = { id: 2 }; await flush();
    resolve({ ...saved, revision: 999 }); await pending; expect(state.snapshot?.revision).toBe(3); expect(mocks.success).not.toHaveBeenCalled();
  });
  it("卸载期间确认完成不发送请求", async () => {
    let resolve!: (value: string) => void;
    mocks.confirm.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = state.save(); await flush(); app.unmount(); resolve("confirm"); await pending; expect(mocks.save).not.toHaveBeenCalled();
  });
});
