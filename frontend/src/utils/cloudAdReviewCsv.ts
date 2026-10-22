import type { AdReview } from "../api/cloudAdReview";
import { zhCnMessages as messages } from "../locales/zh-CN";

function cell(value: string | number) {
  const raw = String(value);
  const safe = /^\s*[=+\-@]/u.test(raw) || /^[\t\r\n]/u.test(raw) ? "'" + raw : raw;
  return `"${safe.replace(/"/g, '""')}"`;
}

export function buildAdReviewCsv(review: AdReview): string {
  if (!review.items.length || review.items.length > 100 || review.executionAuthorized !== false || review.writeAvailable !== false) {
    throw new Error("invalid_review_export");
  }
  const text = messages.cloudInventory.adReview;
  const reason = (code: string) => messages.cloudInventory.reasons[code as keyof typeof messages.cloudInventory.reasons] ?? code;
  const rows: (string | number)[][] = [[text.recordId, text.requestId, messages.cloudInventory.entryId, messages.cloudInventory.name,
    messages.cloudInventory.root, messages.cloudInventory.decision, text.evidence, text.reason, text.hash, text.source, text.localOnly]];
  for (const item of review.items) rows.push([review.id, review.requestId, item.entryId, item.name, item.path,
    text.decisions[item.decision], item.protection ? reason(item.protection) : item.evidence.map(reason).join("；"),
    item.reason, review.snapshotHash, review.sourceCurrent ? text.current : text.historical, text.localOnly]);
  return "\uFEFF" + rows.map(row => row.map(cell).join(",")).join("\r\n") + "\r\n";
}
