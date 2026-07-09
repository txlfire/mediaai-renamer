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
- 记录扫描、重命名、回滚等长耗时操作的过程日志，支持从页面实时查看和事后追溯。
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
- 操作运行日志表和实时日志查看入口。
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

### 4.1 审计日志和操作运行日志边界

M9 日志体系分两层：

- 审计日志：回答“谁在什么时间对什么对象做了什么，结果是什么”，用于权限追责和安全追踪。
- 操作运行日志：回答“扫描、重命名、回滚执行过程中每一步发生了什么”，用于实时观察、失败定位和事后复盘。

两类日志都必须脱敏，但职责不能混用。审计日志保留关键摘要和结构化结果；操作运行日志保留任务过程、阶段、进度和失败原因。

## 4.2 操作运行日志设计

建议新增 `operation_logs` 表：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| task_type | 任务类型：`scan_job` / `rename_operation` / `rollback_plan` / `metadata_batch` / `ai_batch` |
| task_id | 关联任务 ID |
| level | `info` / `warning` / `error` / `success` |
| stage | 执行阶段，例如 `prepare` / `validate` / `scan` / `dry_run` / `execute` / `finish` |
| message | 中文日志内容 |
| progress_current | 当前进度，可为空 |
| progress_total | 总进度，可为空 |
| detail_json | 脱敏后的结构化详情 |
| approx_bytes | 该条日志估算大小，用于总量清理 |
| created_at | 创建时间 |

索引：

- `(task_type, task_id, id)`：用于任务弹窗按增量 ID 拉取日志。
- `(created_at)`：用于清理和后续任务治理聚合。

写入原则：

- 任务开始、参数快照、前置校验、批处理进度、单项失败、部分完成、最终结果必须写入。
- 高频循环不逐文件写入普通成功日志；扫描可按批次写入进度，失败和跳过原因按条写入或按批聚合。
- 真实重命名必须记录每个失败项；成功项可记录汇总和必要的源 / 目标路径。
- 路径可展示完整值，凭据、token、API Key、密码、Authorization、SMB secret 必须脱敏。
- 操作日志写入失败不得中断扫描或重命名主流程，只能降级为后端文件日志 warning。

首批覆盖范围：

- 扫描任务：创建任务、读取媒体源、路径校验、批量扫描进度、小文件过滤、失败/跳过摘要、完成状态。
- 重命名 dry-run：批次创建、条目数量、冲突检查、空名检查、共享协议写入校验、可执行/冲突汇总。
- 真实重命名执行：执行开始、逐项失败、成功/失败汇总、索引同步结果、最终状态。
- 回滚：随 M9-4 接入回滚计划创建、dry-run 和执行日志。

保留策略：

- 默认跟随现有 `logging.retention_days`，清理逻辑与“通用设置”中的日志保留天数保持一致；后续可增加独立 `operation_logs.retention_days`，但不能与现有系统设置表现冲突。
- 必须同时支持大小限制，避免短时间大量日志撑大 SQLite。建议新增或复用系统设置：
  - `operation_logs.max_total_mb`：操作运行日志总量上限，默认 128 MB。
  - `operation_logs.max_task_mb`：单个任务日志上限，默认 16 MB。
  - `operation_logs.cleanup_batch_size`：单次清理批量，默认 1000 条，避免一次删除阻塞。
- 清理任务应按 `created_at` 删除过期 `operation_logs`，并保留任务本身状态、统计和审计事件。
- 超过总量上限时，按 `created_at ASC` 优先清理最早日志；超过单任务上限时，按该任务内 `id ASC` 优先清理最早日志。
- 清理后应保留任务最后若干条关键日志，至少包括最终状态、错误和 warning；如果必须清理到只剩摘要，应由摘要接口明确提示“详细日志已部分清理，仅保留摘要”。
- 日志写入时应维护估算大小，至少按 `message + detail_json` UTF-8 字节数估算；清理任务可用估算值快速判断，不要求每次精确扫描整表。
- 日志清理后，查询接口不能返回空白误导用户，应返回 `logAvailable=false`、`cleared=true` 和中文提示，例如“该任务日志已按保留策略清理”。
- 扫描任务列表、重命名批次和任务治理入口应根据摘要接口结果控制“查看日志”按钮：日志已清理时按钮置灰并展示 tooltip；如果仍允许点击，则弹窗内必须明确提示日志已清理。
- 删除历史操作日志只影响可观测数据，不删除扫描结果、重命名批次和审计事件。

## 4.3 实时查看设计

不在 M9 引入 WebSocket。实时查看采用前端轮询，满足本地部署和 NAS 场景下的简单可靠性：

```http
GET /api/operation-logs?task_type=scan_job&task_id=123&after_id=456&limit=200
GET /api/operation-logs/summary?task_type=scan_job&task_id=123
```

响应建议：

```json
{
  "items": [
    {
      "id": 457,
      "level": "info",
      "stage": "scan",
      "message": "已扫描 200 / 1200 个文件",
      "progressCurrent": 200,
      "progressTotal": 1200,
      "createdAt": "2026-07-09T10:00:00+00:00"
    }
  ],
  "latestId": 457,
  "running": true,
  "logAvailable": true,
  "cleared": false,
  "message": null
}
```

性能约束：

- 默认轮询间隔不小于 1500 ms；任务高频写入时可退避到 3000-5000 ms，任务结束后立即停止自动轮询。
- 单次拉取默认 `limit=100`，最大不得超过 500；接口应按 `id ASC` 返回，前端用 `after_id` 增量拉取。
- 后端写入扫描进度必须节流：普通成功文件不逐条写入，按批次、时间间隔或状态变化写入；错误、warning 和最终状态不丢弃。
- 前端日志列表最多保留最近 1000 条渲染项；超过上限时折叠旧日志，保留“已隐藏较早日志 N 条”的提示，避免 DOM 持续膨胀。
- 日志文本区域应避免复杂富文本渲染；优先使用轻量列表、等宽文本和固定高度滚动容器。
- 弹窗隐藏、路由切换、用户关闭自动刷新、浏览器标签页不可见时必须停止或降频轮询。
- 连续请求失败时指数退避，并在界面提示“日志刷新失败，已降低刷新频率”。

前端弹窗策略：

- 触发扫描后自动打开“扫描日志”弹窗或右侧抽屉。
- 触发 dry-run / 真实重命名后自动打开“重命名日志”弹窗或在现有重命名结果弹窗中嵌入实时日志区域。
- 扫描任务列表和重命名批次详情增加“查看日志”图标按钮。
- 弹窗支持自动刷新开关、手动刷新、只看 warning/error、复制日志、导出文本。
- 弹窗底部默认跟随最新日志；用户向上滚动后暂停自动贴底，避免阅读时跳动。
- 任务完成、失败或取消后停止轮询，保留最后状态和日志。
- 如果摘要接口返回日志已清理，入口按钮置灰并显示原因；弹窗已打开时显示空态提示，不持续轮询。

权限：

- 查看操作运行日志需要对应任务的读取权限；M9 第一阶段可复用登录态。
- 如果后续需要细分权限，可复用 `audit:read` 或新增 `operation_log:read`，但当前不建议增加权限复杂度。

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
POST /api/auth/change-password
POST /api/auth/reset-admin-password
```

首次启动时允许创建首个管理员。`config/config.toml` 可启用默认 `admin` 初始化，默认密码为 `123456`，账号会被标记为必须改密；用户登录后如果未改密，每次登录都应提示修改。隐藏恢复接口 `reset-admin-password` 仅在配置文件显式开启时可用，用于将 `admin` 重置为默认密码并重新要求改密。

### 7.2 用户管理

```http
GET /api/users
POST /api/users
PUT /api/users/{user_id}
POST /api/users/{user_id}/reset-password
```

用户管理只维护本地用户信息、启用状态、临时密码和直接权限列表，不提供角色模型。系统设置页提供用户信息维护入口，操作权限复用 `settings:write`；后端需阻止移除最后一个启用且具备 `settings:write` 的用户。

### 7.3 审计

```http
GET /api/audit-events
GET /api/audit-events/{event_id}
```

### 7.3.1 操作运行日志

```http
GET /api/operation-logs
GET /api/operation-logs/summary
GET /api/operation-logs/export
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
- 扫描任务和重命名操作提供“查看日志”入口；任务触发后自动弹出实时日志窗口。
- 实时日志窗口使用结构化日志流展示阶段、级别、时间和进度，不直接展示后端原始文件日志。

## 9. 验收标准

- 首次启动无用户时可创建管理员。
- 管理员可登录、退出、查看当前用户。
- 未登录请求敏感接口返回明确 401。
- 权限不足请求返回明确 403。
- 真实重命名、回滚执行和系统设置保存均写入审计事件。
- 扫描任务可实时查看过程日志，并能在任务结束后回看。
- 重命名 dry-run 和真实执行可实时查看过程日志，失败项有明确原因。
- 回滚 dry-run 能发现目标冲突和源文件缺失。
- 回滚执行不覆盖已有文件，失败条目有明确原因。
- 任务治理页可查看扫描、重命名和回滚任务。

## 10. 风险和处理

- 引入登录后可能影响本地开发效率：开发环境用明确配置控制免登录，生产默认开启。
- 旧接口增加权限守卫可能破坏前端流程：先统一 API 客户端处理 401/403，再逐步覆盖敏感接口。
- 回滚涉及真实文件操作：完全复用 M3 dry-run 和共享目录校验，不单独绕过安全检查。
- 审计数据可能增长：列表分页，后续可增加保留周期和归档策略。
