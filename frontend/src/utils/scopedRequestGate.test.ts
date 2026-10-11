import { describe, expect, it } from "vitest";
import { createScopedRequestGate } from "./scopedRequestGate";

describe("scoped request gate", () => {
  it("prevents overlapping silent polls and manual requests", () => {
    const gate = createScopedRequestGate();
    const token = gate.begin()!;
    expect(gate.begin()).toBeNull();
    expect(gate.finish(token)).toBe(true);
    expect(gate.begin()).not.toBeNull();
  });
  it("rejects old responses and old finally after switching tasks", () => {
    const gate = createScopedRequestGate();
    const oldToken = gate.begin()!;
    gate.invalidate();
    const newToken = gate.begin()!;
    expect(gate.isCurrent(oldToken)).toBe(false);
    expect(gate.finish(oldToken)).toBe(false);
    expect(gate.isCurrent(newToken)).toBe(true);
    expect(gate.begin()).toBeNull();
  });
  it("invalidates outstanding responses on close or unmount", () => {
    const gate = createScopedRequestGate();
    const token = gate.begin()!;
    gate.invalidate();
    expect(gate.isCurrent(token)).toBe(false);
    expect(gate.finish(token)).toBe(false);
  });
});
