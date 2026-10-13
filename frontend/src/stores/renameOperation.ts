import { getSessionGeneration, sessionRequest, SessionChangedError } from "../utils/sessionGeneration";
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
      const sessionGeneration = getSessionGeneration();
      if (this.loading || this.rollbackLoading || this.rollbackUncertain || ["queued", "running"].includes(this.currentRollbackPlan?.status ?? "") || this.executionUncertain || ["queued", "running"].includes(this.currentOperation?.status ?? "")) return;
      this.loading = true;
      this.errorMessage = "";
      const version = ++this.executionVersion;
      try {
        const operation = await sessionRequest(createRenameDryRun(renamePreviewIds), sessionGeneration);
        if (version !== this.executionVersion) return;
        this.currentOperation = operation;
        this.currentRollbackPlan = null;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        if (version === this.executionVersion) this.errorMessage = error instanceof Error ? error.message : messages.errors.renameConflictFailed;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.executionVersion) this.loading = false;
        }
      }
    },

    async executeCurrentOperation(onProgress?: (operation: RenameOperation) => void) {
      const sessionGeneration = getSessionGeneration();
      if (!this.canExecute || !this.currentOperation) {
        return;
      }
      this.loading = true;
      this.errorMessage = "";
      const operationId = this.currentOperation.id;
      const version = ++this.executionVersion;
      this.executionUncertain = true;
      try {
        const accepted = await sessionRequest(acceptRenameOperation(operationId), sessionGeneration);
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        this.currentOperation = accepted;
        this.executionUncertain = false;
        this.currentRollbackPlan = null;
        onProgress?.(accepted);
        await sessionRequest(this.pollCurrentOperation(operationId, version, onProgress), sessionGeneration);
      } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
        if (version === this.executionVersion) this.errorMessage = messages.renamePreviews.operationDialog.queryBeforeRetry;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.executionVersion) this.loading = false;
        }
      }
    },

    async pollCurrentOperation(operationId: number, version: number, onProgress?: (operation: RenameOperation) => void) {
      const sessionGeneration = getSessionGeneration();
      while (version === this.executionVersion && this.currentOperation?.id === operationId
        && ["queued", "running"].includes(this.currentOperation.status)) {
        await sessionRequest(new Promise(resolve => setTimeout(resolve, 1000)), sessionGeneration);
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        const operation = await sessionRequest(fetchRenameOperation(operationId), sessionGeneration);
        if (version !== this.executionVersion || this.currentOperation?.id !== operationId) return;
        this.currentOperation = operation;
        onProgress?.(operation);
      }
    },

    async refreshCurrentOperation(onProgress?: (operation: RenameOperation) => void, operationId?: number) {
      const sessionGeneration = getSessionGeneration();
      const targetId = operationId ?? this.currentOperation?.id;
      if (this.loading || !targetId) return;
      const version = ++this.executionVersion;
      this.loading = true;
      this.errorMessage = "";
      this.executionUncertain = true;
      try {
        const operation = await sessionRequest(fetchRenameOperation(targetId), sessionGeneration);
        if (version !== this.executionVersion) return;
        this.currentOperation = operation;
        if (this.currentRollbackPlan?.operation_id !== operation.id) {
          this.currentRollbackPlan = null;
          this.rollbackVersion += 1;
          this.rollbackUncertain = false;
        }
        this.executionUncertain = false;
        onProgress?.(operation);
        await sessionRequest(this.pollCurrentOperation(targetId, version, onProgress), sessionGeneration);
      } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
        if (version === this.executionVersion) this.errorMessage = messages.renamePreviews.operationDialog.queryBeforeRetry;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.executionVersion) this.loading = false;
        }
      }
    },

    async loadRollbackPlans(operationId: number) {
      const sessionGeneration = getSessionGeneration();
      if (this.rollbackLoading || this.rollbackUncertain) return;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plans = await sessionRequest(fetchRenameRollbackPlans(operationId), sessionGeneration);
        if (version !== this.rollbackVersion || this.currentOperation?.id !== operationId) return;
        this.currentRollbackPlan = plans[0] ?? null;
        if (this.currentRollbackPlan) await sessionRequest(this.pollRollbackPlan(this.currentRollbackPlan.id, version), sessionGeneration);
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.rollbackVersion) this.rollbackLoading = false;
        }
      }
    },

    async createRollbackPlan() {
      const sessionGeneration = getSessionGeneration();
      if (!this.currentOperation || this.loading || this.rollbackLoading || this.rollbackUncertain
        || ["queued", "running"].includes(this.currentOperation.status)
        || ["queued", "running"].includes(this.currentRollbackPlan?.status ?? "")) return;
      const operationId = this.currentOperation.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plan = await sessionRequest(createRenameRollbackPlan(operationId), sessionGeneration);
        if (version === this.rollbackVersion && this.currentOperation?.id === operationId) this.currentRollbackPlan = plan;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.rollbackVersion) this.rollbackLoading = false;
        }
      }
    },

    async dryRunRollbackPlan() {
      const sessionGeneration = getSessionGeneration();
      if (!this.currentRollbackPlan || this.loading || this.rollbackLoading || this.rollbackUncertain
        || !["draft", "checked"].includes(this.currentRollbackPlan.status)) return;
      const planId = this.currentRollbackPlan.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      try {
        const plan = await sessionRequest(dryRunRenameRollbackPlan(planId), sessionGeneration);
        if (version === this.rollbackVersion && this.currentRollbackPlan?.id === planId) this.currentRollbackPlan = plan;
      } catch (error) {
        if (sessionGeneration !== getSessionGeneration()) throw error;
        if (version === this.rollbackVersion) this.rollbackErrorMessage = error instanceof Error ? error.message : messages.errors.unknown;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.rollbackVersion) this.rollbackLoading = false;
        }
      }
    },

    async executeRollbackPlan(onProgress?: (plan: RenameRollbackPlan) => void) {
      const sessionGeneration = getSessionGeneration();
      if (!this.currentRollbackPlan || this.currentRollbackPlan.status !== "checked" || !this.currentRollbackPlan.executable_count
        || this.loading || this.rollbackLoading || this.rollbackUncertain) return;
      const planId = this.currentRollbackPlan.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackUncertain = true;
      this.rollbackErrorMessage = "";
      try {
        const accepted = await sessionRequest(acceptRenameRollbackPlan(planId), sessionGeneration);
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        this.currentRollbackPlan = accepted;
        this.rollbackUncertain = false;
        onProgress?.(accepted);
        await sessionRequest(this.pollRollbackPlan(planId, version, onProgress), sessionGeneration);
      } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
        if (version === this.rollbackVersion) this.rollbackErrorMessage = messages.renamePreviews.operationDialog.rollbackQueryBeforeRetry;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.rollbackVersion) this.rollbackLoading = false;
        }
      }
    },

    async pollRollbackPlan(planId: number, version: number, onProgress?: (plan: RenameRollbackPlan) => void) {
      const sessionGeneration = getSessionGeneration();
      while (version === this.rollbackVersion && this.currentRollbackPlan?.id === planId
        && ["queued", "running"].includes(this.currentRollbackPlan.status)) {
        await sessionRequest(new Promise(resolve => setTimeout(resolve, 1000)), sessionGeneration);
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        const plan = await sessionRequest(fetchRenameRollbackPlan(planId), sessionGeneration);
        if (version !== this.rollbackVersion || this.currentRollbackPlan?.id !== planId) return;
        this.currentRollbackPlan = plan;
        onProgress?.(plan);
      }
    },

    async refreshRollbackPlan(planId?: number, onProgress?: (plan: RenameRollbackPlan) => void) {
      const sessionGeneration = getSessionGeneration();
      const targetId = planId ?? this.currentRollbackPlan?.id;
      if (!targetId || this.rollbackLoading) return;
      const operationId = this.currentOperation?.id;
      const version = ++this.rollbackVersion;
      this.rollbackLoading = true;
      this.rollbackErrorMessage = "";
      this.rollbackUncertain = true;
      try {
        const plan = await sessionRequest(fetchRenameRollbackPlan(targetId), sessionGeneration);
        if (version !== this.rollbackVersion || this.currentOperation?.id !== operationId) return;
        if (plan.operation_id !== operationId) throw new Error(messages.errors.unknown);
        this.currentRollbackPlan = plan;
        this.rollbackUncertain = false;
        onProgress?.(plan);
        await sessionRequest(this.pollRollbackPlan(targetId, version, onProgress), sessionGeneration);
      } catch {
        if (sessionGeneration !== getSessionGeneration()) throw new SessionChangedError();
        if (version === this.rollbackVersion) this.rollbackErrorMessage = messages.renamePreviews.operationDialog.rollbackQueryBeforeRetry;
      } finally {
if (sessionGeneration === getSessionGeneration()) {
          if (version === this.rollbackVersion) this.rollbackLoading = false;
        }
      }
    },
  },
});
