import { beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("./client", () => ({ apiClient: api }));
import { findAdReview, listAdReviews, saveAdReview, type AdReview } from "./cloudAdReview";
const id = "12345678-1234-1234-1234-123456789abc";
const record: AdReview = { id, requestId: id, jobId: id, inventoryRevision: 1, pathEpoch: 0, ruleVersion: "test",
  createdAt: 1792100000, snapshotHash: "a".repeat(64), sourceCurrent: true, staleReason: null,
  networkAccess: false, writeAvailable: false, executionAuthorized: false, contentReadBytes: 0,
  items: [{ entryId: 1, decision: "review", reason: "", name: "更多资源.txt", path: "/test/更多资源.txt",
    size: 10, remoteId: "file-id", generation: 1, protection: null, evidence: ["promotional_phrase"], evidenceLevel: "low" }] };
describe("local ad review client", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  it("saves only the exact explicit IDs and finds the original UUID without a retry", async () => {
    api.post.mockResolvedValue({ data: record }); api.get.mockResolvedValue({ data: record });
    const input = { requestId: id, jobId: id, expectedRevision: 1, items: [{ entryId: 1, decision: "review" as const, reason: "" }], confirmLocalOnly: true as const };
    expect(await saveAdReview(input)).toEqual(record);
    expect(api.post).toHaveBeenCalledExactlyOnceWith("/cloud-drive/ad-reviews", input);
    expect(await findAdReview(id)).toEqual(record);
    expect(api.get).toHaveBeenCalledExactlyOnceWith(`/cloud-drive/ad-review-requests/${id}`);
  });
  it("rejects foreign UUID, duplicate IDs, excessive rows and claimed cloud execution", async () => {
    for (const value of [{ ...record, requestId: "other" }, { ...record, items: [record.items[0], record.items[0]] },
      { ...record, items: new Array(101).fill(record.items[0]) }, { ...record, executionAuthorized: true }, { ...record, contentReadBytes: 1 },
      { ...record, createdAt: 1e20 }, { ...record, sourceCurrent: "true" }]) {
      api.get.mockResolvedValueOnce({ data: value }); await expect(findAdReview(id)).rejects.toThrow();
    }
    expect(api.post).not.toHaveBeenCalled();
  });
  it("sanitizes transport errors and preserves only the HTTP status", async () => {
    api.get.mockRejectedValueOnce({ response: { status: 409 }, config: { secret: "private-test-value" } });
    try { await findAdReview(id); throw new Error("must fail"); } catch (error) {
      expect(error).toHaveProperty("status", 409); expect(JSON.stringify(error)).not.toContain("private-test-value");
    }
  });
  it("bounds history and rejects accidental cloud access or mismatched pages", async () => {
    const value = { items: [{ id, requestId: id, jobId: id, itemCount: 1, createdAt: 1792100000, sourceCurrent: true }], page: 1, pageSize: 20, total: 1, networkAccess: false, writeAvailable: false };
    api.get.mockResolvedValueOnce({ data: value }); expect(await listAdReviews()).toEqual(value);
    for (const data of [{ ...value, page: 2 }, { ...value, networkAccess: true }, { ...value, items: new Array(21).fill(value.items[0]) }]) {
      api.get.mockResolvedValueOnce({ data }); await expect(listAdReviews()).rejects.toThrow();
    }
  });
  it("translates only allowlisted stop codes and does not echo arbitrary server text", async () => {
    api.get.mockRejectedValueOnce({ response: { status: 409, data: { detail: "CD2盘点停止：review_capacity；不会自动重扫或扩大范围" } } });
    await expect(findAdReview(id)).rejects.toThrow("达到数量或逻辑字节上限");
    api.get.mockRejectedValueOnce({ response: { status: 409, data: { detail: "private raw server detail" } } });
    await expect(findAdReview(id)).rejects.not.toThrow("private raw server detail");
  });
});
