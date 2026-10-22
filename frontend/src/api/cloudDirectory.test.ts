import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionChangedError } from "../utils/sessionGeneration";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("./client", () => ({ apiClient: http }));
import { prepareDirectoryPlan, findDirectoryPlan, cancelDirectoryPlan, fetchDirectoryPolicy, startDirectoryExecution,
  findDirectoryExecution, cancelDirectoryExecution, commitDirectoryLocally } from "./cloudDirectory";
import { startDirectoryRecovery, findDirectoryRecovery, cancelDirectoryRecovery, commitDirectoryRecovery,
  prepareReverseDirectoryPlan, fetchDirectoryPlanHistory, reviewDirectoryRecoveryWorker } from "./cloudDirectory";

describe("fixed directory plan API", () => {
  beforeEach(() => { vi.clearAllMocks(); http.get.mockResolvedValue({ data: {} }); http.post.mockResolvedValue({ data: {} }); });
  it("only submits a local observation selection and never submits a remote identity or token", async () => {
    const input = { requestId: "test-request", jobId: "test-job", expectedRevision: 2, entryIds: [1], folderTerms: ["推广"] };
    await prepareDirectoryPlan(input);
    expect(http.post).toHaveBeenCalledExactlyOnceWith("/cloud-drive/directory-plans", input);
    await findDirectoryPlan("request/a");
    expect(http.get).toHaveBeenCalledExactlyOnceWith("/cloud-drive/directory-plan-requests/request%2Fa");
    await cancelDirectoryPlan("plan/a", 0);
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/cancel", { expectedRevision: 0 });
  });
  it("redacts raw errors and does not repeat an uncertain request", async () => {
    http.post.mockRejectedValueOnce(Object.assign(new Error("synthetic-private-value"), { config: { data: "private" }, response: { status: 409 } }));
    const failure = await cancelDirectoryPlan("test", 0).catch(error => error);
    expect(failure.message).not.toContain("synthetic-private-value");
    expect(failure.config).toBeUndefined(); expect(failure.status).toBe(409);
    expect(http.post).toHaveBeenCalledTimes(1);
    const changed = new SessionChangedError(); http.get.mockRejectedValueOnce(changed);
    await expect(findDirectoryPlan("request")).rejects.toBe(changed);
  });
  it("keeps execution, local query, cancel and local sync as separate commands", async () => {
    await fetchDirectoryPolicy(); expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/directory-policy");
    const input = { requestId: "request", expectedRevision: 2, policyHash: "hash", confirmMaintenance: true, confirmGlobalFence: true, apiToken: "synthetic" };
    await startDirectoryExecution("plan/a", input); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/execute", input);
    await findDirectoryExecution("request/a"); expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/directory-execution-requests/request%2Fa");
    await cancelDirectoryExecution("plan/a", 3); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/execution/cancel", { expectedRevision: 3 });
    await commitDirectoryLocally("plan/a", 4); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/commit-local", { expectedRevision: 4, confirmLocalOnly: true });
  });
  it("recovery uses its own request, local commit and strictly separate readonly budget endpoint", async () => {
    const input = { requestId: "new-read-request", expectedRevision: 9, confirmMaintenance: true, confirmRemoteIdle: true, apiToken: "synthetic-read-token" };
    await startDirectoryRecovery("plan/a", input); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/recoveries", input);
    await findDirectoryRecovery("request/a"); expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/directory-recovery-requests/request%2Fa");
    await cancelDirectoryRecovery("recovery/a", 3); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-recoveries/recovery%2Fa/cancel", { expectedRevision: 3 });
    await commitDirectoryRecovery("recovery/a", 4); expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-recoveries/recovery%2Fa/commit-local", { expectedRevision: 4, confirmCurrentPositions: true });
  });
  it("reverse creation and history are local-only commands without names or credentials", async () => {
    await prepareReverseDirectoryPlan("plan/a", { requestId: "reverse-request", expectedRevision: 5 });
    expect(http.post).toHaveBeenLastCalledWith("/cloud-drive/directory-plans/plan%2Fa/reverse", { requestId: "reverse-request", expectedRevision: 5 });
    await fetchDirectoryPlanHistory(2, 20); expect(http.get).toHaveBeenLastCalledWith("/cloud-drive/directory-plans", { params: { page: 2, pageSize: 20 } });
  });
  it("worker review submits only current revision and explicit local confirmations without retry", async () => {
    const input = { expectedRevision: 4, confirmClientsStopped: true, confirmRemoteIdle: true };
    http.post.mockRejectedValueOnce(new Error("synthetic-private"));
    const error = await reviewDirectoryRecoveryWorker("recovery/a", input).catch(value => value);
    expect(http.post).toHaveBeenCalledExactlyOnceWith("/cloud-drive/directory-recoveries/recovery%2Fa/review-worker", input);
    expect(error.message).not.toContain("synthetic-private");
  });
});
