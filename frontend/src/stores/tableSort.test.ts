import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useTableSortStore } from "./tableSort";

describe("table sort state", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("sorts all items by number before pagination", () => {
    const store = useTableSortStore();
    store.setSort("scan-results", "file_size", "ascending");

    const result = store.applySort("scan-results", [
      { file_size: 20 },
      { file_size: 5 },
      { file_size: 10 },
    ]);

    expect(result.map((item) => item.file_size)).toEqual([5, 10, 20]);
  });

  it("uses default sort for scan jobs", () => {
    const store = useTableSortStore();

    const result = store.applySort("scan-jobs", [{ id: 1 }, { id: 3 }, { id: 2 }]);

    expect(result.map((item) => item.id)).toEqual([3, 2, 1]);
  });

  it("uses default sort for scan results", () => {
    const store = useTableSortStore();

    const result = store.applySort("scan-results", [
      { modified_at: "2026-01-01T00:00:00Z" },
      { modified_at: "2026-03-01T00:00:00Z" },
      { modified_at: "2026-02-01T00:00:00Z" },
    ]);

    expect(result.map((item) => item.modified_at)).toEqual([
      "2026-03-01T00:00:00Z",
      "2026-02-01T00:00:00Z",
      "2026-01-01T00:00:00Z",
    ]);
  });

  it("sorts all items by date descending", () => {
    const store = useTableSortStore();
    store.setSort("scan-results", "modified_at", "descending");

    const result = store.applySort("scan-results", [
      { modified_at: "2026-01-01T00:00:00Z" },
      { modified_at: "2026-02-01T00:00:00Z" },
    ]);

    expect(result.map((item) => item.modified_at)).toEqual([
      "2026-02-01T00:00:00Z",
      "2026-01-01T00:00:00Z",
    ]);
  });

  it("sorts file names in natural order", () => {
    const store = useTableSortStore();
    store.setSort("rename-previews", "file_name", "ascending");

    const result = store.applySort("rename-previews", [
      { file_name: "Movie 10.mkv" },
      { file_name: "Movie 2.mkv" },
      { file_name: "Movie 1.mkv" },
    ]);

    expect(result.map((item) => item.file_name)).toEqual([
      "Movie 1.mkv",
      "Movie 2.mkv",
      "Movie 10.mkv",
    ]);
  });

  it("leaves items unchanged when sorting is cleared", () => {
    const store = useTableSortStore();

    const items = [{ file_size: 2 }, { file_size: 1 }];

    expect(store.applySort("scan-results", items)).toEqual(items);
  });
});
