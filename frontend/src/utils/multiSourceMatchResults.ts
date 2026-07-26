import type {
  BatchMultiSourceMatchResult,
  ProviderSearchResult,
  RenamePreview,
} from "../api/client";

export type MultiSourceMatchRow = {
  preview: RenamePreview;
  previewId: number;
  fileName: string;
  candidateCount: number;
  matchStatus: string | null;
  providerResults: ProviderSearchResult[];
  canViewCandidates: boolean;
};

export function buildMultiSourceMatchRows(
  result: BatchMultiSourceMatchResult,
): MultiSourceMatchRow[] {
  return result.items.map((item) => ({
    preview: item.preview,
    previewId: item.preview.id,
    fileName: item.preview.file_name,
    candidateCount: item.preview.metadata_candidate_count ?? 0,
    matchStatus: item.preview.metadata_match_status,
    providerResults: item.provider_results,
    canViewCandidates: (item.preview.metadata_candidate_count ?? 0) > 0,
  }));
}
