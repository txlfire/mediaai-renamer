# 移除登录页装饰性渐变，改用既有 --app-bg token

Written against: `35e3db9` + 未提交改动（工作树脏；`frontend/src/views/LoginView.vue` 未被本次审查改动）

## Evidence chain

- Surface: `frontend/src/views/LoginView.vue` → 路由 `/login`（`meta.public`）。经 `frontend/src/App.vue:112` 的 `isPublicPage` 分支渲染为裸 `<RouterView>`，**不在**应用外壳（`app-layout` / `app-sidebar`）内。
- Problem: 登录页的页面背景由两条低透明度对角渐变叠加而成，是全项目唯一使用装饰性渐变的表面；应用其余所有全屏与面板底色均为 token 纯色。
- Design evidence:
  - 契约：`.codex/skills/mediaai-frontend-ui/SKILL.md:12` —— "Prefer existing shared layout components and global table/button styles; add scoped styles only for local exceptions."（优先复用既有共享样式；仅本地例外才添加 scoped 样式）
  - 既有可复用 token：`frontend/src/styles.css:13` `--app-bg: #f4f7fb`（亮）/ `:42` `--app-bg: #101418`（暗，位于 `:root[data-theme="dark"]`，见 `styles.css:40`）
  - 既有做法（exemplar）：`styles.css:101`（`html { background: var(--app-bg) }`）、`:2445`、`:3016` 同样以 `var(--app-bg)` 作为全屏底色
  - 唯一性证据：全仓库检索 `linear-gradient|radial-gradient`（`frontend/src/styles.css` + `views/*.vue` + `components/*.vue`）**仅命中本文件 221、222 两行**
- Owner: `frontend/src/views/LoginView.vue` 的 `<style scoped>` 内的 `.login-page` 规则（本地所有，非共享 owner）
- Scope and affected surfaces: 仅登录页 `/login` 的背景呈现。`.login-panel`（卡片）、`.brand-logo`、表单与弹窗均不受影响。
- Uncertainty: 无。渐变的两条色值（`rgba(66,118,255,0.10)`、`rgba(35,168,133,0.12)`）未在项目任何 token 中出现，也无法映射到既有 token；移除是唯一由证据确定的修正，不需要新增 token。

## Design decision

将 `.login-page` 的背景由"两条装饰性对角渐变叠加 `var(--app-bg)`"改为单一 `var(--app-bg)`。

根因不是"颜色不好看"，而是：**登录页的底色未使用项目既有的共享 token，而是本地发明了一套渐变**，这与项目明确的"优先复用既有共享样式"规则冲突，也使它成为全项目唯一不遵循该视觉语言的表面。改用 `--app-bg` 后，登录页与 `html`、与其余全屏 surface 共享同一底色，并自动继承亮/暗两套取值。

**不引入新 token。** 既有 `--app-bg` 已同时覆盖亮暗主题，无需扩展系统。

## Reuse

- `var(--app-bg)` —— `frontend/src/styles.css:13`（亮）/ `:42`（暗）
- Exemplar: `frontend/src/styles.css:101`（`html` 的全屏底色即 `var(--app-bg)`）

无需新 primitive：既有系统已能表达该决策。

## Changes

1. `frontend/src/views/LoginView.vue`（`<style scoped>` 内 `.login-page` 规则，当前位于 220–223 行）
   - Change: 将 `background` 属性的值由
     ```
     background:
       linear-gradient(135deg, rgba(66, 118, 255, 0.10), transparent 32%),
       linear-gradient(315deg, rgba(35, 168, 133, 0.12), transparent 36%),
       var(--app-bg);
     ```
     改为
     ```
     background: var(--app-bg);
     ```
   - Preserve: 同一条规则内的 `min-height: 100vh`、`display: grid`、`place-items: center`、`padding: 32px` 全部保持不变（布局与居中行为不变）。`.login-panel` 的卡片样式、品牌区、表单、密码弹窗一律不动。
   - Verify: 亮色主题下登录页背景为 `#f4f7fb` 纯色；暗色主题下为 `#101418` 纯色；页面无任何渐变；登录卡片仍居中且与视口自适应。

## Scope

- Inherit: 无其他消费方。`.login-page` 仅被 `LoginView.vue` 使用（类名全仓库唯一）。
- Verify: `/login` 在亮/暗两种主题下的呈现；浏览器缩放下的居中与不溢出。
- Exclude: `.brand-logo` 的硬编码色（属另一项独立 finding，本计划不处理）；圆角值；`el-dialog` 宽度；应用外壳内任何页面；无障碍与焦点可见性（本次范围外）。

## Validation

- Product: 以未登录状态访问 `/login`，能正常渲染登录表单与（在有权限时）初始化标签页，登录流程不受影响。
- Interface:
  - 路由/状态：`/login`（默认）；`?redirect=/scan-jobs`（应仍保留重定向参数处理）；`bootstrapAvailable` 为真时的"初始化管理员"标签页
  - 内容极端：超长用户名/占位文案不撑破卡片；窄视口（≤ 420px）
  - 交互：标签页切换、密码显隐、记住密码/记住登录勾选、提交按钮 loading 态
  - 视口：桌面常规宽度 + 窄窗；亮色与暗色主题各一次
- System: 确认未新增 token、未新增 primitive、未引入任何依赖；确认 `.login-page` 不出现与 `html` 底色不一致的第三种底值。
- Repository: `npm.cmd run frontend:test` → 全部通过；`npm.cmd run frontend:build` → 构建成功。（Node 需用 **24.15.0**：本机 `PATH` 默认的 v16.20.2 会导致 Vite 8 / Vitest 4 失败，与本次改动无关。）

## Stop conditions

- 若发现 `.login-page` 的背景在某个未追踪的 surface 上也被消费（即类名并非唯一），停止并重新界定范围。
- 若移除渐变后暗色主题下登录卡片与背景对比不足（`--panel-bg #171e26` 对 `--app-bg #101418`），停止并改走"调整卡片描边/层级"的独立决策，**不要**为提升对比而恢复渐变。
- 若执行者认为必须保留某种视觉区分，停止并回报——保留渐变属于产品意图变更，不在本计划授权范围内。

## Design documentation

- After acceptance and validation: 在根目录 `design.md` 的"待补齐"清单第 5 条（Token 漂移）中，将"`LoginView.vue:221,222,232` 有 3 处硬编码颜色"更新为已处理状态，并注明残留项仅剩 `:232` 的 `box-shadow`。不要在本计划中直接改 `design.md`。
