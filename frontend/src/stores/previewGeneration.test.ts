import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePreviewStore } from "./preview";

const api = vi.hoisted(() => ({ generateRenamePreviews: vi.fn(), fetchRenamePreviews: vi.fn() }));
vi.mock("../api/client", () => api);
describe("preview generation refresh compatibility", () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.resetAllMocks(); });
  it("keeps the legacy refresh default", async () => {
    const store = usePreviewStore();
    api.generateRenamePreviews.mockResolvedValue({ generated_count: 1 });
    api.fetchRenamePreviews.mockResolvedValue([]);
    await store.generatePreviews({ scan_job_id: 1 });
    expect(api.fetchRenamePreviews).toHaveBeenCalledOnce();
  });
  it("allows paged callers to skip the legacy full-list query", async () => {
    const store = usePreviewStore();
    api.generateRenamePreviews.mockResolvedValue({ generated_count: 1 });
    await store.generatePreviews({ scan_job_id: 1 }, false);
    expect(api.fetchRenamePreviews).not.toHaveBeenCalled();
    expect(store.generationSummary?.generated_count).toBe(1);
  });
  it("clears the previous success before a failed generation", async () => {
    const store = usePreviewStore();
    api.generateRenamePreviews.mockResolvedValueOnce({ generated_count: 9 }).mockRejectedValueOnce(new Error("synthetic failure"));
    await store.generatePreviews({ scan_job_id: 1 }, false);
    await store.generatePreviews({ scan_job_id: 1 }, false);
    expect(store.generationSummary).toBeNull();
    expect(store.errorMessage).toBe("synthetic failure");
    expect(store.loading).toBe(false);
  });
});
