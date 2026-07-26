import { describe, expect, it } from "vitest";

import { resolveStoredCollapsedState } from "./collapsibleSettings";

describe("设置页折叠状态", () => {
  it("未保存状态时使用默认折叠值", () => {
    expect(resolveStoredCollapsedState(null, true)).toBe(true);
    expect(resolveStoredCollapsedState(null, false)).toBe(false);
  });

  it("已保存状态优先于默认值", () => {
    expect(resolveStoredCollapsedState("true", false)).toBe(true);
    expect(resolveStoredCollapsedState("false", true)).toBe(false);
  });
});
