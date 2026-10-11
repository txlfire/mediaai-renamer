# Hallmark 前端呈现巡检报告（2026-10-09）

文档类型：工具巡检记录。**只读扫描，未修改任何代码。**
工具：[Hallmark](https://github.com/nutlope/hallmark)（安装于 `.agents/skills/hallmark/`，已 gitignore）。
方法：`hallmark audit` 判据（`references/anti-patterns.md`）+ 项目自身的 `design.md`。

## 0. 范围与基线

- 扫描目标：`frontend/src/` 下 20 个页面与组件（8 个 views + 12 个 components），另加 `frontend/src/styles.css`（约 3108 行，token 源头）。
- 系统基线：[design.md](../../../design.md)（本次同时新建，已固化本项目 token 与三条误报豁免）。
- 判定口径：**以 `design.md` 为准**。Hallmark 面向营销页的判据（hero/nav/footer 原型、macrostructure 多样性、pure-white 禁令、Inter-everywhere 禁令）在本项目**不适用**，已在 `design.md` 末尾明列豁免，不计入本报告。

**结论摘要：`6 critical · 5 major · 4 minor`**

## 1. 结论：项目本身没有"AI slop"

先说反向结论，避免误读后续条目。全量扫描后：

| 维度 | 实测 | 评价 |
| --- | --- | --- |
| Token 纪律 | 20 个页面/组件中，**仅 1 个**（`LoginView.vue`）有硬编码颜色，共 3 处 | 优秀 |
| 图标库一致性 | 依赖里只有 `@element-plus/icons-vue` 一个图标库 | 优秀 |
| 组件库漂移 | 无（未混入其他 UI 库） | 优秀 |
| hover-only 关键操作 | **未发现** | 优秀 |
| `tabular-nums` | 已用 2 处，且用法正确 | 有意识，但覆盖不全 |

也就是说：Hallmark 的价值不在"拯救一个 slop 项目"，而在**补几个具体缺口**。

## 2. Critical（6 项）

### C1. 扫描任务表有 6 列连续数字未用等宽数字，当前必然无法对齐

- **Tell**：Tabular data without `tabular-nums`
- **Where**：`frontend/src/views/ScanJobsView.vue:428-434`（`scanned_count` / `video_count` / `new_count` / `changed_count` / `missing_count` / `warning_count`，均 `align="center"`，最小宽度 76-90px）；同类：`frontend/src/views/TaskGovernanceView.vue:579`
- **证据**：`styles.css` 全文只有两处 `font-variant-numeric: tabular-nums`（`:777` 的 `.list-stat-item strong`、`:1496` 的 `.cleanup-summary dd`），**用法正确但未覆盖表格**。`.scan-jobs-table` 的规则（`styles.css:1886`）只处理 `interval-column` 的换行，未涉及数字对齐。
- **为什么是 critical**：6 列数字居中且相邻时，比例字体的字宽不一致会让每列数字左右抖动，用户做纵向比较时要靠眼睛重新找位。这是数据表的可用性缺陷，不只是观感。
- **Fix（一行）**：在 `.scan-jobs-table .cell`（或该表数字列）加 `font-variant-numeric: tabular-nums;`；`TaskGovernanceView` 的数字列同理。

### C2. 三处用 emoji 充当功能图标

- **Tell**：Generic emoji as feature icon
- **Where**：`RenamePreviewsView.vue:2172`（`📊` 作为统计区图标）、`SettingsView.vue:1583` 与 `:1618`（`🟢`/`🔴` 作为连接成功/失败状态图标）
- **为什么是 critical**：项目已选定 `@element-plus/icons-vue`，emoji 是**第二个"字体系统"**——它由操作系统渲染，Windows/macOS/Linux/Android 字形各异；同时破坏单一图标库的描边语汇。Hallmark 对此有明确禁令。
- **Fix（一行）**：`📊` → `TrendCharts`；`🟢`/`🔴` → `SuccessFilled` / `CircleCloseFilled`（均已在该图标库中）。

### C3-C6. 焦点可见性缺失（按判据分列为 4 项）

- **Tell**：Missing visible focus ring / `outline: none`
- **Where**：
  - `styles.css:176` `.brand-block:focus-visible { background: …; outline: none; }`
  - `styles.css:3228` `.settings-test-status-bar:focus-visible, … { outline: none; box-shadow: none; }`
  - 全站 `:focus-visible` 仅 3 处，**其中 2 处恰好是移除焦点环**
- **为什么是 critical**：这是无障碍缺陷（WCAG 2.4.7 Focus Visible），不是审美问题。键盘用户在品牌区与设置测试状态栏上**看不到焦点位置**。Hallmark 的硬要求是"`:focus-visible` 必须有 ≥3:1 对比的可见环，且**不得**给环加动画"。
- **Fix（一行）**：两处 `outline: none` 改为可见环（如 `outline: 2px solid var(--el-color-primary); outline-offset: 2px;`，与已正确的 `styles.css:1206` 一致），并补全其余交互元素的 `:focus-visible`。

## 3. Major（5 项）

### M1. `LoginView.vue` 是唯一有 token 漂移的文件

- **Tell**：Mid-render token improvisation
- **Where**：`LoginView.vue:221`（`linear-gradient(135deg, rgba(66,118,255,0.10), transparent 32%)`）、`:222`（`linear-gradient(315deg, rgba(35,168,133,0.12), transparent 36%)`）、`:232`（`box-shadow: 0 18px 48px rgba(15,23,42,0.12)`）
- **证据**：全站 20 个页面/组件中，只有这一个文件出现硬编码颜色（3 处）。其余全部走 token。
- **为什么是 major**：三处值绕过了 token 体系，其中渐变颜色是**该文件独有**的，无法随主题(token)统一调整；深色主题下这两个叠加渐变是否仍成立未经校验。
- **Fix（一行）**：把三处颜色提升为 `styles.css` 中的命名 token（如 `--login-accent-wash` / `--login-card-shadow`），并同址提供深色值，然后按名引用。

### M2. 双斜向叠加渐变落在 Hallmark 的渐变/aurora 判据上

- **Tell**：Aurora-blob / layered gradient background
- **Where**：`LoginView.vue:221-222`（两处 `linear-gradient`，一 135° 蓝、一 315° 绿，均渐隐到 `transparent`）
- **为什么是 major**：这是登录页的**第一视觉**，而它是全站唯一使用装饰性渐变的位置——与"数据工作台"的克制调性不一致。Hallmark 的修法是"单一锚点色 + 实底"，或极低不透明度的双停梯度 + 颗粒。
- **Fix（一行）**：二选一——改为单色微染的实底；或保留但统一到同一色相并降到 <5% 不透明度。

### M3. `transparent` 作为渐变色标（易产生灰带）

- **Tell**：Gradient to `transparent`
- **Where**：`LoginView.vue:221-222`（两处均 `… , transparent 32%/36%`）
- **为什么是 major**：`transparent` 在多数浏览器等价于 `rgba(0,0,0,0)`，从彩色渐隐到"透明黑"会经过一段**灰带**，而不是干净地淡出到背景色。
- **Fix（一行）**：终点改为同色相的零透明值，例如 `rgba(66,118,255,0)` 而非 `transparent`。

### M4. 261 行的登录页有 0 处交互状态定义

- **Tell**：Missing interaction states（Hallmark component-scope 硬要求：8 状态齐备）
- **Where**：`LoginView.vue` 全文 `focus` 相关匹配 **0 处**；该页含表单 + 提交按钮 + 可能的记住登录开关
- **为什么是 major**：登录页是**未认证用户唯一会看到的界面**，且是键盘输入密集页。缺 `:focus-visible` 在此处影响最大。
- **Fix（一行）**：为输入框、提交按钮、开关补齐 `default / hover / :focus-visible / :active / disabled / loading / error / success` 八态（Element Plus 已提供大部分，需要的是**显式确认并覆盖**项目色）。

### M5. 对话框宽度有 10 种不同取值，缺统一规范

- **Tell**：Design-system drift（同类元素无统一取值）
- **Where**：`el-dialog` 共 53 处使用；`width` 实测出现 **10 种**取值：`420 / 520 / 560 / 640 / 680 / 760 / 800 / 860 / 920 / 1120 px`
- **为什么是 major**：同类容器宽度不统一会让"弹窗大小"失去语义（用户无法从宽度预期内容量），属系统一致性问题。这不是 Hallmark 的具名判据，是本次巡检按 `design.md` 的"遵从同一系统"要求发现的。
- **Fix（一行）**：在 `styles.css` 定义 3–4 档语义宽度（如 `--dialog-sm: 520px` / `--dialog-md: 680px` / `--dialog-lg: 1120px`），逐处归位。

## 4. Minor（4 项）

### m1. 圆角存在 7px / 8px 漂移值

- **Where**：`styles.css` 中 `6px` 出现 21 次（`--el-border-radius-base` 也是 6px），但另有 `7px`×2、`8px`×3。
- **Fix**：把 `7px`/`8px` 归并到 `6px`，或显式提升为 `--radius-lg` 并说明用途。

### m2. 无统一缓动变量，过渡使用浏览器默认 `ease`

- **Where**：仅 `--sidebar-transition: 300ms ease-in-out` 一个具名过渡（`styles.css:12`）；其余 `transition:` 多为字面值，未见 `cubic-bezier` 具名缓动。
- **为什么只判 minor**：Hallmark 要求"用命名缓动、不用浏览器默认 `ease`"，但本项目动效克制、位移量小，实际观感影响有限。
- **Fix**：补 `--ease-out: cubic-bezier(0.16, 1, 0.3, 1)` 等 2–3 个具名缓动，替换字面值。

### m3. 间距未 token 化、无字号阶梯

- **Where**：`styles.css` 未见 `--space-*` 与 `--text-*` 序列；间距与字号为字面值。
- **Fix**：建立 4pt 间距变量与 1.25 比例字号阶梯。属较大重构，建议**只在新代码中引入**，不整体回改。

### m4. `TablePagination.vue:124` 近似误报（说明）

- 该行 `&#39029;`（HTML 实体"页"）会被朴素的十六进制颜色正则命中。**经核对不是硬编码颜色**，`TablePagination.vue` 实际无颜色漂移。记录在此以防后续复扫时重复告警。

## 5. 工具适用性结论（供后续使用参考）

**可以用于优化前端呈现，但必须限定用法。**

- ✅ **适用**：`audit` 动词（只读、不编辑、按 critical/major/minor 输出）；component-scope 流程（单组件、强制沿用既有 token、强制 8 状态）。
- ❌ **不适用**：默认 Design 流程与 `redesign` 动词 —— 它按**营销落地页** macrostructure（21 选）重排，会破坏工作台形态。它自带的 `workbench` macrostructure 也**不是**后台工作台，而是"产品截图导览"宣传页。
- ⚠️ **三条误报已在 `design.md` 豁免**：pure-black/white 禁令、Inter-everywhere 禁令、页面间多样性规则（本项目要求各页**共享**同一系统，规则反转）。
- **前置条件已满足**：`design.md` 现已存在于项目根目录。后续 Hallmark 运行会先读它，并按项目自身系统判据评估，而不是拿营销页标准套用。

## 6. 本次未做

- 未修改任何代码（本报告为只读巡检）。
- 未运行真实浏览器截图或 4K/组合缩放验证（属 `remediation-plan-2026-10-01.md` 第 4.3 节的既有门禁，与本巡检分离）。
- 未评估后端或功能正确性。

## 7. 建议实施顺序

1. **C3-C6 焦点环**（无障碍缺陷，改动最小、收益最直接）。
2. **C1 表格数字等宽**（一行样式，覆盖 6 列）。
3. **C2 emoji → 图标库**（三处替换）。
4. **M1-M3 登录页**（token 化 + 渐变处理，集中在单文件）。
5. **M4/M5** 与 minor 项按后续排期。
