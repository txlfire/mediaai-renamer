import { defineStore } from "pinia";

import {
  createRenameDryRun,
  executeRenameOperation,
  type RenameOperation,
} from "../api/client";

export const useRenameOperationStore = defineStore("renameOperation", {
  state: () => ({
    currentOperation: null as RenameOperation | null,
    loading: false,
    errorMessage: "",
  }),
  getters: {
    canExecute(state) {
      return Boolean(
        state.currentOperation &&
          state.currentOperation.status === "dry_run" &&
          state.currentOperation.ready_count > 0,
      );
    },
  },
  actions: {
    async runDryRun(renamePreviewIds: number[]) {
      this.loading = true;
      this.errorMessage = "";
      try {
        this.currentOperation = await createRenameDryRun(renamePreviewIds);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : "冲突检测失败";
      } finally {
        this.loading = false;
      }
    },

    async executeCurrentOperation() {
      if (!this.currentOperation) {
        return;
      }
      this.loading = true;
      this.errorMessage = "";
      try {
        this.currentOperation = await executeRenameOperation(this.currentOperation.id);
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : "执行重命名失败";
      } finally {
        this.loading = false;
      }
    },
  },
});
