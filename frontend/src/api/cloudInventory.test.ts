import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { zhCnMessages as messages } from "../locales/zh-CN";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("./client", () => ({ apiClient: http }));
import { cancelInventoryJob, clearInventoryCache, setInventoryKeep, fetchInventoryAdvice, fetchInventoryJobs, fetchInventorySummary, fetchInventoryDirectories } from "./cloudInventory";
import { fetchInventoryPolicy, prepareInventoryJob, findInventoryRequest, startInventoryJob, inventoryRequestId } from "./cloudInventory";
import { SessionChangedError } from "../utils/sessionGeneration";

describe("local inventory API contracts", () => {
  beforeEach(() => { vi.clearAllMocks(); http.get.mockReset().mockResolvedValue({ data: {} }); http.post.mockReset().mockResolvedValue({ data: {} }); http.put.mockReset().mockResolvedValue({ data: {} }); });
  afterEach(() => vi.unstubAllGlobals());
  it("prepares without secrets and queries the original request without executing it", async () => {
    await fetchInventoryPolicy();
    expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/inventory-policy");
    const input = { requestId: "12345678-1234-1234-1234-123456789abc", selectedPath: "/cloud/movies", policyHash: "a".repeat(64), confirmRead: true as const };
    await prepareInventoryJob(input);
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs", input);
    http.post.mockClear();
    await findInventoryRequest(input.requestId);
    expect(http.get).toHaveBeenLastCalledWith(`/cloud-drive/inventory-requests/${input.requestId}`);
    expect(http.post).not.toHaveBeenCalled();
    expect(inventoryRequestId(input.requestId)).toBe(input.requestId);
    for (const invalid of ["../start", "x".repeat(36), [input.requestId], null]) expect(inventoryRequestId(invalid)).toBe("");
  });
  it("blocks secret handoff on HTTP before making any request", async () => {
    vi.stubGlobal("location", { protocol: "http:" });
    await expect(startInventoryJob("test-id", 0, "a".repeat(64), "synthetic-token")).rejects.toThrow(/HTTPS/);
    expect(http.post).not.toHaveBeenCalled();
  });
  it("sends a secret once with a bounded timeout and clears the local request object", async () => {
    vi.stubGlobal("location", { protocol: "https:" });
    let sent: Record<string, unknown> = {};
    http.post.mockImplementationOnce(async (_url, body) => { sent = { ...body }; return { data: { id: "test-id" } }; });
    await startInventoryJob("test-id", 3, "a".repeat(64), "synthetic-token");
    expect(sent).toEqual({ expectedRevision: 3, policyHash: "a".repeat(64), confirmRead: true, apiToken: "synthetic-token" });
    expect(http.post).toHaveBeenCalledOnce();
    expect(http.post.mock.calls[0]?.[0]).toBe("/cloud-drive/inventory-jobs/test-id/start");
    expect(http.post.mock.calls[0]?.[1].apiToken).toBe("");
    expect(http.post.mock.calls[0]?.[2]).toEqual({ timeout: 15000 });
  });
  it("never exposes an Axios secret/config or retries an unknown outcome", async () => {
    vi.stubGlobal("location", { protocol: "https:" });
    const raw = Object.assign(new Error("synthetic-token"), { config: { data: "synthetic-token" }, response: { status: 503, data: { detail: "synthetic-token" } } });
    http.post.mockRejectedValueOnce(raw);
    const failure = await startInventoryJob("test-id", 0, "a".repeat(64), "synthetic-token").catch(error => error);
    expect(failure.message).not.toContain("synthetic-token");
    expect(failure.config).toBeUndefined(); expect(failure.cause).toBeUndefined();
    expect(http.post).toHaveBeenCalledOnce();
    expect(http.post.mock.calls[0]?.[1].apiToken).toBe("");
    http.get.mockRejectedValueOnce({ response: { status: 404, data: { detail: "synthetic-token" } } });
    await expect(findInventoryRequest("missing")).rejects.toMatchObject({ status: 404 });
    const changed = new SessionChangedError();
    http.post.mockRejectedValueOnce(changed);
    await expect(startInventoryJob("test-id", 0, "a".repeat(64), "synthetic-token")).rejects.toBe(changed);
  });
  it("uses the shared authenticated client for local query and analysis", async () => {
    await fetchInventoryJobs(2);
    expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs?page=2&pageSize=20");
    await fetchInventorySummary("test-id");
    expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id");
    const body = { afterId: 10, limit: 100, folderEntryIds: [11], folderTerms: ["论坛推广"] };
    await fetchInventoryAdvice("test-id", body);
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id/advice", body);
    await cancelInventoryJob("test-id");
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id/cancel", {});
    await setInventoryKeep("test-id", 42, true, 8);
    expect(http.put).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id/entries/42/keep", { kept: true, expectedRevision: 8 });
    await clearInventoryCache("test-id", 9);
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id/clear-cache", { confirmClear: true, expectedRevision: 9 });
    await fetchInventoryDirectories("test-id", 50);
    expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/inventory-jobs/test-id/directories?afterId=50&limit=50");
  });
  it("exposes cloud audit types in the existing settings filter dictionaries", () => {
    expect(messages.settings.audit.eventTypeLabels["cloud_drive.budget"]).toBe("CD2只读预算");
    expect(messages.settings.audit.eventTypeLabels["cloud_drive.inventory"]).toBe("CD2盘点");
    expect(messages.settings.audit.targetTypeLabels.cloud_inventory_job).toBe("CD2盘点任务");
    expect(messages.settings.audit.targetTypeLabels.cloud_drive_budget).toBe("CD2预算");
  });
});
