import { defineStore } from "pinia";

import {
  createRenameDryRun,
  createRenameRollbackPlan,
  dryRunRenameRollbackPlan,
  executeRenameOperation,
  executeRenameRollbackPlan,
  fetchRenameRollbackPlans,
  type RenameOperation,
  type RenameRollbackPlan,
} from "../api/client";
import { zhCnMessages as messages } from "../locales/zh-CN";

export const useRenameOperationStore = defineStore("renameOperation", {
  state: () => ({
    currentOperation: null as RenameOperation | null,
    currentRollbackPlan: null as RenameRollbackPlan | null,
    loading: false,
    rollbackLoading: false,
    errorMessage: "",
    rollbackErrorMessage: "",
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
        this.currentRollbackPlan = null;
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.errors.renameConflictFailed;
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
        this.currentRollbackPlan = null;
      } catch (error) {
        this.errorMessage = error instanceof Error ? error.message : messages.errors.renameExecutionFailed;
      } finally {
        this.loading = false;
      }
    },

    async loadRollbackPlans(operationId: number) {
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plans = await fetchRenameRollbackPlans(operationId);
        this.currentRollbackPlan = plans[0] ?? null;
      } catch (error) {
        this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        this.rollbackLoading = false;
      }
    },

    async createRollbackPlan() {
      if (!this.currentOperation) {
        return;
      }
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        this.currentRollbackPlan = await createRenameRollbackPlan(this.currentOperation.id);
      } catch (error) {
        this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        this.rollbackLoading = false;
      }
    },

    async dryRunRollbackPlan() {
      if (!this.currentRollbackPlan) {
        return;
      }
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        this.currentRollbackPlan = await dryRunRenameRollbackPlan(this.currentRollbackPlan.id);
      } catch (error) {
        this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        this.rollbackLoading = false;
      }
    },

    async executeRollbackPlan() {
      if (!this.currentRollbackPlan) {
        return;
      }
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        this.currentRollbackPlan = await executeRenameRollbackPlan(this.currentRollbackPlan.id);
      } catch (error) {
        this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        this.rollbackLoading = false;
      }
    },
  },
});
