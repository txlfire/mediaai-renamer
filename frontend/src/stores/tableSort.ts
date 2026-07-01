import { defineStore } from "pinia";

import type { PaginationKey } from "./pagination";

export type SortOrder = "ascending" | "descending" | null;

type SortState = {
  prop: string;
  order: SortOrder;
};

const DEFAULT_SORTS: Partial<Record<PaginationKey, SortState>> = {
  "scan-jobs": { prop: "id", order: "descending" },
  "scan-results": { prop: "modified_at", order: "descending" },
  "rename-previews": { prop: "id", order: "ascending" },
};

const naturalCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

function normalizeValue(value: unknown): number | string {
  if (typeof value === "number") {
    return value;
  }

  if (typeof value === "string") {
    const timestamp = Date.parse(value);
    if (!Number.isNaN(timestamp) && /\d{4}-\d{2}-\d{2}/.test(value)) {
      return timestamp;
    }
    return value;
  }

  return "";
}

export const useTableSortStore = defineStore("tableSort", {
  state: () => ({
    sorts: {} as Record<string, SortState>,
  }),
  actions: {
    setSort(key: PaginationKey, prop: string, order: SortOrder) {
      this.sorts[key] = { prop, order };
    },

    applySort<T extends Record<string, unknown>>(key: PaginationKey, items: T[]): T[] {
      const sort = this.sorts[key] ?? DEFAULT_SORTS[key];
      if (!sort?.prop || !sort.order) {
        return items;
      }

      const direction = sort.order === "ascending" ? 1 : -1;
      return [...items].sort((left, right) => {
        if (sort.prop === "__sequence") {
          return direction;
        }

        const leftValue = normalizeValue(left[sort.prop]);
        const rightValue = normalizeValue(right[sort.prop]);

        if (typeof leftValue === "string" && typeof rightValue === "string") {
          return naturalCollator.compare(leftValue, rightValue) * direction;
        }

        if (leftValue < rightValue) {
          return -1 * direction;
        }
        if (leftValue > rightValue) {
          return 1 * direction;
        }
        return 0;
      });
    },
  },
});
