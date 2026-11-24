import { shallowRef } from "vue";

type SelectionTable<Row> = {
  clearSelection: () => void;
  toggleRowSelection: (row: Row, selected?: boolean, ignoreSelectable?: boolean) => void;
};

const interactiveCellContent = [
  "button", "a[href]", "input", "select", "textarea", "label", "summary",
  '[contenteditable]:not([contenteditable="false"])',
  '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="switch"]', '[role="combobox"]',
  ".el-checkbox", ".el-select", ".el-input", ".el-switch", ".el-table__expand-icon", ".table-actions",
].join(",");

export function useTableRowSelection<Row>(enabled: () => boolean = () => true) {
  const table = shallowRef<SelectionTable<Row> | null>(null);

  function toggleByRowClick(row: Row, column: { type?: string } | null, event: MouseEvent) {
    const target = event.target;
    if (!table.value || !enabled() || !column || column.type === "selection" || column.type === "expand"
      || event.defaultPrevented || event.button !== 0 || !(target instanceof Element)
      || target.closest(interactiveCellContent)) return;
    const selection = target.ownerDocument.getSelection();
    if (selection && !selection.isCollapsed && selection.containsNode(target, true)) return;
    // 复用表格的选中状态和事件，且不绕过列上的禁选条件。
    table.value.toggleRowSelection(row, undefined, false);
  }

  return { table, toggleByRowClick };
}
