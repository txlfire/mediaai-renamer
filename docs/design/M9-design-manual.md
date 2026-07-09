# MediaAI Renamer M9 设计手册

版本：草案
阶段：M9 权限、历史、回滚、审计和任务治理
更新日期：2026-07-09

## 1. 阶段目标

M9 在 M1-M8 的扫描、预览、匹配、命名规则和安全重命名流程稳定后，补齐治理能力。目标是让高风险操作有身份、权限、审计和可恢复路径。

目标：

- 建立本地管理员身份和会话基础。
- 对配置变更、媒体源变更、外部提交覆盖、真实重命名和回滚执行做权限守卫。
- 记录关键操作审计日志，支持筛选和追踪。
- 基于已完成的重命名批次生成回滚计划，先 dry-run 后执行。
- 建立任务治理视图，统一查看扫描、重命名、批量匹配和回滚类任务状态。

## 2. 范围

### 2.1 本阶段实现

- 本地单实例管理员账户。
- 会话登录、退出和当前用户查询。
- 用户权限枚举，权限直接挂在用户记录上。
- 后端敏感接口权限守卫。
- 前端登录页、当前用户展示和按钮权限禁用。
- 审计事件表和审计列表。
- 重命名回滚计划、回滚 dry-run 和回滚执行。
- 任务治理列表和任务详情。

### 2.2 本阶段不实现

- 企业级多租户。
- OAuth、LDAP、OIDC、双因素认证。
- 精细到单媒体源的数据权限。
- 跨设备会话管理。
- 对已被外部程序移动或覆盖的文件做强制回滚。
- WebSocket 实时任务推送。

## 3. 身份与权限设计

### 3.1 用户模型

建议新增 `users` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| username | 登录名，唯一 |
| display_name | 展示名 |
| password_hash | 密码哈希 |
| permissions_json | 用户权限列表 JSON |
| enabled | 是否启用 |
| last_login_at | 最近登录时间 |
| created_at / updated_at | 创建和更新时间 |

本地首次启动时，如果不存在用户，允许创建本地管理员。开发环境可通过明确配置允许免登录，但生产和 Docker 默认应启用登录保护。

### 3.2 会话模型

建议新增 `user_sessions` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| user_id | 用户 ID |
| token_hash | 会话 token 哈希 |
| expires_at | 过期时间 |
| created_at | 创建时间 |
| revoked_at | 退出或吊销时间 |

接口返回 token 时只返回明文一次，数据库只保存哈希。

### 3.3 用户权限枚举

建议使用后端固定权限枚举：

- `settings:write`：保存系统设置、AI Provider、敏感词、命名模板。
- `source:write`：新增、编辑、删除媒体源。
- `scan:run`：发起扫描任务。
- `metadata:submit`：发起 TMDB、IMDb、AI 外部提交。
- `rename:execute`：执行真实重命名。
- `rollback:execute`：执行回滚。
- `audit:read`：查看审计日志。
- `task:manage`：重试、取消或归档任务。

权限直接保存到用户记录中。首个初始化用户默认拥有全部权限；后续用户管理只调整权限列表，不引入角色层。

## 4. 审计设计

建议新增 `audit_events` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| event_type | 事件类型 |
| actor_id | 操作用户 ID，可为空 |
| actor_name | 操作用户展示名 |
| target_type | 目标类型 |
| target_id | 目标 ID |
| action | 操作名称 |
| result | `success` / `failed` |
| summary | 中文摘要 |
| detail_json | 脱敏后的结构化详情 |
| ip_address | 请求 IP |
| user_agent | 请求 UA |
| created_at | 创建时间 |

必须审计：

- 登录成功和失败。
- 保存系统设置。
- 媒体源新增、编辑、删除。
- 外部提交覆盖保护。
- dry-run 创建和真实重命名执行。
- 回滚计划创建、dry-run 和执行。
- 用户和权限变更。

敏感信息处理：

- API Key、token、密码和 SMB 凭据不得进入 `detail_json`。
- 文件路径可按现有 UI 规则展示完整路径，但导出审计时需要保留后续脱敏扩展点。

## 5. 回滚设计

### 5.1 回滚计划

建议新增 `rename_rollback_plans` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| operation_id | 原重命名批次 ID |
| status | `draft` / `checked` / `executed` / `partial_failed` / `failed` |
| item_count | 计划条目数 |
| executable_count | 可执行条目数 |
| conflict_count | 冲突条目数 |
| created_by | 创建用户 |
| created_at / updated_at | 创建和更新时间 |

建议新增 `rename_rollback_items` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| plan_id | 回滚计划 ID |
| operation_item_id | 原重命名明细 ID |
| current_path | 当前应存在的文件路径 |
| rollback_path | 计划回滚到的路径 |
| status | `pending` / `ready` / `conflict` / `executed` / `failed` |
| message | 中文结果或失败原因 |
| executed_at | 执行时间 |

### 5.2 回滚规则

- 只能基于已成功的重命名明细生成回滚计划。
- 回滚必须先 dry-run，检查当前文件是否存在、目标路径是否为空、目标文件是否已存在、目录是否可写。
- 回滚不覆盖已有文件。
- 回滚不跨目录创建复杂迁移语义，只按原重命名记录中的源路径和目标路径反向执行。
- 部分条目失败时，成功条目保留，失败条目记录原因，不做自动二次回滚。

## 6. 任务治理设计

M9 建议新增统一任务视图，不强制重构所有旧表为同一张任务表。

第一阶段可通过聚合接口展示：

- 扫描任务：`scan_jobs`。
- 重命名批次：`rename_operations`。
- 回滚计划：`rename_rollback_plans`。
- 批量 TMDB / AI 操作：使用现有批量结果摘要或后续新增轻量任务记录。

任务治理页面至少支持：

- 按任务类型、状态、媒体源、时间范围筛选。
- 查看任务详情和失败原因。
- 对支持的任务执行重试、归档或跳转到关联页面。
- 对运行中任务显示当前阶段和最近更新时间。

## 7. API 设计

### 7.1 认证

```http
POST /api/auth/login
POST /api/auth/logout
GET /api/auth/me
POST /api/auth/bootstrap-admin
```

### 7.2 用户管理

```http
GET /api/users
POST /api/users
PUT /api/users/{user_id}
POST /api/users/{user_id}/reset-password
POST /api/users/{user_id}/disable
```

### 7.3 审计

```http
GET /api/audit-events
GET /api/audit-events/{event_id}
```

### 7.4 回滚

```http
POST /api/rename-operations/{operation_id}/rollback-plan
POST /api/rename-rollback-plans/{plan_id}/dry-run
POST /api/rename-rollback-plans/{plan_id}/execute
GET /api/rename-rollback-plans
GET /api/rename-rollback-plans/{plan_id}
```

### 7.5 任务治理

```http
GET /api/task-center
GET /api/task-center/{task_type}/{task_id}
POST /api/task-center/{task_type}/{task_id}/archive
```

## 8. 前端设计

- 未登录时展示登录页，不加载业务数据。
- 左下角展示当前用户和退出入口。
- 系统设置增加“用户与权限”“审计日志”“任务治理”分类。
- 只读用户看得到列表和详情，但执行类按钮禁用并有 tooltip 提示。
- 回滚入口放在重命名操作历史或任务详情中，必须经过 dry-run 和二次确认。
- 审计列表默认按时间倒序展示，支持事件类型、结果、操作者和时间筛选。

## 9. 验收标准

- 首次启动无用户时可创建管理员。
- 管理员可登录、退出、查看当前用户。
- 未登录请求敏感接口返回明确 401。
- 权限不足请求返回明确 403。
- 真实重命名、回滚执行和系统设置保存均写入审计事件。
- 回滚 dry-run 能发现目标冲突和源文件缺失。
- 回滚执行不覆盖已有文件，失败条目有明确原因。
- 任务治理页可查看扫描、重命名和回滚任务。

## 10. 风险和处理

- 引入登录后可能影响本地开发效率：开发环境用明确配置控制免登录，生产默认开启。
- 旧接口增加权限守卫可能破坏前端流程：先统一 API 客户端处理 401/403，再逐步覆盖敏感接口。
- 回滚涉及真实文件操作：完全复用 M3 dry-run 和共享目录校验，不单独绕过安全检查。
- 审计数据可能增长：列表分页，后续可增加保留周期和归档策略。
