import { defineStore } from "pinia";

export const PAGE_SIZE_ALL = 0;
export const PAGE_SIZE_OPTIONS = [10, 50, PAGE_SIZE_ALL] as const;

export type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
export type PaginationKey =
  | "media-sources"
  | "scan-jobs"
  | "scan-results"
  | "rename-previews"
  | "metadata-candidates"
  | "task-governance";

type PaginationState = {
  currentPage: number;
  pageSize: PageSize;
};

function createDefaultState(): PaginationState {
  return {
    currentPage: 1,
    pageSize: 10,
  };
}

export const usePaginationStore = defineStore("pagination", {
  state: () => ({
    pages: {} as Record<string, PaginationState>,
  }),
  actions: {
    getState(key: PaginationKey): PaginationState {
      if (!this.pages[key]) {
        this.pages[key] = createDefaultState();
      }

      return this.pages[key];
    },

    setPage(key: PaginationKey, page: number) {
      const state = this.getState(key);
      state.currentPage = Math.max(1, page);
    },

    setPageSize(key: PaginationKey, pageSize: number) {
      const state = this.getState(key);
      state.pageSize = PAGE_SIZE_OPTIONS.includes(pageSize as PageSize)
        ? (pageSize as PageSize)
        : 10;
      state.currentPage = 1;
    },

    paginate<T>(key: PaginationKey, items: T[]): T[] {
      const state = this.getState(key);
      if (state.pageSize === PAGE_SIZE_ALL) {
        return items;
      }

      const start = (state.currentPage - 1) * state.pageSize;
      return items.slice(start, start + state.pageSize);
    },
  },
});
