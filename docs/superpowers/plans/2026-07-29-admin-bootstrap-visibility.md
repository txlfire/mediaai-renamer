# 初始化管理员入口治理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 仅在首次启动或完整系统重置后展示并允许初始化管理员。

**架构：** 后端以热配置和用户表状态共同决定初始化可用性，并通过公开最小状态接口提供给登录页。前端认证 Store 负责加载状态，登录页只按 Store 状态渲染初始化 Tab；管理员在通用设置中管理开关。

**技术栈：** FastAPI、SQLite、Vue 3、Pinia、TypeScript、Element Plus、unittest、Vitest

---

## 文件结构

- `backend/app/service/settings_service.py`：声明 `auth.admin_bootstrap_enabled` 热配置。
- `backend/app/service/auth_service.py`：计算初始化可用性并保护初始化操作。
- `backend/app/api/auth.py`：提供最小初始化状态接口。
- `backend/tests/test_auth_api.py`：验证状态接口、重复初始化和配置关闭。
- `backend/tests/test_settings_service.py`：验证配置默认值及布尔校验。
- `frontend/src/api/client.ts`、`frontend/src/api/client.test.ts`：增加初始化状态 API。
- `frontend/src/stores/auth.ts`、`frontend/src/stores/auth.test.ts`：维护初始化可用状态。
- `frontend/src/views/LoginView.vue`：按状态显示初始化 Tab。
- `frontend/src/views/SettingsView.vue`：增加管理员开关。
- `frontend/src/locales/zh-CN.ts`：增加配置和提示文案。
- `config/config.toml`、`config/config.example.toml`：默认关闭无人值守管理员自动创建。

### 任务 1：后端状态与初始化保护

- [x] **步骤 1：编写失败测试**

在 `test_auth_api.py` 中验证新数据库状态为可用、初始化后状态关闭、配置关闭时拒绝初始化、已有用户时重新开启配置仍不可初始化。

- [x] **步骤 2：运行测试并确认失败**

运行：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_auth_api backend.tests.test_settings_service -v
```

预期：状态接口返回 404，配置键不存在。

- [x] **步骤 3：实现后端最小代码**

配置定义：

```python
"auth.admin_bootstrap_enabled": SettingDefinition(
    key="auth.admin_bootstrap_enabled",
    category="operations",
    default=True,
    value_type="bool",
    description="Allow administrator bootstrap when no local users exist",
)
```

服务函数 `is_admin_bootstrap_available(settings)` 同时检查有效配置和用户数量。初始化服务在写用户前再次检查，并在成功后保存 `false`。

- [x] **步骤 4：运行后端定向测试**

运行任务 1 步骤 2 的命令，预期全部通过。

### 任务 2：前端状态 API 与 Store

- [x] **步骤 1：编写失败测试**

在 API 测试中断言 `GET /auth/bootstrap-status` 返回 `{ available }`；在 Store 测试中断言加载成功、加载失败默认隐藏、初始化成功后状态关闭。

- [x] **步骤 2：运行测试并确认失败**

```powershell
& 'C:\Users\txlfi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' ..\node_modules\vitest\vitest.mjs run src/api/client.test.ts src/stores/auth.test.ts
```

预期：客户端函数和 Store 状态不存在。

- [x] **步骤 3：实现 API 与 Store**

新增：

```ts
export type BootstrapStatus = { available: boolean };
export async function fetchBootstrapStatus(...): Promise<BootstrapStatus>;
```

Store 增加 `bootstrapAvailable` 和 `loadBootstrapStatus()`；请求异常时设置为 `false`。

- [x] **步骤 4：运行前端定向测试**

运行任务 2 步骤 2 的命令，预期全部通过。

### 任务 3：登录页和管理员配置

- [x] **步骤 1：实现登录页状态渲染**

登录页挂载时调用 `loadBootstrapStatus()`，为初始化 Tab 添加 `v-if="authStore.bootstrapAvailable"`。状态不可用时固定 `activeTab` 为 `login`。

- [x] **步骤 2：实现管理员配置**

通用设置表单增加 `adminBootstrapEnabled`，从 `auth.admin_bootstrap_enabled` 同步并保存；配置项仅在 `!settingsWriteDisabled` 时展示。

- [x] **步骤 3：补充中文文案**

增加“允许初始化管理员”和“仅在系统没有本地用户时生效”等文案。

- [x] **步骤 4：运行构建**

```powershell
$env:PATH='C:\Users\txlfi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:PATH
npm.cmd --prefix frontend run build
```

预期：TypeScript 检查和 Vite 构建通过。

### 任务 4：整体回归

- [x] **步骤 1：后端全量测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
```

- [x] **步骤 2：前端全量测试**

```powershell
& 'C:\Users\txlfi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' ..\node_modules\vitest\vitest.mjs run
```

- [x] **步骤 3：编码与差异检查**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check-encoding.ps1
git diff --check
```

预期：所有命令退出码为 0。
