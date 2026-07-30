# 登录记忆与免登录配置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现浏览器原生保存密码和管理员可配置的长会话期限。

**架构：** 登录请求携带长会话选择，后端从热配置读取期限并签发对应会话。前端以 sessionStorage/localStorage 区分短会话和长会话，密码只交给浏览器原生密码管理器。

**技术栈：** FastAPI、Pydantic、SQLite、Vue 3、Pinia、TypeScript、Element Plus、Vitest

---

## 文件结构

- `backend/app/service/settings_service.py`：声明管理员可配置的长会话天数。
- `backend/app/service/auth_service.py`：根据登录模式计算会话到期时间。
- `backend/app/api/auth.py`：接收 `rememberLogin` 并传递到认证服务。
- `backend/tests/test_auth_api.py`：验证登录接口和会话期限。
- `backend/tests/test_settings_service.py`：验证长会话配置默认值和范围。
- `frontend/src/api/client.ts`：扩展登录请求并管理双 Token 存储。
- `frontend/src/api/client.test.ts`：验证双存储行为。
- `frontend/src/stores/auth.ts`、`frontend/src/stores/auth.test.ts`：传递登录选项。
- `frontend/src/views/LoginView.vue`：增加两个选项和浏览器自动填充语义。
- `frontend/src/views/SettingsView.vue`：在通用设置中展示管理员配置。
- `frontend/src/locales/zh-CN.ts`：增加中文文案。

### 任务 1：后端会话期限与管理员配置

- [x] **步骤 1：编写失败测试**

在认证测试中断言旧登录请求约 24 小时过期，`rememberLogin=true` 默认约 7 天过期；在设置测试中断言 `auth.remember_login_days` 默认值为 7 且拒绝 0 和 31。

- [x] **步骤 2：运行测试验证失败**

运行：`$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_auth_api backend.tests.test_settings_service -v`

预期：长会话字段或设置定义缺失导致断言失败。

- [x] **步骤 3：实现最少后端代码**

增加：

```python
"auth.remember_login_days": SettingDefinition(
    key="auth.remember_login_days",
    category="operations",
    default=7,
    value_type="int",
    description="Remember-login session duration in days",
    min_value=1,
    max_value=30,
)
```

登录接口使用 `remember_login: bool = False`，长会话从有效设置读取天数，短会话保持 24 小时。

- [x] **步骤 4：运行测试验证通过**

运行：`$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_auth_api backend.tests.test_settings_service -v`

预期：全部通过。

### 任务 2：前端 Token 双存储

- [x] **步骤 1：编写失败测试**

在 `client.test.ts` 中断言短会话 Token 写入 sessionStorage、长会话 Token 写入 localStorage、退出登录同时清理两处。

- [x] **步骤 2：运行测试验证失败**

运行：`npm.cmd --prefix frontend test -- --run src/api/client.test.ts`

预期：现有实现始终写入 localStorage，测试失败。

- [x] **步骤 3：实现最少客户端代码**

扩展登录类型：

```ts
export type LoginPayload = {
  username: string;
  password: string;
  rememberLogin?: boolean;
};
```

`setAuthToken(token, persistent)` 根据 `persistent` 选择存储，并先清理另一处。

- [x] **步骤 4：运行测试验证通过**

运行：`npm.cmd --prefix frontend test -- --run src/api/client.test.ts`

预期：全部通过。

### 任务 3：登录页和管理员设置界面

- [x] **步骤 1：编写失败测试**

在 Store 测试中断言 `rememberLogin` 被传递至 API；通过组件源码约束测试登录字段的 `name`、`autocomplete` 和固定“一周免登录”文案。

- [x] **步骤 2：运行测试验证失败**

运行：`npm.cmd --prefix frontend test -- --run src/stores/auth.test.ts`

预期：Store 尚未接收登录选项，测试失败。

- [x] **步骤 3：实现最少界面代码**

登录页增加独立复选框；通用设置使用现有动态设置渲染机制展示 `auth.remember_login_days`，并沿用 `settings:write` 权限。

- [x] **步骤 4：运行测试和构建**

运行：

```powershell
npm.cmd --prefix frontend test -- --run src/stores/auth.test.ts src/api/client.test.ts
npm.cmd --prefix frontend run build
```

预期：测试和构建全部通过。

### 任务 4：整体回归

- [x] **步骤 1：运行后端认证与设置回归**

运行：`$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_auth_api backend.tests.test_settings_api backend.tests.test_settings_service -v`

- [x] **步骤 2：运行前端回归**

运行：`npm.cmd --prefix frontend test -- --run`

- [x] **步骤 3：运行编码检查**

运行：`powershell -ExecutionPolicy Bypass -File scripts/check-encoding.ps1`

- [x] **步骤 4：检查差异**

运行：`git diff --check`

预期：所有命令退出码为 0。
