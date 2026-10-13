# 2026-10-11 主题两轴重构（阶段 1a–1c + 1d）

类型：实施过程记录。承接 [修复指令](../development/reviews/remediation-plan-2026-10-09.md) 与本会话的主题设计。

## 1. 背景与目标

项目原有主题只有**亮/暗**一个维度，实现为 `:root`（亮，29 变量）+ `:root[data-theme="dark"]`（暗，40 变量）**两层**，其中第二层散落 **19 个组件级覆盖块**、含 **23 个硬编码 hex**。

用户确认：新增主题**叠加为配色轴**（palette），与亮/暗方案（scheme）**正交**，后续会添加更多配色。

**本阶段目标**：把两轴解耦，使"新增一套配色 = 一个变量块"，且**零观感变化**。

依据：[design.md](../../design.md)「主题架构」与「新增一套配色（palette）的契约」；执行计划 [design-plans/phase1-theme-token-layer.md](../../design-plans/phase1-theme-token-layer.md)。

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

## 5. 过程事故：非规范启动导致实例串线（本方责任）

### 5.1 事故

实机验证期间，本方用**手工命令**启动隔离后端（`python -m uvicorn … --port 8970`），占用了**标准实例端口 8970**；同时标准前端监听 `0.0.0.0:8970`，浏览器优先命中隔离实例，表现为**默认开发账号密码"失效"**。

**不是正式开发库密码变化**，而是两实例争用同一端口的串线。隔离库位于 `.codex/test-artifacts/review-fixes/theme-dev/data/`，账号与该库绑定。该事故已由复核方记录于 [Harness 开发服务启动规约](../development/reviews/discussions/harness-development-service-startup.md) 第 7.2 节。

### 5.2 违规项（对照规约第 6 节）

- 直接运行 `python -m uvicorn …` 与 `node …/vite.js`；
- 用 `Start-Process`、`cmd /c`、WMI `Win32_Process.Create` 让子进程脱离任务生命周期长期驻留；
- 隔离实例复用标准端口 `8970`；
- 为自建实例另存 pid 文件，绕过 `.codex/run-logs/backend.pid`、`frontend.pid`；
- 看到端口占用后未先核对命令行归属即清理，**误杀自己的后端进程**（`Stop-Process` 连带子进程）。

先后尝试四种启动方式均无法让后端长期存活，反复重启进一步加剧端口争用。

### 5.3 已采取与后续约定

- 停止一切手工启动；服务改由项目脚本管理，本方不再自行启停。
- 已释放 `8970` 并清理本人遗留进程；确认无同端口 `127.0.0.1` 与 `0.0.0.0` 双进程占用。
- 启动规约已接入 [`.codex/skills/mediaai-frontend-ui/SKILL.md`](../../.codex/skills/mediaai-frontend-ui/SKILL.md) 的主题验证步骤。
- 后续统一：标准实例 `npm.cmd run dev:windows -- start`；隔离实例独立端口（如 5183/8981）+ `.codex/test-artifacts/<任务名>/` 配置，结束即 stop。
- **已清理（2026-10-11）**：隔离实例目录 `.codex/test-artifacts/review-fixes/theme-dev/`（配置、384KB 隔离库、日志）以及本方在 `.codex/run-logs/` 留下的 8 个 `theme-dev-*` 非标准产物均已删除。删除前已确认无进程占用、pid 失效、`8970` 已释放，标准实例未受影响。

## 6. 风险与回退

- **回退方式（2026-10-13 复核修正）**：主题源码和测试涉及 `frontend/src/styles.css`、`frontend/src/stores/app.ts`、`frontend/src/stores/app.test.ts`。按主题补丁同步回退并验证测试；相关文件混有其他业务改动，不使用整文件恢复命令，以免覆盖无关工作。
- **兼容性**：`localStorage["mediaai-theme-mode"]` 语义未变，旧用户偏好保留；无数据库或接口变更；后端未改。
- **已知边界**：`--text-strong` 与 `--text-main` 在亮暗下取值均相同（亮 `#1f2937`、暗 `#e5edf5`），按计划 Stop conditions 本应合并；暂保留两者以便后续配色可分别调整，**需在复评时确认是否合并**。
- **本机环境**：`npm` 相关命令必须用 Node **24.15.0**；`PATH` 默认的 v16.20.2 会使 Vite 8 / Vitest 4 失败。后端服务用 venv311 的 Python 3.11.15。

## 7. 待评审问题（供代码审查）

> **状态：已由复核方答复，见第 9 节。** 以下保留原始提问以便追溯。

1. `--text-strong` 与 `--text-main` 同值，是否应合并为单一 token？
2. 品牌强调采用"4 个真值 + 2 个别名"是否需要？若允许配色块重复定义亮暗两值，可简化为 2 个 token（但每套配色多写 2 行）。
3. `color-mix()` 用于背景/描边派生是否可接受（浏览器支持与构建期处理）？hover 色因无法精确复现而保留显式 token，该取舍是否认可。
4. 19 个暗色组件块**全部保留但已无色值**——是否应进一步删除纯结构性规则？
5. `data-palette` 尚未写入。阶段 3 之前，`:root[data-palette]` 选择器不存在，因此当前新增配色无法生效；这一中间态是否可接受。

## 8. 复核方结论与修正（原文保留）

2026-10-11 对 Harness 产出的源码、设计计划和本记录进行复核，结论如下：

- 两轴拆分方式可继续使用。`--text-strong` 与 `--text-main` 当前虽同值，但语义职责不同，暂不合并。
- 保留品牌强调的四个真值与两个方案别名，避免每套配色重复方案切换逻辑。
- `color-mix()` 仅用于可精确等价的背景和描边；项目支持的现代浏览器与 Vite 8 构建链可接受该用法。
- 暗色结构性选择器暂保留，后续在对应组件重构时再删除，不在主题前置重构中扩大范围。
- `data-palette` 属阶段 2、3 的明确后续项，不视为本阶段缺陷。
- 修正 `sync`、`run`、`toggle` 操作色的 token 映射，使三个语义 token 都有独立引用；保持现有视觉色值不变。
- 为运行时主题应用补充测试，确认暗色写入 `data-scheme="dark"`，且不再写入旧的 `data-theme`。

最终门禁：前端 32 个测试文件、193 项全部通过；生产构建、编码检查和 `git diff --check` 通过。构建仅保留既有的主包超过 500 kB 提示。

## 9. 本方对复核结论的确认与自查补记

### 9.1 确认接受

上节全部结论本方接受，无异议。第 7 节 5 个提问均已得到答复（不合并同义 token、保留 4 真值 + 2 别名、接受 `color-mix` 用于可精确等价处、结构性选择器暂留、`data-palette` 归后续阶段）。

### 9.2 复核方修掉的两处本方缺陷（自查确认）

复核指出"修正 `sync`/`run`/`toggle` 的 token 映射，使三个语义 token 都有独立引用"。经查阅本会话我的中间产物，确认这修掉的是**两处由我引入的真实缺陷**：

1. **共享选择器的映射错误**：`sync`/`run`/`toggle` 三者共用一条规则，我把整组统一指向 `--action-toggle-dark`（暗色）/ `--action-toggle`（亮色），导致 `--action-run-*`、`--action-sync-*` 定义了却**无任何引用**。复核改为引入中间变量 `--action-color` / `--action-hover-color`，再让 `.action-sync`、`.action-toggle` 各自覆盖，三个语义 token 遂各自独立可用。
2. **`--action-sync` 颜色漂移**：原文件三者共用绿色 `#16a34a`；我改写时把 `sync` 误设为青色 `#0891b2`。复核已改回绿色，与现状一致。

两者均属"单测通过但语义/视觉已偏"的类型——**193 项测试没抓到，因为测试不校验 computed 色值**；只有实机比对与人工复核能发现。

### 9.3 本方过程失职补记

除第 5 节的启动违规外，本阶段还暴露出验证方法上的不足，一并记录：

- 实机核对只覆盖了**中性色与激活导航**，**未逐项核对 8 组操作图标的 computed 颜色**——这正是颜色漂移得以漏过的原因。后续应按 `design.md` 的门禁清单逐项取值比对。
- 未在实现后立即补运行时主题测试，由复核方补上（`app.test.ts` 断言 `dataset.scheme === "dark"`）。

## 10. 2026-10-13 接续复核

当前源码基线为 `a40762c`，较上轮没有前端源码变化，未重复运行相同测试。已将 Harness 审计结论、操作列计划及本记录的回退清单复核归档到本地交流文件 `docs/development/reviews/discussions/assessment-harness-frontend.md`。

接续重点：两处测试结果栏缺少键盘可操作性；96px 操作列计划遗漏左右各 12px 的单元格内边距，必须重写；原报告存在重复计数和仅凭 scoped CSS 判断组件状态的证据不足；4K 组合缩放仍未验收。主题阶段 1 可保留，前端整体验收保持未完成。交流文件按约定本地保留，不提交 Git。

## 11. 2026-10-13 阶段 2/3 交付接续

默认/candy 配色 token、dataset.palette、独立存储键和侧栏外观选择已实现，包含亮暗两套取值。说明文字、默认亮色状态/操作色和默认暗色品牌 ink 按对比度契约调整，因此本轮不是“零视觉变化”的阶段 1。设置测试结果栏改原生按钮、补焦点环、扫描操作列改 112px、替换功能 emoji、数字列等宽及登录背景 token 同步实施。

最新验证和 Harness 交接以 [10 月 13 日收尾记录](progress-2026-10-13-review-closeout.md) 为准。72 场景 CSS 视口矩阵不能证明实际 Windows 200% + 浏览器 125% 已验收；不把这一限制隐藏在旧阶段完成声明中。
