# 将扫描任务表操作列固定为确定宽度

Written against: `35e3db9` + 未提交改动（工作树脏；`frontend/src/views/ScanJobsView.vue` 未被本次审查改动）

## Evidence chain

- Surface: `frontend/src/views/ScanJobsView.vue` → 路由 `/scan-jobs`。经 `frontend/src/App.vue:113` 的 `app-layout` 分支渲染（非 public），侧栏与内容区同时在场。
- Problem: 该页表格的操作列是 `fixed="right"` 的固定列，却只声明了 `min-width`，未声明确定宽度；应用内其余所有表格操作列（及固定选择列）均声明确定 `width`。
- Design evidence:
  - 契约：`.codex/skills/mediaai-frontend-ui/SKILL.md:12` —— "Prefer existing shared layout components and global table/button styles; add scoped styles only for local exceptions."（优先复用既有共享布局组件与全局样式；仅本地例外才添加 scoped 样式）
  - 契约：同文件 `:14` —— 表格须保持"分页、排序、固定操作列、截断 tooltip、空状态、无意外横向溢出"
  - 既有做法（exemplar）：全项目 14 个操作列/固定列中 13 个使用确定 `width` ——
    `MediaSourcesView.vue:810`（`width="240"`）、`RenamePreviewsView.vue:2095`（190）、`:2250`（144）、`:2444`、`:2553`（148）、`:2602`（132）、`SettingsView.vue:2680`（300）、`:3695`（92）、`TaskGovernanceView.vue:589`（168）、`:696`（112）、`:765`（96）；固定选择列同样为 `width="44"`（`MediaSourcesView.vue:778`、`RenamePreviewsView.vue:1966`、`:2219`）
  - 唯一例外：`ScanJobsView.vue:472` 使用 `min-width="96"`
- Owner: `frontend/src/views/ScanJobsView.vue:472` 的 `el-table-column` 声明（页面本地，非共享 owner）
- Scope and affected surfaces: 仅 `/scan-jobs` 表格的操作列（右侧固定区）。共享样式 `.table-actions`（`styles.css:1874`）与 `.table-action-button`（`styles.css:1844`）**不在改动范围**。
- Uncertainty:
  - 已核验内容不会因该列变窄而溢出：`.table-action-button` 为 `width/min-width: 24px`（`styles.css:1846-1847`），`.table-actions` 为 `gap: 3px`（`:1878`），3 个按钮合计 3×24 + 2×3 = **78px**，小于 96px。
  - 未做真实浏览器横向滚动验证（需渲染环境）。修正为"与既有做法一致"而非"修复某个已观测的错位"，此为 Confidence 未取高的原因。

## Design decision

将 `ScanJobsView.vue:472` 操作列的 `min-width="96"` 改为 `width="96"`。

根因不是"列太窄"，而是**固定列声明了不确定宽度**。`fixed` 列参与横向滚动时，其固定区宽度需由声明值确定；写成 `min-width` 使 Element Plus 改走 auto 计算，固定区宽度不再由 `96` 决定。项目中其余 13 个操作列均为确定 `width`，本页是唯一偏离者 —— 修正方向是**回到既有做法**，不是引入新规范。

**不引入新值。** `96` 已是本行当前声明值，仅把约束语义由"下限"改为"确定"。

## Reuse

- 既有做法范例：`TaskGovernanceView.vue:765`（`width="96"`）—— 与本次目标值相同的操作列，可直接对照
- 共享样式无需改动：`.table-actions`（`styles.css:1874`）、`.table-action-button`（`styles.css:1844`）、`.data-table`（`styles.css:1738+`）

无需新 primitive 或新 token。

## Changes

1. `frontend/src/views/ScanJobsView.vue`（操作列声明，当前位于 472 行）
   - Change: 将 `min-width="96"` 改为 `width="96"`。该行其余属性（`align="center"`、`header-align="center"`、`fixed="right"`）保持不变。
   - Preserve: 三个操作按钮的图标、顺序、`el-tooltip` 文案、`action-view` / `action-magic` 类名、`canReadLogs` 的禁用逻辑与 `@click` 处理函数全部不动。`.table-actions` 的居中与 `gap` 行为不动。
   - Verify: 操作列在横向滚动时保持固定于右侧；列宽为确定 96px（不再随内容/布局变化）；三个图标按钮单行居中且不换行、不溢出。

## Scope

- Inherit: 无。该声明为 `ScanJobsView` 本地所有，不被其他 surface 消费。
- Verify: `/scan-jobs` 在以下状态下的表格呈现 —— 有数据（含多页）、空状态、加载中（`v-loading`）、窄视口（触发横向滚动）、亮色与暗色主题。
- Exclude: `el-dialog width="640px"`（`:514`）**不在范围** —— 实测 26 个弹窗中 14 个用裸 px、12 个用 `min()`，既有做法本身不一致，无契约可依，不应顺手改。9 个数字列的 `tabular-nums` **不在范围** —— 项目无该契约（见下方 Stop conditions）。表格其他列的宽度、对齐、排序一律不动。

## Validation

- Product: 打开 `/scan-jobs`，选择媒体源后查询，表格正常渲染扫描任务历史；启动扫描后任务状态与进度正常更新。
- Interface:
  - 路由/状态：`/scan-jobs`；带 `?media_source_id=<id>` 的深链；空状态；加载态
  - 内容极端：长 `error_message`（经 `TextCell` 截断）、`batch_interval_seconds` 的小数（依赖 `.scan-jobs-table .interval-column` 的 `nowrap`，`styles.css:1886`）、0 值与三位数计数
  - 交互：三个操作按钮的 tooltip 与点击、`canReadLogs` 为 false 时第三个按钮禁用
  - 视口：宽视口（无横向滚动）+ 窄视口（触发横向滚动，确认右侧固定列不抖动）+ 亮/暗主题
- System: 确认未新增 token / primitive / 依赖；确认未改动任何共享样式（`styles.css` 应无 diff）；确认操作列仍是全项目唯一使用 `min-width` 的例外被消除。
- Repository: `npm.cmd run frontend:test` → 全部通过；`npm.cmd run frontend:build` → 构建成功。（Node 需用 **24.15.0**：本机 `PATH` 默认 v16.20.2 会使 Vite 8 / Vitest 4 失败，与本次改动无关。）

## Stop conditions

- 若改为 `width="96"` 后在真实浏览器中出现表头与单元格错位、或固定列与滚动区重叠，停止并回报 —— 说明该版本 Element Plus 对固定列的宽度处理与预期不同，需另行判定。
- 若执行者认为应同时统一其他表格操作列的宽度值，停止 —— 各列按自身按钮数量取 92–300px，属按需取值，不在本计划授权范围。
- **不要**顺手给 9 个数字列加 `font-variant-numeric: tabular-nums`。该项目无此契约（全项目仅 `styles.css:777`、`:1496` 使用，各服务 `.list-stat-item` 与 `.cleanup-summary`），属新增规范，需另行决策。
- **不要**改 `:514` 的 `el-dialog width="640px"`（理由见 Scope/Exclude）。

## Design documentation

- After acceptance and validation: 无需修改 `design.md`。本次未新增 token、未改变设计系统，仅为页面回到既有做法。若项目后续要为"表格操作列统一宽度档位"或"数字列等宽数字"立规，应作为独立决策记入 `design.md`，不在本计划内。
