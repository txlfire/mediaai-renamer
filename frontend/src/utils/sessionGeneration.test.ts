import { describe, expect, it } from "vitest";
import { getSessionGeneration, invalidateSessionRequests, isSessionChangedError, sessionRequest, SessionChangedError } from "./sessionGeneration";

describe("session request cancellation", () => {
  it("keeps current-session successes and original failures", async () => {
    const generation = getSessionGeneration();
    expect(await sessionRequest(Promise.resolve(7), generation)).toBe(7);
    const error = new Error("真实请求失败");
    await expect(sessionRequest(Promise.reject(error), generation)).rejects.toBe(error);
  });

  it("rejects a late success as a control-flow cancellation", async () => {
    let resolve!: (value: number) => void;
    const request = sessionRequest(new Promise<number>(done => { resolve = done; }), getSessionGeneration());
    invalidateSessionRequests();
    resolve(7);
    await expect(request).rejects.toBeInstanceOf(SessionChangedError);
  });

  it("does not expose a late failure from the previous session", async () => {
    let reject!: (error: Error) => void;
    const request = sessionRequest(new Promise<number>((_, fail) => { reject = fail; }), getSessionGeneration());
    invalidateSessionRequests();
    reject(new Error("旧请求连接失败"));
    await expect(request).rejects.toBeInstanceOf(SessionChangedError);
  });

  it("does not identify genuine errors by matching their message", () => {
    expect(isSessionChangedError(new SessionChangedError())).toBe(true);
    expect(isSessionChangedError(new Error("登录状态已失效，请重新登录"))).toBe(false);
    expect(isSessionChangedError(new Error(new SessionChangedError().message))).toBe(false);
    expect(isSessionChangedError(null)).toBe(false);
  });
});
