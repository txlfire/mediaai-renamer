import { describe, expect, it } from "vitest";
import { zhCnMessages } from "../locales/zh-CN";
import { isThemePalette, themeDefinitions } from "./themeRegistry";

describe("theme registry", () => {
  it("provides six unique supported themes with localized labels", () => {
    expect(themeDefinitions).toHaveLength(6);
    expect(new Set(themeDefinitions.map(theme => theme.id)).size).toBe(6);
    for (const theme of themeDefinitions) {
      expect(isThemePalette(theme.id)).toBe(true);
      expect(zhCnMessages.app.theme[theme.labelKey]).toBeTruthy();
    }
  });

  it.each([null, undefined, 0, {}, [], "__proto__", "constructor", "unknown", "ocean dark"])("rejects unsupported palette %s", value => {
    expect(isThemePalette(value)).toBe(false);
  });
});
