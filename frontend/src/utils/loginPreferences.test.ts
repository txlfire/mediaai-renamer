import { describe, expect, it } from "vitest";

import {
  loadLoginPreferences,
  resolvePasswordAutocomplete,
  saveLoginPreferences,
} from "./loginPreferences";

function createStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

describe("login preferences", () => {
  it("enables browser password manager semantics only when requested", () => {
    expect(resolvePasswordAutocomplete(true)).toBe("current-password");
    expect(resolvePasswordAutocomplete(false)).toBe("off");
  });

  it("persists only the username and remember-password preference", () => {
    const storage = createStorage();

    saveLoginPreferences(" admin ", true, storage);

    expect(loadLoginPreferences(storage)).toEqual({ username: "admin", rememberPassword: true });
    expect([...Array(storage.length)].map((_, index) => storage.key(index))).not.toContain("password");
  });

  it("clears preferences when remember-password is disabled or data is invalid", () => {
    const storage = createStorage();
    saveLoginPreferences("admin", true, storage);
    saveLoginPreferences("admin", false, storage);
    expect(loadLoginPreferences(storage)).toEqual({ username: "", rememberPassword: false });

    storage.setItem("mediaai-login-preferences", "invalid-json");
    expect(loadLoginPreferences(storage)).toEqual({ username: "", rememberPassword: false });
    expect(storage.length).toBe(0);
  });
});
