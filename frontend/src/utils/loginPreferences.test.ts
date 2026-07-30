import { describe, expect, it } from "vitest";

import { resolvePasswordAutocomplete } from "./loginPreferences";

describe("login preferences", () => {
  it("enables browser password manager semantics only when requested", () => {
    expect(resolvePasswordAutocomplete(true)).toBe("current-password");
    expect(resolvePasswordAutocomplete(false)).toBe("off");
  });
});
