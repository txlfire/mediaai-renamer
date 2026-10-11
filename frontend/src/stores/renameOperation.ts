import { defineStore } from "pinia";

import {
  createRenameDryRun,
  createRenameRollbackPlan,
  dryRunRenameRollbackPlan,
  acceptRenameOperation,
  fetchRenameOperation,
  acceptRenameRollbackPlan,
  fetchRenameRollbackPlan,
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
    executionVersion: 0,
    executionUncertain: false,
    rollbackVersion: 0,
    rollbackUncertain: false,
  }),
  getters: {
    canExecute(state) {
      return Boolean(
        state.currentOperation &&
          state.currentOperation.status === "dry_run" &&
          !state.loading && !state.executionUncertain && !state.rollbackLoading && !state.rollbackUncertain &&
          !["queued", "running"].includes(state.currentRollbackPlan?.status ?? "") &&
          state.currentOperation.ready_count > 0,
      );
    },
  },
  actions: {
    stopExecutionPolling() {
      this.executionVersion += 1;
      this.rollbackVersion += 1;
      this.rollbackLoading = false;
      this.loading = false;
    },
    async runDryRun(renamePreviewIds: number[]) {
      if (this.loading || this.rollbackLoading || this.rollbackUncertain || ["queued", "running"].includes(this.currentRollbackPlan?.status ?? "") || this.executionUncertain || ["queued", "running"].includes(this.currentOperation?.status ?? "")) return;
      this.loading = true;
      this.errorMessage = "";
      const version = ++this.executionVersion;
      try {
        const operation = await createRenameDryRun(renamePreviewIds);
        if (version !== this.executionVersion) return;
        this.currentOperation = operation;
        this.currentRollbackPlan = null;
      } catch (error) {
        if (version === this.executionVersion) this.errorMessage = error instanceof Error ? error.message : messages.errors.renameConflictFailed;
      } finally {
        if (version === this.executionVersion) this.loading = false;
      }
    },

    async executeCurrentOperation(onProgress?: (operation: RenameOperation) => void) {
      if (!this.canExecute || !this.currentOperation) {
        return;
      }
      this.loading = true;
      this.errorMessage = "";
      const operationId = this.currentOperation.id;
      const version = ++this.executionVersion;
      this.executionUncertain = true;
      try {
        const accepted = await acceptRenameOperation(operationId);
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        this.currentOperation = accepted;
        this.executionUncertain = false;
        this.currentRollbackPlan = null;
        onProgress?.(accepted);
        await this.pollCurrentOperation(operationId, version, onProgress);
      } catch {
        if (version === this.executionVersion) this.errorMessage = messages.renamePreviews.operationDialog.queryBeforeRetry;
      } finally {
        if (version === this.executionVersion) this.loading = false;
      }
    },

    async pollCurrentOperation(operationId: number, version: number, onProgress?: (operation: RenameOperation) => void) {
      while (version === this.executionVersion && this.currentOperation?.id === operationId
        && ["queued", "running"].includes(this.currentOperation.status)) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        const operation = await fetchRenameOperation(operationId);
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        this.currentOperation = operation;
        onProgress?.(operation);
      }
    },

    async refreshCurrentOperation(onProgress?: (operation: RenameOperation) => void, operationId?: number) {
      const targetId = operationId ?? this.currentOperation?.id;
      if (this.loading || !targetId) return;
      const version = ++this.executionVersion;
      this.loading = true;
      this.errorMessage = "";
      this.executionUncertain = true;
      try {
        const operation = await fetchRenameOperation(targetId);
        if (version !== this.executionVersion) return;
        this.currentOperation = operation;
        if (this.currentRollbackPlan?.operation_id !== operation.id) {
          this.currentRollbackPlan = null;
          this.rollbackVersion += 1;
          this.rollbackUncertain = false;
        }
        this.executionUncertain = false;
        onProgress?.(operation);
        await this.pollCurrentOperation(targetId, version, onProgress);
      } catch {
        if (version === this.executionVersion) this.errorMessage = messages.renamePreviews.operationDialog.queryBeforeRetry;
      } finally {
        if (version === this.executionVersion) this.loading = false;
      }
    },

    async loadRollbackPlans(operationId: number) {
      if (this.rollbackLoading || this.rollbackUncertain) return;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plans = await fetchRenameRollbackPlans(operationId);
        if (version !== this.rollbackVersion || this.currentOperation?.id !== operationId) return;
        this.currentRollbackPlan = plans[0] ?? null;
        if (this.currentRollbackPlan) await this.pollRollbackPlan(this.currentRollbackPlan.id, version);
      } catch (error) {
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        if (version === this.rollbackVersion) this.rollbackLoading = false;
      }
    },

    async createRollbackPlan() {
      if (!this.currentOperation || this.loading || this.rollbackLoading || this.rollbackUncertain
        || ["queued", "running"].includes(this.currentOperation.status)
        || ["queued", "running"].includes(this.currentRollbackPlan?.status ?? "")) return;
      const operationId = this.currentOperation.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plan = await createRenameRollbackPlan(operationId);
        if (version === this.rollbackVersion && this.currentOperation?.id === operationId) this.currentRollbackPlan = plan;
      } catch (error) {
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        if (version === this.rollbackVersion) this.rollbackLoading = false;
      }
    },

    async dryRunRollbackPlan() {
      if (!this.currentRollbackPlan || this.loading || this.rollbackLoading || this.rollbackUncertain
        || !["draft", "checked"].includes(this.currentRollbackPlan.status)) return;
      const planId = this.currentRollbackPlan.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plan = await dryRunRenameRollbackPlan(planId);
        if (version === this.rollbackVersion && this.currentRollbackPlan?.id === planId) this.currentRollbackPlan = plan;
      } catch (error) {
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
        if (version === this.rollbackVersion) this.rollbackLoading = false;
      }
    },

    async executeRollbackPlan(onProgress?: (plan: RenameRollbackPlan) => void) {
      if (!this.currentRollbackPlan || this.currentRollbackPlan.status !== "checked" || !this.currentRollbackPlan.executable_count
        || this.loading || this.rollbackLoading || this.rollbackUncertain) return;
      const planId = this.currentRollbackPlan.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackUncertain = true;
      this.rollbackErrorMessage = "";
      try {
        const accepted = await acceptRenameRollbackPlan(planId);
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        this.currentRollbackPlan = accepted;
        this.rollbackUncertain = false;
        onProgress?.(accepted);
        await this.pollRollbackPlan(planId, version, onProgress);
      } catch {
        if (version === this.rollbackVersion) this.rollbackErrorMessage = messages.renamePreviews.operationDialog.rollbackQueryBeforeRetry;
      } finally {
        if (version === this.rollbackVersion) this.rollbackLoading = false;
      }
    },

    async pollRollbackPlan(planId: number, version: number, onProgress?: (plan: RenameRollbackPlan) => void) {
      while (version === this.rollbackVersion && this.currentRollbackPlan?.id === planId
        && ["queued", "running"].includes(this.currentRollbackPlan.status)) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        const plan = await fetchRenameRollbackPlan(planId);
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        this.currentRollbackPlan = plan;
        onProgress?.(plan);
      }
    },

    async refreshRollbackPlan(planId?: number, onProgress?: (plan: RenameRollbackPlan) => void) {
      const targetId = planId ?? this.currentRollbackPlan?.id;
      if (!targetId || this.rollbackLoading) return;
      const operationId = this.currentOperation?.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      this.rollbackUncertain = true;
      try {
        const plan = await fetchRenameRollbackPlan(targetId);
        if (version !== this.rollbackVersion || this.currentOperation?.id !== operationId) return;
        if (plan.operation_id !== operationId) throw new Error(messages.errors.unknown);
        this.currentRollbackPlan = plan;
        this.rollbackUncertain = false;
        onProgress?.(plan);
        await this.pollRollbackPlan(targetId, version, onProgress);
      } catch {
        if (version === this.rollbackVersion) this.rollbackErrorMessage = messages.renamePreviews.operationDialog.rollbackQueryBeforeRetry;
      } finally {
        if (version === this.rollbackVersion) this.rollbackLoading = false;
      }
    },
  },
});
