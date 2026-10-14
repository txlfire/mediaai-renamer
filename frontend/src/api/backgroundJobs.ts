import { fetchRenameOperation, fetchRenameRollbackPlan, type BatchAiParseResult, type BatchMultiSourceMatchResult,
  type MetadataMatchSource, type MultiSourceMatchMode } from "./client";
import { useBackgroundJobsStore } from "../stores/backgroundJobs";
import { getSessionGeneration, sessionRequest } from "../utils/sessionGeneration";

export async function createRenameDryRunBackground(ids: number[]) {
  const generation = getSessionGeneration();
  const result = await sessionRequest(useBackgroundJobsStore().run<{ operation_id: number }>("rename_dry_run", { rename_preview_ids: ids }), generation);
  return sessionRequest(fetchRenameOperation(result.operation_id), generation);
}

export async function dryRunRenameRollbackPlanBackground(planId: number) {
  const generation = getSessionGeneration();
  const result = await sessionRequest(useBackgroundJobsStore().run<{ plan_id: number }>("rollback_dry_run", { plan_id: planId }), generation);
  return sessionRequest(fetchRenameRollbackPlan(result.plan_id), generation);
}

export function matchRenamePreviewsMultiSourceBackground(ids: number[], source: MetadataMatchSource, mode: MultiSourceMatchMode) {
  return useBackgroundJobsStore().run<BatchMultiSourceMatchResult>("metadata_batch",
    { rename_preview_ids: ids, metadata_match_source: source, mode });
}

export function parseRenamePreviewsWithAiBackground(ids: number[], source: MetadataMatchSource) {
  return useBackgroundJobsStore().run<BatchAiParseResult>("ai_batch", { rename_preview_ids: ids, metadata_match_source: source });
}
