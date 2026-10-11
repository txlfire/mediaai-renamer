# 阶段 1：主题语义 token 化（两轴架构的前置重构）

Written against: `35e3db9` + 未提交改动（工作树脏；本次不修改任何产品源码，仅产出本计划）

> **执行状态（2026-10-11）**：阶段 **1a / 1b / 1c / 1d 已实施并验证**，详见工作日志
> [progress-2026-10-11-theme-two-axis.md](../../work-logs/progress-2026-10-11-theme-two-axis.md)。
> 已验证：前端 193 项测试、构建、编码与 diff 检查均通过；实机逐项核对了亮暗两态的全部中性色与
> 激活导航色，**与改造前一致（零观感变化）**。
> 实施中的两处偏离本计划，均已修正并记录：
> 1. **hover 色不能用 `color-mix` 派生** —— 实测 `#2563eb` 按 8% 黑混得 `#225bd8`，而现有 hover 是
>    `#1d4ed8`，无法精确复现。故 hover 保留为显式 token（`--action-*-hover`）；只有背景/描边的
>    `rgba()` 可精确等价，已改为 `color-mix`。
> 2. **品牌强调需方案别名** —— 本计划把 `--brand-accent` 写成 palette 单值，实测导致暗色强调色
>    退化为亮色值（`#2563eb` 而非 `#3b82f6`）。已改为 `--brand-accent-{light,dark}{,-ink}` 四个真值
>    + `--brand-accent` / `--brand-accent-ink` 两个方案别名。
>
> **阶段 2（candy 配色）与阶段 3（UI/文案）尚未实施**；`data-palette` 暂未写入。
> Harness 复核已完成：操作色 token 映射与 `data-scheme` 运行时断言已补齐，本阶段没有未解决的阻断项。

> **锚点复核记录（2026-10-11，改造前）**：本计划的全部行号已针对当时的 `frontend/src/styles.css` 逐一复核通过。
> 依据：该文件 **2026-07-08 后未被改动**，仍为 3634 行（注意：`Measure-Object -Line` 会因空行漏计而报 3108，必须用 `Get-Content` 计数或 Python `splitlines()`），
> `data-theme` 选择器 44 处、6 位 hex 138 个，均与本计划成文时一致。
> 已抽验锚点：`L7`（`:root`）、`L19`/`L48`（`--sidebar-active` 两处）、`L40`（暗色块起始）、`L82`（暗色块结束）、
> `L176`/`L3228`（`outline: none`）、`L935`（`.el-table` 覆盖）、`L1004`（占位符）、`L1013`（`.el-alert`）、
> `L2182`（`.action-edit` 亮色基础）、`L2286`（`.action-sync|run|toggle` 暗色覆盖）、`L2994`（`.directory-item .el-icon`）、`L3502`（`.naming-builder-warnings`）。
> 唯一修正：`.el-drawer__header` 实际在 **L1025**（原写 1024）。
> **注意**：上述行号对应**改造前**的文件；阶段 1 已实施，行号已整体下移（文件 3634 → 3704 行），
> 后续如需按行号定位请重新核对，或直接以选择器文本搜索。

## 目标与前提

**目标**：把"亮/暗方案（scheme）"与"配色（palette）"两个轴解耦，使**新增一套配色只需一个约 20 行的变量块**，无需重复任何组件级 CSS。

**前提决策（已确认）**：新主题**叠加**为配色轴，与亮/暗方案正交；后续会添加更多配色。

**本计划不新增任何功能，也必须零观感变化。** 它的唯一产物是 token 层与替换映射。

## 现状实测

| 事实 | 数据 |
| --- | --- |
| `:root`（亮色） | 约 29 个变量 |
| `:root[data-theme="dark"]` | 40 个变量，**全部为中性色**（`#0d1117`…`#e5edf5`），无点缀色 |
| `styles.css` 中 `data-theme` 选择器 | **44 处** |
| 含硬编码颜色的暗色块 | **20 个**（1 个变量块 + 19 个组件覆盖块） |
| 暗色块内不同 hex | **23 个**，其中 **0 个**已作为 token 存在于 `:root` |
| 语义色现状 | 组件引用 `--el-color-success/warning/danger` 等（`styles.css:782/786/798/1907/3286/3291/3442/3475/3543` 等处），但本项目 `:root` **从未定义**这些 `--el-color-*`，故实际取 Element Plus 默认值。暗色块中也未覆盖，暗色语义色同样来自 EP 默认。**这是 token 化的关键收益点**：改为 palette 的 `--semantic-*` 后，语义色才真正可控 |
| 样式加载顺序 | `main.ts:1` Element Plus → `main.ts:10` `styles.css`，故本项目可覆盖 EP 变量 |
| Element Plus 暗色包 | **未引入**，暗色完全由 `styles.css` 自管 |
| 激活导航 | `.side-menu a.active`（`styles.css:264-268`）= `background: var(--sidebar-active)` + `color: #ffffff` |

## 1. Token 所有权分层

判据：**该值是否应随配色变化？** 会 → palette；不会 → scheme。

### 1.1 scheme 所有（中性色，每方案一套）

`:root`（亮）与 `:root[data-scheme="dark"]`（暗）各定义一套：

```
--app-bg  --panel-bg  --panel-soft-bg  --sidebar-bg
--sidebar-text  --text-main  --text-muted  --border-color
--table-header-bg  --table-row-bg  --input-bg
--tooltip-bg  --tooltip-text  --tooltip-border
--button-default-bg  --button-default-border  --button-default-text
--button-default-hover-bg  --button-default-disabled-bg
--button-default-disabled-border  --button-default-disabled-text
--button-quiet-bg  --button-quiet-hover-bg
--el-bg-color-overlay  --el-fill-color  --el-fill-color-light  --el-fill-color-lighter
--el-border-color-light  --el-border-color-lighter  --el-disabled-bg-color  --el-disabled-text-color
color-scheme: light | dark
```

> **现状可直接复用**：`:root[data-theme="dark"]` 的 40 个变量已是纯中性色，**内容无需重写**，仅需把选择器由 `[data-theme="dark"]` 改为 `[data-scheme="dark"]`。

### 1.2 新增 scheme 所有（本次 token 化产出）

这些是当前写在暗色组件块里的中性色，需提升为 scheme token：

| 新 token | 亮色值 | 暗色值 | 取代 |
| --- | --- | --- | --- |
| `--text-strong` | `#1f2937` | `#e5edf5` | 暗色块中 6 处 `#e5edf5`（表头文本、`th`、drawer 标题） |
| `--table-text` | `#1f2937` | `#d8e3ed` | 暗色块中 14 处 `#d8e3ed`（表格正文、表单标签、alert） |
| `--text-weak` | `#64748b` | `#8b9aaa` | 空状态文本 |
| `--text-subtle` | `#94a3b8` | `#b0b8c4` | 空描述文本 |
| `--input-placeholder` | `#94a3b8` | `#7f8da0` | 输入占位符 |
| `--table-row-hover-bg` | `#f1f5f9` | `#243140` | `.el-table` 行悬停 |

> `--text-strong` 与 `--text-main` 在亮色下取值相同；保留两者是因为暗色将它们指向不同值（`#e5edf5` vs `#e5edf5`，当前亦相同）。**若复核发现二者始终同值，可合并为一个**，执行时以实际暗色块取值为准。

### 1.3 palette 所有

**取值模式分三种，不要混用**（详见 `design.md`）：

| 模式 | token | 说明 |
| --- | --- | --- |
| **双值** | `--brand-accent` / `-ink`（亮）、`--brand-accent-dark` / `-ink-dark`（暗） | 强调色在亮暗下取值不同：亮色需 ink 达标，暗色需自身压暗面板达标 |
| **双值** | `--action-*` 与 `--action-*-dark`（共 16 个） | `.action-*` 现有**亮色基础色**（`styles.css:2182/2206/2218/2244/2253/2272`）+ **暗色覆盖块**（`:2194/2230/2237/2286/2293/2300`），故必须两套 |
| **单值** | `--semantic-success / -warning / -danger / -info` | 亮暗共用同值；`default` 的亮色需注意低于 4.5 的既有值（见 design.md 门禁注） |

```css
/* 品牌强调 —— 亮暗各一值 */
--brand-accent            /* 激活导航底色（原 --sidebar-active 的角色） */
--brand-accent-ink        /* 压在亮色强调色上的文本色 */
--brand-accent-dark
--brand-accent-ink-dark

/* 语义 —— 单值 */
--semantic-success  --semantic-warning  --semantic-danger  --semantic-info

/* 表格操作图标 —— 按语义命名，亮暗各一值 */
--action-edit   --action-edit-dark     --action-view   --action-view-dark
--action-magic  --action-magic-dark    --action-run    --action-run-dark
--action-toggle --action-toggle-dark   --action-sync   --action-sync-dark
--action-ai     --action-ai-dark       --action-delete --action-delete-dark
```

> **命名理由**：用 `--action-edit` 而非 `--action-blue`。同一语义在不同配色下颜色不同（例：default 的 `#2563eb` 蓝 → candy 下 `#be185d` 粉），按语义命名才能在配色间替换。

> **派生值不要做成 token**：`.action-*` 的 `--el-button-bg-color` / `-border-color` / `-hover-bg-color` 当前是硬编码 `rgba(…,.12/.28/.18)`（`styles.css:2183-2185` 等）。改为 `color-mix(in srgb, var(--action-X) 12%, transparent)` 派生 —— **一个色值代替 4 个手写值**。核对：default 取 `#2563eb` 时与现状 `rgba(37,99,235,.12/.28/.18)` 等价，故零观感变化。同理 `--semantic-warning-bg` / `-border` 由 `--semantic-warning` 派生，不入 palette。

> **`--brand-accent-soft` 已从契约移除**：原计划设想的"次按钮 tint"实际由 scheme 层的 `--button-quiet-*` 承担，全项目有 20+ 处消费点（`styles.css:177/426/429/…`）。把中性按钮染色属独立决策，本次不做，因此不需要该 token。

## 2. 替换映射（20 个块的逐条归属）

### 2.1 变量块 `:root[data-theme="dark"]`（L40）

该块 40 个变量**全部属 scheme**。除选择器改名外，仅两处需调整：

| 行 | 现状 | 改为 | 原因 |
| --- | --- | --- | --- |
| L48 | `--sidebar-active: #3b82f6` | **移出**该块 | 强调色属 palette；palette 层统一以 `--brand-accent` 提供 |
| L939-943（在 L935 块内） | 见下 | 见下 | 重复定义，应收敛 |

### 2.2 组件覆盖块

| 现状行 | 选择器 | 现状值 | 改为 | 归属 |
| --- | --- | --- | --- | --- |
| 939 | `.el-table` | `--el-table-header-text-color: #d8e3ed` | `var(--table-text)` | scheme |
| 940 | `.el-table` | `--el-table-text-color: #d8e3ed` | `var(--table-text)` | scheme |
| 942 | `.el-table` | `--el-table-row-hover-bg-color: #243140` | `var(--table-row-hover-bg)` | scheme |
| 955 | `.el-table__header tr` | `color: #e5edf5` | `var(--text-strong)` | scheme |
| 962 | `.el-table td` | `color: #d8e3ed` | `var(--table-text)` | scheme |
| 972 | `.el-table__empty-text` | `color: #8b9aaa` | `var(--text-weak)` | scheme |
| 982 | `.el-empty__description p` | `color: #b0b8c4` | `var(--text-subtle)` | scheme |
| 987 | `.el-table th` | `color: #e5edf5` | `var(--text-strong)` | scheme |
| 1001 | `.el-form-item__label` | `color: #d8e3ed` | `var(--table-text)` | scheme |
| 1005 | `.el-input__inner::placeholder` | `color: #7f8da0` | `var(--input-placeholder)` | scheme |
| 1014 | `.el-alert` | `color: #d8e3ed` | `var(--table-text)` | scheme |
| 1025 | `.el-drawer__header` | `color: #e5edf5` | `var(--text-strong)` | scheme |
| 2195 | `.action-edit`（暗） | `color: #60a5fa` | `var(--action-edit-dark)` | **palette** |
| 2202 | `.action-edit:hover`（暗） | `color: #93c5fd` | `var(--action-edit-dark-hover)` 或 `color-mix` 派生 | **palette** |
| 2231 | `.action-view`（暗） | `color: #22d3ee` | `var(--action-view-dark)` | **palette** |
| 2238 | `.action-magic`（暗） | `color: #c4b5fd` | `var(--action-magic-dark)` | **palette** |
| 2287 | `.action-toggle`（暗） | `color: #4ade80` | `var(--action-toggle-dark)` | **palette** |
| 2294 | `.action-ai`（暗） | `color: #60a5fa` | `var(--action-ai-dark)` | **palette** |
| 2301 | `.action-delete`（暗） | `color: #f87171` | `var(--action-delete-dark)` | **palette** |
| 2995 | `.directory-item .el-icon`（暗） | `color: #60a5fa` | `var(--action-view-dark)` | **palette** |
| 3503 | `.naming-builder-warnings`（暗） | `border-color: #8a6a24` | `color-mix(… var(--semantic-warning) 28% …)` | **palette** |
| 3504 | `.naming-builder-warnings`（暗） | `background: #332a14` | `color-mix(… var(--semantic-warning) 12% …)` | **palette** |
| 3505 | `.naming-builder-warnings`（暗） | `color: #f6d98a` | `var(--semantic-warning)` | **palette** |

### 2.2b 亮色操作按钮的硬编码色（**原计划遗漏，必须一并处理**）

`.action-*` 在亮色下**不是**引用 token，而是 6 组硬编码色值，每组含 1 个 `color` + 3 个 `rgba()`：

| 行 | 选择器 | 现状 | 改为 |
| --- | --- | --- | --- |
| 2183-2186 | `.action-edit` | `rgba(37,99,235,.12/.28/.18)` + `color:#2563eb` | 三个 rgba → `color-mix(var(--action-edit) 12%/28%/18%)`；`color` → `var(--action-edit)` |
| 2207-2210 | `.action-view` | `rgba(8,145,178,…)` + `#0891b2` | 同上，基色 `var(--action-view)` |
| 2219-2222 | `.action-magic` | `rgba(124,58,237,…)` + `#7c3aed` | 同上，基色 `var(--action-magic)` |
| 2245-2248 | `.action-sync` / `.action-run` / `.action-toggle` | `rgba(22,163,74,…)` + `#16a34a` | 同上，基色 `var(--action-run)` / `var(--action-toggle)` |
| 2254-2257 | `.action-ai` | `rgba(37,99,235,…)` + `#2563eb` | 同上，基色 `var(--action-ai)` |
| 2273-2276 | `.action-delete` | `rgba(220,38,38,…)` + `#dc2626` | 同上，基色 `var(--action-delete)` |

**等价性核对（零观感变化的依据）**：default 配色取 `--action-edit: #2563eb` 时，
`color-mix(in srgb, #2563eb 12%, transparent)` ≡ `rgba(37,99,235,.12)`；28% 与 18% 同理。

> **已核实：全部 8 个 action 的亮暗覆盖是完整的**，不存在缺失。
> 其中 `.action-sync` / `.action-run` / `.action-toggle` 在暗色下由**同一条规则**覆盖
> （`styles.css:2284-2291`，选择器列表已同时列出三者），值 `#4ade80`。
> 逐项实测对暗面板 `#171e26` 的对比度：`#4ade80` 9.64、`#22d3ee` 9.30、`#c4b5fd` 9.10、
> `#f472b6` 6.34、`#f87171` 6.07 —— **全部达标**。
> （本计划早期版本曾误判 `.action-run` 缺少暗色覆盖、并误报 `#16a34a` 对暗面板为 2.2:1；
> 实测为 **5.10**，该误判已撤销。）

### 2.3 关键产出：palette 块加入后，这 19 个组件块**全部可删除**

除上表已 token 化的色值外，这些块中**没有非颜色的结构差异**（已逐块核对：其余属性为 `background-color` / `color` / `box-shadow: none` 等，且对应色值均已在上表覆盖或本就可省略）。

**执行时的验证判据**：把 `data-scheme="dark"` 与 `data-palette="candy"` 同时置上后，若页面无需任何 `[data-palette] .el-xxx` 组件选择器即正常渲染，则两条轴已真正解耦。**这是阶段 2 的验收核心。**

## 3. 目标 CSS 结构

```css
/* ── 1. 默认配色（palette：default）+ 亮色方案（scheme：light） ── */
:root {
  color-scheme: light;

  /* scheme: light */
  --app-bg: #f4f7fb;
  /* …（原 :root 的中性色） */

  /* palette: default（点缀色，亮暗共用） */
  --brand-accent: #2563eb;
  --brand-accent-ink: #ffffff;
  --semantic-success: #16a34a;
  --action-edit: #2563eb;
  /* … */

  /* scheme 扩展 token（本次新增） */
  --text-strong: #1f2937;
  --table-text: #1f2937;
  --text-weak: #64748b;
  --text-subtle: #94a3b8;
  --input-placeholder: #94a3b8;
  --table-row-hover-bg: #f1f5f9;
}

/* ── 2. 仅覆盖 scheme（中性色），所有配色共用 ── */
:root[data-scheme="dark"] {
  color-scheme: dark;
  --app-bg: #101418;
  /* …（原暗色 40 变量，去掉 --sidebar-active） */

  --text-strong: #e5edf5;
  --table-text: #d8e3ed;
  --text-weak: #8b9aaa;
  --text-subtle: #b0b8c4;
  --input-placeholder: #7f8da0;
  --table-row-hover-bg: #243140;
}

/* ── 3. 仅覆盖 palette（点缀色），亮暗共用 ── */
:root[data-palette="candy"] {
  --brand-accent: #ec4899;
  --brand-accent-ink: #ffffff;
  --semantic-success: …;
  --action-edit: …;
  /* …约 20 个 */
}
```

**新增一套配色的成本 = 第 3 节那一个块。**

## 4. 颜色工具函数块（原 25 个暗色组件块的去向）

原先靠 25 个 `[data-theme="dark"] .el-xxx` 块强改 Element Plus 内部色值。token 化后：

- 色值 → 转为 scheme/palette token（第 2 节映射）；
- 结构差异（如 `.data-table .el-table__empty-text { box-shadow: none }`）→ 保留为**不带方案的通用规则**，因为它与亮暗无关；
- `.el-table` 内的 `--el-table-*` 定义（L935-944）→ 保留一处通用规则，指向新 token。

> 若保留后仍存在"仅暗色需要"的结构性规则，应改为引用 scheme token，而非复制整块。

## 5. 影响面与兼容性

| 资产 | 影响 | 处理 |
| --- | --- | --- |
| `stores/app.ts:7-8` | `ThemeMode`/`ResolvedTheme` 需引入 `scheme` 与 `palette` | 见阶段 3 |
| `stores/app.ts:66` | `dataset.theme` → 拆为 `dataset.scheme` + `dataset.palette` | 见阶段 3 |
| `stores/app.test.ts:50-56` | 用 `"blue"` 验证非法值回退；`blue` 可能成为合法配色名**冲突** | 改用真正非法的值（如 `"__invalid__"`），**保留该用例语义** |
| `stores/app.test.ts:42-47` | `resolveTheme` 三态断言 | 扩展为 scheme × palette |
| `App.vue:41,72` | `isDarkTheme` 与二元切换 | 见阶段 3 |
| `App.vue:98-104` | `matchMedia` 监听 | 仅作用于 scheme，palette 无关 |
| `zh-CN.ts:26-28` | 仅"切换亮色/暗色" | 补配色名文案 |
| `design.md` | 新增配色=新增设计决策 | 每个正式配色必须登记（见 (b) 契约） |
| `docs/design/project-design.md` §4.3 | 组合缩放验收门禁 | 阶段 4 按矩阵验证 |

## 6. 分阶段实施

**阶段 1a — 新增 scheme token 并替换中性色硬编码**（本次计划主体）
产出：scheme 扩展 token + 第 2.2 表中 12 处 scheme 类替换。零观感变化。

**阶段 1b — 抽出 palette 层**
新增 `--brand-accent*`、语义色族、`--action-*`；把第 2.2 表中 10 处 palette 类替换指向新 token；在 `:root` 定义 default 配色值（取当前亮色值）。零观感变化。

**阶段 1c — 选择器改名与块收敛**
`[data-theme="dark"]` → `[data-scheme="dark"]`（44 处）；删除已全部 token 化的 19 个组件块；保留必要结构规则。零观感变化。

**阶段 1d — 运行时代码接线**
`app.ts` 双属性、旧键兼容、`App.vue` 切换入口。此步**不是**零观感变化（属性名变了），需与 1c 同步验证。

**阶段 2 — 样板配色 candy（糖果粉紫）**：仅加一个 palette 块；验收"零组件 CSS"假设。

**阶段 3 — UI 与文案**：方案 + 配色两维选择器、i18n、测试更新。

**阶段 4 — 验收**：组合缩放矩阵 × 亮暗 × 各配色。

## 7. Validation

- **Product**：切换"方案（亮/暗/跟随系统）"与"配色"的任意组合后，页面刷新仍保持所选组合；旧用户（localStorage 仅有 `mediaai-theme-mode`）升级后观感与默认配色一致。
- **Interface**：
  - 路由：全部页面 × 亮/暗 × default/candy（阶段 2 后）
  - 状态：表格（含固定列、空状态、悬停）、表单（含占位符）、弹窗/抽屉/下拉/tooltip、标签、告警、侧栏展开/收缩、命名构建器告警区
  - 视口：4K + Windows 200% + 浏览器 125%（`project-design.md` §4.3）；1920×1080、1366×768 补充
  - 主题：亮、暗、跟随系统；各配色
- **System**：
  - 确认**不存在**任何 `[data-palette="…"] .el-xxx` 形式的组件选择器（解耦判据）
  - 确认新增配色仅需变量块
  - 确认 `--brand-accent-ink` 对 `--brand-accent` 的对比度 ≥ 4.5:1（`.side-menu a.active` 是白字压在强调色上）
  - 确认正文文本对背景 ≥ 4.5:1、焦点环 ≥ 3:1
- **Repository**：
  - `npm.cmd run frontend:test`（Node **24.15.0**）→ 通过
  - `npm.cmd run frontend:build` → 成功
  - `npm.cmd run check:encoding` → 通过
  - **阶段性**：阶段 1a/1b/1c 每步完成后逐页截图比对，确认观感零变化

## 8. Stop conditions

- **若阶段 1c 中出现某个暗色组件块无法用 token 表达**（即有真正的结构差异而非色值差异），停止并回报 —— 说明该处需要"按方案"而非"按配色"的结构规则，不要用复制 palette 块的方式绕过。
- **若 `--text-strong` 与 `--text-main` 在全项目始终同值**，合并为一个 token，不要保留同义 token。
- **若某配色的强调色无法同时满足**"激活导航白字 ≥4.5:1"与"作为按钮底色可用"，停止并回报 —— 需要引入 `--brand-accent-ink` 的深/浅两选（即该配色使用深色文字），不要在未授权情况下放宽对比度门禁。
- **不要**为糖果粉紫修改任何中性色（面板、背景、文本、边框）。活泼须来自强调色；把面板也染色会使亮暗两态都失衡，且后续每套配色都要重新调中性色 —— 这正是本重构要避免的。
- **不要**在阶段 1 顺手修 `styles.css:176`、`:3228` 的 `outline: none` 焦点环缺陷。它属无障碍范畴，需走 `fixing-accessibility`，与本重构混做会让"零观感变化"无法验证。
- **不要**引入 Element Plus 官方暗色包。当前自管方案已可用，引入会引入第二套变量体系。

## 9. Design documentation

- 阶段 1 完成后：在 `design.md` 的 Token 段记录 scheme/palette 两轴分层、命名约定与所有权判据。
- 阶段 2 完成后：在 `design.md` 登记 `candy` 配色的取值与对比度实测值。
- 本计划**不**直接修改 `design.md`；(b) 部分已单独完成。
