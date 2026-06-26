# M4 TMDB 热配置与刮削实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 按 `docs/development/m4/` 新版文档全量落地 M4：统一热配置、TMDB 刮削、命名预览回填、文件过滤和待处理运维列表。

**架构：** M4 分为四个可验证小版本。先做全局热配置底座，再接 TMDB 搜索与匹配算法，然后回填命名预览，最后补文件过滤与待处理运维列表。所有新增能力必须可降级，不破坏 M3 本地解析和安全重命名流程。

**技术栈：** FastAPI、SQLite、Vue 3、Pinia、Element Plus、Vitest、unittest。

---

## M4-0：前置风险修正

**文件：**
- 修改：`backend/app/core/config.py`
- 修改：`backend/tests/test_health.py`
- 修改：`backend/app/core/database.py`
- 创建：`backend/tests/test_database_migrations.py`

- [x] **步骤 1：写失败测试**

将健康检查测试版本期望从 `0.3.0` 改为 `0.3.1`。

- [x] **步骤 2：验证红灯**

运行：

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_health -v
```

预期：失败，后端实际仍返回 `0.3.0`。

- [x] **步骤 3：修复后端版本**

将 `AppSettings.version` 更新为 `0.3.1`。

- [x] **步骤 4：验证绿灯**

运行：

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_health -v
npm.cmd run backend:test
```

预期：全部通过。

- [x] **步骤 5：补轻量数据库迁移框架**

新增 `app_meta.schema_version` 机制，当前 schema 版本为 4。`ensure_database()` 在服务启动时会补齐 M4 新表和 `rename_previews` 元数据字段。

- [x] **步骤 6：验证旧库升级**

运行：

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_database_migrations -v
npm.cmd run backend:test
```

预期：旧 M3 数据库可升级到当前 schema，新库会记录当前 schema version。

## M4-1：统一热配置中心

**目标：** 提供后端热配置表、配置定义、优先级读取、参数校验、敏感值脱敏、配置 API 和前端设置页基础框架。

**文件：**
- 修改：`backend/app/core/database.py`
- 创建：`backend/app/schema/settings.py`
- 创建：`backend/app/service/settings_service.py`
- 创建：`backend/app/api/settings.py`
- 修改：`backend/app/main.py`
- 创建：`backend/tests/test_settings_service.py`
- 创建：`backend/tests/test_settings_api.py`
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/router/index.ts`
- 创建：`frontend/src/stores/settings.ts`
- 创建：`frontend/src/views/SettingsView.vue`
- 修改：`frontend/src/locales/zh-CN.ts`

### 后端

- [x] **步骤 1：写配置服务失败测试**

覆盖行为：
- 默认配置返回 `zh-CN`、`CN`、`10000`、`0`。
- 环境变量优先于数据库配置。
- 敏感配置前端返回脱敏值。
- 非法超时、非法地区、非法布尔值、负数阈值保存失败。
- 更新数据库配置后无需重启即可读取新值。

- [x] **步骤 2：运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_settings_service -v
```

预期：导入失败或表/服务不存在。

- [x] **步骤 3：实现数据库表与服务**

新增 `system_settings` 表，服务层统一定义 M4 Online 配置：
- `tmdb.api_key`
- `tmdb.language`
- `tmdb.region`
- `tmdb.timeout_ms`
- `tmdb.enabled`
- `tmdb.priority`
- `scan.minimum_file_size`

- [x] **步骤 4：运行配置服务测试通过**

- [x] **步骤 5：写配置 API 失败测试**

覆盖 GET / PUT 行为、脱敏、校验失败响应。

- [x] **步骤 6：实现 API 并注册路由**

- [x] **步骤 7：后端全量测试**

运行：

```powershell
npm.cmd run backend:test
```

### 前端

- [x] **步骤 8：补 API client 类型和测试**

- [x] **步骤 9：新增设置菜单、路由、页面和 store**

设置页采用左侧分类菜单 + 右侧表单。本期只实现 TMDB 元数据刮削配置，其余分类占位。

- [x] **步骤 10：前端测试和构建**

运行：

```powershell
npm.cmd run frontend:test
npm.cmd run frontend:build
```

## M4-2：TMDB 搜索与匹配算法

**目标：** 新增 TMDB 客户端、Mock 友好搜索服务、候选结果模型、统一匹配度算法和异常降级。

**文件：**
- 创建：`backend/app/schema/metadata.py`
- 创建：`backend/app/service/tmdb_client.py`
- 创建：`backend/app/service/metadata_matcher.py`
- 创建：`backend/app/service/metadata_service.py`
- 创建：`backend/tests/test_metadata_matcher.py`
- 创建：`backend/tests/test_tmdb_metadata_service.py`

- [x] 写匹配算法失败测试。
- [x] 实现 100 分加权算法。
- [x] 写 TMDB 服务降级和 Mock 搜索测试。
- [x] 实现 TMDB 客户端与服务。
- [x] 后端全量测试。

## M4-3：命名预览 TMDB 回填

**目标：** 命名预览支持 TMDB 匹配状态、来源站点、匹配分数、候选弹窗和人工选择回填。

**文件：**
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/schema/media.py`
- 修改：`backend/app/service/preview_service.py`
- 修改：`backend/app/api/rename_previews.py`
- 修改：`frontend/src/views/RenamePreviewsView.vue`
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/stores/preview.ts`

- [x] 写回填服务测试。
- [x] 实现字段扩展和 API。
- [x] 前端增加 TMDB 匹配按钮、状态列、候选弹窗。
- [x] 后端、前端测试和构建。

## M4-4：文件过滤与待处理运维列表

**目标：** 扫描阶段按最小文件阈值分流；本地解析后低价值文件进入待处理列表；支持移除任务、清空列表、单选/批量迁移文件。

**文件：**
- 修改：`backend/app/core/database.py`
- 创建：`backend/app/schema/pending_files.py`
- 创建：`backend/app/service/pending_file_service.py`
- 修改：`backend/app/service/scan_service.py`
- 创建：`backend/app/api/pending_files.py`
- 修改：`frontend/src/views/RenamePreviewsView.vue`
- 创建：`frontend/src/stores/pendingFiles.ts`

- [x] 写扫描过滤失败测试。
- [x] 实现待处理表和服务。
- [x] 实现移除、清空、迁移 API。
- [x] 前端增加待处理列表模块。
- [x] 全量回归测试。

## 收尾

- [x] 更新 README 当前阶段。
- [x] 编写 M4 设计手册与用户手册。
- [x] 生成工作日志。
- [ ] 合并、打包、发布 M4 版本。
