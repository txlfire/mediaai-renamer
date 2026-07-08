import { describe, expect, it } from "vitest";

import {
  namingTemplateStatusLabel,
  namingTemplateStatusTagType,
  namingTemplateStatusTooltip,
} from "./renamePreviewTemplateStatus";

const text = {
  current: "当前模板",
  outdated: "旧模板生成",
  unknown: "版本未知",
  currentTooltip: "当前预览基于当前模板 v{currentVersion} 生成。",
  outdatedTooltip: "当前预览基于模板 v{snapshotVersion} 生成，当前模板为 v{currentVersion}。",
  unknownTooltip: "当前预览未记录模板版本，当前模板为 v{currentVersion}。",
};

describe("rename preview template status", () => {
  it("labels outdated previews with a warning tone and version details", () => {
    expect(namingTemplateStatusLabel("outdated", text)).toBe("旧模板生成");
    expect(namingTemplateStatusTagType("outdated")).toBe("warning");
    expect(namingTemplateStatusTooltip("outdated", 2, 3, text)).toBe(
      "当前预览基于模板 v2 生成，当前模板为 v3。",
    );
  });

  it("labels unknown previews without marking them as outdated", () => {
    expect(namingTemplateStatusLabel("unknown", text)).toBe("版本未知");
    expect(namingTemplateStatusTagType("unknown")).toBe("info");
    expect(namingTemplateStatusTooltip("unknown", null, 3, text)).toBe(
      "当前预览未记录模板版本，当前模板为 v3。",
    );
  });

  it("keeps current previews quiet by default", () => {
    expect(namingTemplateStatusLabel("current", text)).toBe("当前模板");
    expect(namingTemplateStatusTagType("current")).toBe("success");
    expect(namingTemplateStatusTooltip("current", 3, 3, text)).toBe(
      "当前预览基于当前模板 v3 生成。",
    );
  });
});
