import { describe, expect, it } from "vitest";
import { buildCloudInventoryCsv } from "./cloudInventoryCsv";
import type { InventoryAdviceItem } from "../api/cloudInventory";

const item = (name: string, relativePath = "category/file.txt"): InventoryAdviceItem => ({ id: 1, name, relativePath,
  isDirectory: false, size: 10, directoryComplete: false, decision: "review", protection: null, evidenceLevel: "low",
  reasons: ["promotional_phrase"], partialObservation: true, folderSuggestion: null, selected: false, actionAvailable: false });

describe("bounded relative-path CSV exports", () => {
  it("exports Chinese UTF-8 BOM and quoted CSV without absolute roots or identities", () => {
    const csv = buildCloudInventoryCsv([item('文件,"名称".txt')], "metadata-advice-v1", "synthetic-hash");
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain('"文件,""名称"".txt"');
    expect(csv).toContain('"category/file.txt"');
    expect(csv).toContain('"部分观察"');
    expect(csv).not.toContain("approvedRoot");
    expect(csv).not.toContain("remote_id");
  });
  it.each(["=HYPERLINK(test)", "+cmd", "-cmd", "@cmd", "  =cmd", "\tcmd", "\rcmd", "\ncmd"])("neutralizes spreadsheet formula prefix %j", name => {
    expect(buildCloudInventoryCsv([item(name)], "v1", "hash")).toContain('"\'' + name + '"');
  });
  it("rejects absolute, traversal and oversized exports instead of expanding a page", () => {
    for (const path of ["/absolute/file", "C:/private/file", "\\server\\share", "dir/../file"]) {
      expect(() => buildCloudInventoryCsv([item("file", path)], "v1", "hash")).toThrow();
    }
    expect(() => buildCloudInventoryCsv(Array.from({ length: 201 }, () => item("file")), "v1", "hash")).toThrow();
  });
  it("does not export blocked directory names as actionable suggestions", () => {
    const row = { ...item("目录"), isDirectory: true, folderSuggestion: { status: "blocked" as const,
      proposedName: "不能执行的建议", reason: "observed_target_exists" } };
    expect(buildCloudInventoryCsv([row], "v1", "hash")).not.toContain("不能执行的建议");
  });
});
