import { describe, expect, it } from "vitest";
import type { AdReview } from "../api/cloudAdReview";
import { buildAdReviewCsv } from "./cloudAdReviewCsv";
const review = { id: "test", requestId: "request", snapshotHash: "a".repeat(64), sourceCurrent: false,
  executionAuthorized: false, writeAvailable: false, items: [{ entryId: 1, name: "=HYPERLINK(\"bad\")", path: "/test/a.txt",
    decision: "suggest_delete", reason: "+unsafe", evidence: ["promotional_phrase"], protection: null }] } as AdReview;
describe("ad review CSV", () => {
  it("escapes spreadsheet formulas and labels suggestions as unexecuted historical evidence", () => {
    const csv = buildAdReviewCsv(review);
    expect(csv).toContain("'=HYPERLINK"); expect(csv).toContain("'+unsafe");
    expect(csv).toContain("建议删除（未执行）"); expect(csv).toContain("历史证据"); expect(csv).toContain("不执行隔离或删除");
    expect(csv).toContain('""bad""');
  });
  it("rejects oversized or executable exports", () => {
    expect(() => buildAdReviewCsv({ ...review, items: new Array(101).fill(review.items[0]) })).toThrow();
    expect(() => buildAdReviewCsv({ ...review, writeAvailable: true } as unknown as AdReview)).toThrow();
  });
});
