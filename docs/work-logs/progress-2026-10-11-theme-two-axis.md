# 2026-10-11 主题两轴重构（阶段 1a–1c + 1d）

类型：实施过程记录。承接 [修复指令](../development/reviews/remediation-plan-2026-10-09.md) 与本会话的主题设计。

## 1. 背景与目标

项目原有主题只有**亮/暗**一个维度，实现为 `:root`（亮，29 变量）+ `:root[data-theme="dark"]`（暗，40 变量）**两层**，其中第二层散落 **19 个组件级覆盖块**、含 **23 个硬编码 hex**。

用户确认：新增主题**叠加为配色轴**（palette），与亮/暗方案（scheme）**正交**，后续会添加更多配色。

**本阶段目标**：把两轴解耦，使"新增一套配色 = 一个变量块"，且**零观感变化**。

依据：[design.md](../../../design.md)「主题架构」与「新增一套配色（palette）的契约」；执行计划 [design-plans/phase1-theme-token-layer.md](../../../design-plans/phase1-theme-token-layer.md)。

## 2. 已实施内容

### 2.1 变量层（`frontend/src/styles.css`）

| 层 | 选择器 | 承担 |
| --- | --- | --- |
| scheme: light | `:root` | 全部中性色 + scheme 扩展 token |
| scheme: dark | `:root[data-scheme="dark"]` | 仅覆盖中性色（44 处选择器已改名） |
| palette: default | `:root`（与亮色同块） | 全部点缀色，亮暗各一值 |

新增 **6 个 scheme 扩展 token**（由暗色组件块中的硬编码色提升）：`--text-strong`、`--table-text`、`--text-weak`、`--text-subtle`、`--input-placeholder`、`--table-row-hover-bg`，亮暗各一套值。

新增 **palette token 共 42 个**：品牌强调、4 个语义色、8 组 `--action-*`（含亮暗两套值与 hover）。

**品牌强调采用方案别名**：`--brand-accent-light/-light-ink/-dark/-dark-ink` 为真值，`--brand-accent`、`--brand-accent-ink` 由亮暗块分别指向。这样调色板块只需覆盖 4 个真值，不必为每个方案重复。

### 2.2 组件块 token 化

- **19 个暗色覆盖块**中的硬编码色全部替换为 token（现仅剩 3 个纯结构性规则，无色值）。
- **亮色操作按钮块**（6 组 × 4 值）的 3 个 `rgba()` 改为 `color-mix()` 从 `--action-*` 派生；`color` 改为 token。
- **暗色操作按钮块**同理，指向 `--action-*-dark`。
- `.side-menu a.active` 由 `var(--sidebar-active)` + `color: #ffffff` 改为 `var(--brand-accent)` + `var(--brand-accent-ink)`。
- `--sidebar-active` 已移除（其角色由 `--brand-accent` 承担）。

### 2.3 运行时代码（`frontend/src/stores/app.ts`）

`applyThemeMode()` 由 `dataset.theme` 改为 `dataset.scheme`，并补注释说明两轴。**类型、存储键、UI 入口均未改动**（属阶段 3）。

### 2.4 关键取舍：hover 色必须显式定义

原计划设想 hover 色也用 `color-mix` 派生。**实测推翻**：`#2563eb` 按 8% 黑混得 `#225bd8`，而现有 hover 是 `#1d4ed8` —— **无法精确复现**。因此 hover 色保留为独立 token（`--action-*-hover`）。背景/描边的 `rgba()` 则可精确等价（分量完全一致），已改为 `color-mix`。

## 3. 验证证据

### 3.1 自动化

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 前端测试 | `npm.cmd run frontend:test`（Node 24.15.0） | **32 文件 / 193 项全部通过** |
| 前端构建 | `npm.cmd run frontend:build` | 成功，1.13s（chunk >500kB 为既有告警） |
| 编码检查 | `npm.cmd run check:encoding` | 通过 |
| 空白检查 | `git diff --check` | 通过 |

### 3.2 实机核对（Tabbit 浏览器，隔离实例）

启动方式：`MEDIAAI_CONFIG_PATH=.codex/test-artifacts/review-fixes/theme-dev/config.toml`，指向**隔离数据库**（`.codex/.../theme-dev/data/`）。**默认开发库未被触碰**（`data/mediaai.sqlite3` 时间戳保持 2026-10-11 13:37）。

以 `getComputedStyle` 读取解析后的值（权威来源），改造前后逐项一致：

| token | 亮色（改造前 → 现） | 暗色（改造前 → 现） |
| --- | --- | --- |
| `--app-bg` | `#f4f7fb` → `#f4f7fb` ✓ | `#101418` → `#101418` ✓ |
| `--panel-bg` | `#ffffff` → `#ffffff` ✓ | `#171e26` → `#171e26` ✓ |
| `--sidebar-bg` | `#ffffff` → `#ffffff` ✓ | `#0d1117` → `#0d1117` ✓ |
| `--text-main` | `#1f2937` → `#1f2937` ✓ | `#e5edf5` → `#e5edf5` ✓ |
| `--text-muted` | `#64748b` → `#64748b` ✓ | `#9aa8b6` → `#9aa8b6` ✓ |
| `--border-color` | `#d8e1ec` → `#d8e1ec` ✓ | `#2a3642` → `#2a3642` ✓ |
| `--table-row-bg` | `#ffffff` → `#ffffff` ✓ | `#171e26` → `#171e26` ✓ |
| `--table-header-bg` | `#f8fafc` → `#f8fafc` ✓ | `#202a35` → `#202a35` ✓ |
| `--input-bg` | `#ffffff` → `#ffffff` ✓ | `#202a35` → `#202a35` ✓ |
| `--brand-accent`（原 `--sidebar-active`） | `#2563eb` → `#2563eb` ✓ | `#3b82f6` → `#3b82f6` ✓ |
| 激活导航背景 | `rgb(37,99,235)` → `rgb(37,99,235)` ✓ | `rgb(59,130,246)` → `rgb(59,130,246)` ✓ |
| 激活导航文字 | `rgb(255,255,255)` ✓ | `rgb(255,255,255)` ✓ |
| `color-scheme` | `light` ✓ | `dark` ✓ |
| 属性名 | `data-theme` → `data-scheme`（`data-palette` 暂未写入） | 同左 |

**度量变化（HEAD → 工作区）**：

| 指标 | 前 | 后 |
| --- | --- | --- |
| 六位 hex | 138 | 150（新增 palette token 定义净增；组件块内硬编码已消除） |
| `rgba()` | 45 | 9 |
| `color-mix()` | 0 | 38 |
| `data-theme` 选择器 | 44 | 0 |
| `data-scheme` 选择器 | 0 | 46 |
| 行数 | 3635 | 3704 |

### 3.3 过程记录（含两次自身错误）

- **首轮替换脚本有 bug**：暗色 `sync/run/toggle`、`ai`、`delete` 三块因 `replace` 次数断言失败而**未被替换**，且 `#60a5fa` 出现在 3 个不同语义处被误统一为 `--action-edit-dark`。已逐处修正为 `--action-ai-dark`、`--action-view-dark`。
- **首轮遗漏品牌强调的方案差异**：`--brand-accent` 在 palette 层写为单值，导致**暗色强调色退化为亮色值**（实机核对发现 `#2563eb` 而非 `#3b82f6`）。已引入方案别名修正。

## 4. 本阶段未做（留给后续）

- **未新增任何配色**：`candy`（糖果粉紫）色值已在 `design.md` 登记并完成对比度实测，但**尚未写入 CSS**。这是阶段 2。
- **未做 UI 与文案**：`ThemeMode` 类型、`localStorage` 键、切换入口仍是单一亮/暗按钮；未写 `data-palette`；未加配色选择器与 i18n。这是阶段 3。
- **未做组合缩放验收**：`project-design.md` §4.3 的 4K / Windows 200% / 浏览器 125% 矩阵未跑。
- **未处理的残留硬编码**（下一步候选，均不改中性色即可）：
  `styles.css:1507/1509/1517`（蓝调软底与描边，宜入 palette）、`:1351`（极淡蓝）、`:2668/2670`（琥珀告警底/边）、`:1903/3675`（卡片投影，属 scheme）、`:130`（遮罩，属 scheme）、`LoginView.vue` 的渐变（另有独立计划）。
- **未处理**：`styles.css:176`、`:3228` 的 `outline: none` 焦点环缺陷（属无障碍，另有工具与流程）。

## 5. 风险与回退

- **回退方式**：本阶段仅改 `frontend/src/styles.css` 与 `frontend/src/stores/app.ts` 两个文件；`git checkout` 该两文件即可完全回退。
- **兼容性**：`localStorage["mediaai-theme-mode"]` 语义未变，旧用户偏好保留；无数据库或接口变更；后端未改。
- **已知边界**：`--text-strong` 与 `--text-main` 在亮暗下取值均相同（亮 `#1f2937`、暗 `#e5edf5`），按计划 Stop conditions 本应合并；暂保留两者以便后续配色可分别调整，**需在复评时确认是否合并**。
- **本机环境**：`npm` 相关命令必须用 Node **24.15.0**；`PATH` 默认的 v16.20.2 会使 Vite 8 / Vitest 4 失败。后端服务用 venv311 的 Python 3.11.15。

## 6. 待评审问题（供代码审查）

1. `--text-strong` 与 `--text-main` 同值，是否应合并为单一 token？
2. 品牌强调采用"4 个真值 + 2 个别名"是否需要？若允许配色块重复定义亮暗两值，可简化为 2 个 token（但每套配色多写 2 行）。
3. `color-mix()` 用于背景/描边派生是否可接受（浏览器支持与构建期处理）？hover 色因无法精确复现而保留显式 token，该取舍是否认可。
4. 19 个暗色组件块**全部保留但已无色值**——是否应进一步删除纯结构性规则？
5. `data-palette` 尚未写入。阶段 3 之前，`:root[data-palette]` 选择器不存在，因此当前新增配色无法生效；这一中间态是否可接受。

## 7. Harness 复核收尾

2026-10-11 对 Harness 产出的源码、设计计划和本记录进行复核，结论如下：

- 两轴拆分方式可继续使用。`--text-strong` 与 `--text-main` 当前虽同值，但语义职责不同，暂不合并。
- 保留品牌强调的四个真值与两个方案别名，避免每套配色重复方案切换逻辑。
- `color-mix()` 仅用于可精确等价的背景和描边；项目支持的现代浏览器与 Vite 8 构建链可接受该用法。
- 暗色结构性选择器暂保留，后续在对应组件重构时再删除，不在主题前置重构中扩大范围。
- `data-palette` 属阶段 2、3 的明确后续项，不视为本阶段缺陷。
- 修正 `sync`、`run`、`toggle` 操作色的 token 映射，使三个语义 token 都有独立引用；保持现有视觉色值不变。
- 为运行时主题应用补充测试，确认暗色写入 `data-scheme="dark"`，且不再写入旧的 `data-theme`。

最终门禁：前端 32 个测试文件、193 项全部通过；生产构建、编码检查和 `git diff --check` 通过。构建仅保留既有的主包超过 500 kB 提示。
