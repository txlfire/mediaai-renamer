import type { InventoryAdviceItem } from "../api/cloudInventory";
import { zhCnMessages as messages } from "../locales/zh-CN";

function csvCell(value: string | number) {
  const raw = String(value);
  // 始终引用字段；危险起始字符按纯文本导出，避免表格软件执行文件名中的公式。
  const safe = /^\s*[=+\-@]/u.test(raw) || /^[\t\r\n]/u.test(raw) ? "'" + raw : raw;
  return `"${safe.replace(/"/g, '""')}"`;
}

export function buildCloudInventoryCsv(items: InventoryAdviceItem[], ruleVersion: string, rulesHash: string): string {
  if (items.length > 200) throw new Error("inventory_export_page_limit");
  const text = messages.cloudInventory;
  const lines = [[text.entryId, text.name, text.relativePath, text.type, text.completeness, text.decision,
    text.evidence, text.suggestedName, text.ruleVersion, text.rulesHash].map(csvCell).join(",")];
  for (const item of items) {
    if (/^(?:[\/\\]|[a-z]:)/i.test(item.relativePath) || item.relativePath.includes("\\")
        || item.relativePath.split("/").some(part => part === "." || part === "..")) {
      throw new Error("inventory_export_relative_path_required");
    }
    const reason = (code: string) => text.reasons[code as keyof typeof text.reasons] ?? code;
    lines.push([item.id, item.name, item.relativePath, item.isDirectory ? text.folder : text.file,
      item.directoryComplete ? text.complete : text.partial, item.protection ? text.protected : item.decision === "review" ? text.needsReview : text.keep,
      item.protection ? reason(item.protection) : item.reasons.map(reason).join("；"),
      item.folderSuggestion?.status === "suggested" ? item.folderSuggestion.proposedName ?? "" : "",
      ruleVersion, rulesHash].map(csvCell).join(","));
  }
  return "\uFEFF" + lines.join("\r\n") + "\r\n";
}
