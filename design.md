# Design — MediaAI Renamer

锁定的设计系统。后续 Hallmark 运行会先读本文件，页面必须**遵从**它而不是各自另选主题。
本文件是规则：要改动就明确修改本文件，不要在单个页面里做局部覆盖。

> 数据来源：`frontend/src/styles.css`（约 3100 行，**真正的 token 源头**）。
> 本文件只做登记与约束说明，不复制全部 token；数值以 `styles.css` 为准。
> 品类定位：**数据密集型后台工作台**，不是营销页。落地页专用的 macrostructure / hero / footer 原型**不适用**。

## System

- Genre · modern-minimal（克制的工具型界面；非 editorial / atmospheric）
- Macrostructure · Workbench —— 仅指"工具型工作台"，**不采用** Hallmark 的营销页 Workbench（截图导览）含义
- Theme · custom（vibe: "克制的数据工作台，亮暗双主题"）
- Axes · paper-band: 冷调浅灰(#f4f7fb)/深墨(#101418) / display-style: 单一无衬线，层级靠字重与字号 / accent-hue: 蓝 ~262°

## Tokens（canonical · `frontend/src/styles.css` 为源头）

```css
/* 双主题：:root 为亮色，暗色覆盖同一批变量名。保持"同名变量、两套取值"的结构。 */
:root {
  /* 面 / 纸 */
  --app-bg:          #f4f7fb;   /* 暗: #101418 */
  --panel-bg:        #ffffff;   /* 暗: #171e26 */
  --panel-soft-bg:   #f8fafc;   /* 暗: #1f2933 */
  --sidebar-bg:      #ffffff;   /* 暗: #0d1117 */

  /* 墨 / 文本 */
  --text-main:       #1f2937;   /* 暗: #e5edf5 */
  --text-muted:      #64748b;   /* 暗: #9aa8b6 */

  /* 线 */
  --border-color:    #d8e1ec;   /* 暗: #2a3642 */

  /* 强调色（唯一强调色，不要再引入第二个） */
  --sidebar-active:  #2563eb;   /* 暗: #3b82f6 */

  /* 表 */
  --table-header-bg: #f8fafc;   /* 暗: #202a35 */
  --table-row-bg:    #ffffff;   /* 暗: #171e26 */

  /* 按钮：default(次) / quiet / primary(主，见 el 覆盖) */
  --button-default-bg:      #e8f1ff;
  --button-default-border:  #9fc5ff;
  --button-default-text:    #1e4f99;
  --button-quiet-bg:        #eef4fb;

  /* 字体 */
  --font-display: Inter, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  --font-body:    Inter, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  --font-mono:    ui-monospace, SFMono-Regular, Consolas, monospace;

  /* 间距：项目使用 4pt 基准，但当前以字面值书写（见"待补齐"第 2 条） */
  /* 圆角：--el-border-radius-base 6px 为主，999px 用于胶囊，50% 用于圆形 */

  /* 动效 */
  --sidebar-transition: 300ms ease-in-out;   /* 侧栏专用 */
  /* 其余过渡多为 150–300ms；当前未定义统一缓动变量 */
}
```

Element Plus 覆盖变量集中在 `styles.css`（`--el-*`），是**本项目主题的组成部分**，
不是第三方遗留物：改主题要连同 `--el-*` 一起改，否则组件与自有样式会脱节。

## CTA voice

- Primary · 实心蓝 `#2766e8`（hover `#1f58d2` / active `#1748b8`）· 圆角 6px · 常规按钮内边距
- Secondary · `--button-default-*` 描边风格 · 同一 6px 圆角
- Quiet · `--button-quiet-*` 无边框底色
- 纯图标表格操作按钮：无边框、无背景、有语义颜色、必须有可访问名称（aria-label / tooltip）
- 同一操作不要在字段错误、就地提示、toast、确认弹窗之间重复提示（见 `project-design.md` 实施约定）

## Motion stance

- 克制：仅侧栏展开/收缩(300ms)与状态过渡；不做入场编排、不做逐项 stagger
- 只动 `transform` 与 `opacity`；不动布局属性
- 支持 `prefers-reduced-motion: reduce` → 空间位移降级为 ≤150ms 透明度过渡
- 不使用弹跳/回弹缓动；焦点环**不得**加动画，必须立即出现

## Exports

`frontend/src/styles.css` 是本项目的源头，不额外生成 `tokens.css`（避免两处真值）。
如需 Tailwind v4 `@theme` / DTCG `tokens.json` / shadcn 变量，另说一声再追加。

## 主题架构：两轴正交（scheme × palette）

主题由**两个独立的轴**组成，互不耦合。后续新增主题一律走 palette 轴。

| 轴 | 取值 | 承担 | CSS 选择器 |
| --- | --- | --- | --- |
| **scheme（方案）** | `light` / `dark`（可跟随系统） | 全部**中性色**：背景、面板、文本、边框、表格、输入、提示气泡、按钮中性底色 | `:root`（亮）与 `:root[data-scheme="dark"]` |
| **palette（配色）** | `default` / `candy` / … | 全部**点缀色**：品牌强调、语义色、表格操作图标色 | `:root[data-palette="<name>"]` |

**所有权判据**：该值是否应随配色变化？**会 → palette；不会 → scheme。**

后果：**每套配色与亮暗两态自动组合**（candy×亮、candy×暗 都成立），新增一套配色只需一个变量块，**不得**新增任何组件级 CSS。

> **硬约束**：禁止出现 `[data-palette="…"] .el-xxx` 形式的组件选择器。若某处必须按配色改组件结构，说明它本该是 scheme 轴的事。

### 活泼来自强调色，不来自中性色

糖果粉紫这类配色**只改点缀色**，面板/背景/文本/边框保持 scheme 层的值。把面板也染色会让亮暗两态同时失衡，且此后每套配色都要重新调中性色 —— 这正是两轴分层要避免的。

## 新增一套配色（palette）的契约

在 `:root[data-palette="<name>"]` 一个块内提供下列 token。**缺项即回退 default 配色的值**，因此必须全部显式覆盖。

**注意：三种取值模式，不要混用。**

| 模式 | token | 说明 |
| --- | --- | --- |
| 单值 | `--semantic-*` | 亮暗共用同一值（语义色在两个方案下都达标即可） |
| 双值 | `--brand-accent`、`--brand-accent-ink`、`--action-*` | **必须按方案各给一套** |

```css
:root[data-palette="<name>"] {
  /* 品牌强调 —— 亮暗各一值 */
  --brand-accent:            ;   /* 亮色方案用；激活导航底色 */
  --brand-accent-ink:        ;   /* 压在亮色强调色上的文本色 */
  --brand-accent-dark:       ;   /* 暗色方案用（压暗面板作文本/图标） */
  --brand-accent-ink-dark:   ;   /* 压在暗色强调色上的文本色 */

  /* 语义 —— 单值，亮暗共用 */
  --semantic-success:  ;  --semantic-warning:  ;
  --semantic-danger:   ;  --semantic-info:     ;

  /* 表格操作图标 —— 亮暗各一值（按语义命名，不按颜色命名） */
  --action-edit:  ;  --action-edit-dark:  ;
  --action-view:  ;  --action-view-dark:  ;
  --action-magic: ;  --action-magic-dark: ;
  --action-run:   ;  --action-run-dark:   ;
  --action-toggle:;  --action-toggle-dark:;
  --action-sync:  ;  --action-sync-dark:  ;
  --action-ai:    ;  --action-ai-dark:    ;
  --action-delete:;  --action-delete-dark:;
}
```

`--semantic-warning-bg` / `--semantic-warning-border` **不是 palette token** —— 由 `color-mix(in srgb, var(--semantic-warning) 12%, transparent)` 类派生，避免每套配色重复维护。

同理，`.action-*` 的 `--el-button-bg-color` / `-border-color` / `-hover-bg-color`（当前是硬编码 `rgba(…, .12/.28/.18)`）**改为从 `--action-*` 用 `color-mix` 派生**：一个色值代替 4 个手写值。核对：default 配色取 `--action-edit: #2563eb` 时，`12% / 28% / 18%` 与现状 `rgba(37,99,235,.12/.28/.18)` 等价。

### 命名约定

- 用**语义名**（`--action-edit`），不用**颜色名**（`--action-blue`）。同一语义在不同配色下取值不同。
- 新增 token 前先确认 scheme 层是否已有同义项；不要造同义 token。

### 对比度门禁（每套配色必须实测并登记）

| 场景 | 要求 | 原因 |
| --- | --- | --- |
| 正文文本 / 背景 | ≥ 4.5:1 | 可读性 |
| `--brand-accent-ink` / `--brand-accent`（**亮暗两套分别测**） | ≥ 4.5:1 | `.side-menu a.active`（`styles.css:264-268`）是文本压在强调色上 |
| `--action-*` / 其承载背景（亮暗分别测） | ≥ 4.5:1 | 表格操作图标 |
| `--semantic-*` / 其承载背景 | ≥ 4.5:1 | 状态文字 |
| 焦点环 / 相邻背景 | ≥ 3:1 | WCAG 2.4.11 |

**若某强调色无法同时满足"ink 达标"与"作为强调底可用"**：允许该配色改用深色 ink（如暗色方案用深紫而非白字），**不得**放宽门禁。

> **门禁与既有 default 的真实差距（实测，已登记为待处理）**：`--text-muted: #64748b` 对 `--app-bg: #f4f7fb` 仅 **4.43:1**，略低于 4.5；对 `--panel-bg` 为 4.76 达标。这是**既有 default 配色的问题，不是本门禁过严**。若改为 `#5b6b80` 可达 5.06。属独立决策，不在糖果粉紫范围内。

### 登记要求

每套正式启用的配色必须在本文件登记：名称、各 token 取值、对比度实测值、验证日期。

| 配色 | 状态 | `--brand-accent`（亮/暗） | 备注 |
| --- | --- | --- | --- |
| `default` | 已启用 | `#2563eb` / `#3b82f6` | 现网值，即原 `--sidebar-active`。**已实机核对**：亮色 `getComputedStyle` 读到 `--app-bg: #f4f7fb`、`--panel-bg: #ffffff`、`--sidebar-active: #2563eb`、激活导航 `rgb(37, 99, 235)`；暗色读到 `--app-bg: #101418`、`--panel-bg: #171e26`、`--sidebar-bg: #0d1117`、`--text-main: #e5edf5`、`--text-muted: #9aa8b6`、`--border-color: #2a3642`、`--table-row-bg: #171e26`、`--table-header-bg: #202a35`、`--input-bg: #202a35`、`--sidebar-active: #3b82f6` |
| `candy`（糖果粉紫） | **已定值，待实施** | `#db2777` / `#f472b6` | 见下表；依赖阶段 1 的 palette 层 |
| `candy` 各 token 实测 | | | |
| token | 亮色值 | 对白底 | 暗色值 | 对暗面板 |
| --- | --- | --- | --- | --- |
| `--brand-accent` | `#db2777` | 白字 **4.60** ✓ | `#f472b6` | **6.34** ✓ |
| `--brand-accent-ink` | `#ffffff` | — | `#500724` | **5.68** ✓（白字仅 2.65 ✗，故用深紫 ink） |
| `--action-edit` | `#be185d` | **6.04** ✓ | `#f472b6` | **6.34** ✓ |
| `--action-view` | `#0e7490` | **5.36** ✓ | `#22d3ee` | **9.30** ✓ |
| `--action-magic` | `#7e22ce` | **6.98** ✓ | `#c4b5fd` | **9.10** ✓ |
| `--action-run` / `--action-toggle` | `#047857` | **5.48** ✓ | `#4ade80` | **9.64** ✓ |
| `--action-sync` | `#0e7490` | **5.36** ✓ | `#22d3ee` | **9.30** ✓ |
| `--action-ai` | `#6d28d9` | **7.10** ✓ | `#c4b5fd` | **9.10** ✓ |
| `--action-delete` | `#dc2626` | **4.83** ✓ | `#f87171` | **6.07** ✓ |
| `--semantic-success` | `#15803d` | **5.02** ✓ | `#4ade80` | **9.64** ✓ |
| `--semantic-warning` | `#b45309` | **5.02** ✓ | `#f6d98a` | **12.17** ✓ |
| `--semantic-danger` | `#dc2626` | **4.83** ✓ | `#f87171` | **6.07** ✓ |
| `--semantic-info` | `#1d4ed8` | **6.70** ✓ | `#60a5fa` | **6.61** ✓ |

**被否决的候选（供追溯，勿再采用）**：`#ec4899`（白字 3.53）、`#8b5cf6`（白字 4.23）—— 均达不到 `.side-menu a.active` 门禁；`#16a34a`（3.30）、`#d97706`（3.19）、`#0891b2`（3.68）、`#059669`（3.77）—— 作亮色文本/图标均低于 4.5。

**糖果粉紫只改点缀色，不动任何中性色。** 面板、背景、文本、边框保持 scheme 层的值。

## 工具引用与改判规则

本文件是**被审对象**，不是工具的执行指令。三个已安装的只读审查工具会读它或据它判定：

| 工具 | 读取方式 | 备注 |
| --- | --- | --- |
| **Hallmark** | **读本文件**（项目根目录），并将其作为锁定系统；其判据服从本文件 | 本文件缺失或过期时，它会拿营销页标准套本项目 —— 末尾"不适用判据"清单就是为此存在 |
| **improve-ui** | 把本文件视为**设计文档**（非契约），仅在其"现行且管辖该 surface"时引用 | 产出 `design-plans/` 计划；**默认丢弃无障碍类发现** |
| **fixing-accessibility** | 不读本文件 | 无障碍问题归此工具，不归 improve-ui |

**如何让一条规则变成可被工具引用的契约**：把它写进本文件。工具只承认"项目已采纳"的规则 —— 实测中 `tabular-nums` 与固定弹窗宽度都因**未写入本文件**而被 `improve-ui` 正确判为"无契约、不予报告"。这是本文件的主要作用：**不写进来，就等于没有规则。**

工具选择与计划执行流程见 [`.codex/skills/mediaai-frontend-ui/SKILL.md`](.codex/skills/mediaai-frontend-ui/SKILL.md) 的"Review tools"一节，此处不重复。

## 待补齐（本项目当前的真实缺口）

1. **焦点可见性**：全站仅 3 处 `:focus-visible`，其中 2 处是 `outline: none`（`styles.css:176`、`:3228`），
   实际只有 1 处（`:1206`）给出可见焦点环。**违反本文件的"焦点环必须可见"与 WCAG 2.4.7。**
   （更正：本条原写"违反 Hallmark 硬要求"不准确 —— Hallmark 的 `audit` 并不以焦点环为判据，且 `improve-ui` 明确排除无障碍类发现。**此类问题须交给 `fixing-accessibility`**，否则两个审查工具都不会报告它。）
2. **间距/字号未 token 化**：间距为字面值，未建立 `--space-*`；无字号阶梯变量。
   圆角基本统一在 6px，但有 `7px`×2、`8px`×3 的漂移值。
3. **数字对齐**（**未采纳为契约**）：仅 `styles.css:777`、`:1496` 使用 `tabular-nums`；而本应用大量展示文件大小、计数、进度、日期、ID（如 `ScanJobsView` 连续 9 个数字列）。**注意**：`improve-ui` 已实测判定其"无契约、不予报告"—— 要让它可被强制，须先把规则正式写入本文件。
4. **图标一致性**：项目只装 `@element-plus/icons-vue`（正确），但有 3 处用 emoji 充当功能图标
   （`RenamePreviewsView.vue:2172` 的 📊、`SettingsView.vue:1583`/`:1618` 的 🟢🔴），
   违反"全项目单一图标库"，且 emoji 跨平台渲染不一致。**依据为 Hallmark 判据**（非本文件既有规则）。
5. **Token 漂移**：`LoginView.vue:221,222,232` 有 3 处硬编码颜色（双斜向渐变 + 大投影），未走 token。
   **同时**：`LoginView.vue:221-222` 是全项目**唯一**使用装饰性渐变的表面（其余全屏/面板底色均为 token 纯色），此点已由 `improve-ui` 依本文件"优先复用既有共享样式"判为成立 finding（见 `design-plans/remove-login-page-gradient.md`）。

## 明确不适用的 Hallmark 判据（对本项目是误报，不要照做）

- **"Pure black, pure white"**：`#ffffff`/`#000000` 面板在工作台是合理取值，不视为 critical。
- **"Inter-everywhere"**：中文后台用系统无衬线 + Inter 是**刻意**选择，不是模板特征。
  （真实可改进点是中英混排协调性，属独立的字体排印议题，不要按"必须双字体配对"处理。）
- **多样性/反模板规则**：本文件生效后该规则**反转** —— 各页面必须**共享同一系统**，而不是彼此不同。
- 所有营销页结构：macrostructure 21 选、hero 原型(H*)、footer 原型(Ft*)、nav 原型(N*) —— 后台不使用。
- `redesign` 动词：不要对本项目执行，会把工作台重排成营销页形态。
