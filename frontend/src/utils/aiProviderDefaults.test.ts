import { describe, expect, it } from "vitest";

import { getAiProviderDefaults } from "./aiProviderDefaults";

describe("getAiProviderDefaults", () => {
  it("returns DeepSeek defaults", () => {
    expect(getAiProviderDefaults("deepseek")).toEqual({
      model: "deepseek-chat",
      baseUrl: "https://api.deepseek.com",
    });
  });

  it("returns OpenAI compatible defaults", () => {
    expect(getAiProviderDefaults("openai_compatible")).toEqual({
      model: "gpt-4o-mini",
      baseUrl: "https://api.openai.com/v1",
    });
  });

  it("returns custom defaults", () => {
    expect(getAiProviderDefaults("custom")).toEqual({
      model: "",
      baseUrl: "https://",
    });
  });

  it("falls back to DeepSeek defaults for unknown providers", () => {
    expect(getAiProviderDefaults("unknown")).toEqual({
      model: "deepseek-chat",
      baseUrl: "https://api.deepseek.com",
    });
  });
});
