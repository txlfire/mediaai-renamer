import { describe, expect, it } from "vitest";

import { formatSensitiveWordsInput, parseSensitiveWordsInput } from "./sensitiveWords";

describe("sensitive word input helpers", () => {
  it("parses double-pipe separated words with deduplication", () => {
    expect(parseSensitiveWordsInput("情色|| 暴力 ||AV||FBI WARNING||暴力")).toEqual([
      "情色",
      "暴力",
      "AV",
      "FBI WARNING",
    ]);
  });

  it("normalizes legacy newline text to double-pipe display", () => {
    expect(formatSensitiveWordsInput("情色\n暴力\r\nAV")).toBe("情色||暴力||AV");
  });

  it("formats arrays as double-pipe separated text", () => {
    expect(formatSensitiveWordsInput(["情色", "暴力", "血腥"])).toBe("情色||暴力||血腥");
  });
});
