import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { usePaginationStore } from "./pagination";

describe("pagination state", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("uses 10 items per page by default", () => {
    const store = usePaginationStore();

    expect(store.getState("media-sources").pageSize).toBe(10);
    expect(store.paginate("media-sources", [1, 2, 3])).toEqual([1, 2, 3]);
  });

  it("supports 50 items per page", () => {
    const store = usePaginationStore();
    const items = Array.from({ length: 60 }, (_, index) => index + 1);

    store.setPageSize("scan-jobs", 50);

    expect(store.paginate("scan-jobs", items)).toHaveLength(50);
  });

  it("supports showing all results without pagination", () => {
    const store = usePaginationStore();
    const items = Array.from({ length: 60 }, (_, index) => index + 1);

    store.setPageSize("scan-results", 0);

    expect(store.paginate("scan-results", items)).toHaveLength(60);
  });

  it("resets to first page when page size changes", () => {
    const store = usePaginationStore();

    store.setPage("scan-results", 3);
    store.setPageSize("scan-results", 50);

    expect(store.getState("scan-results").currentPage).toBe(1);
  });
});
