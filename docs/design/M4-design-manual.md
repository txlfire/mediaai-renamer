# MediaAI Renamer M4 设计手册

版本：v0.4.0 预览版
阶段：M4 TMDB 元数据刮削与文件运维
日期：2026-06-26

## 1. 阶段结论

M4 在 M3 安全重命名基础上，完成统一热配置、TMDB 元数据匹配、命名预览回填和待处理文件运维闭环。

新增能力均为后置增强。关闭 TMDB 或未配置密钥时，系统会降级为 M3 的本地解析与安全重命名流程，不阻断扫描、预览和重命名。

## 2. 已包含范围

- 轻量数据库迁移框架。
- 统一系统设置页和热配置 API。
- TMDB API Key、语言、地区、超时、启用开关和站点优先级配置。
- 文件最小扫描阈值热配置。
- TMDB 搜索客户端和可替换 provider 接口。
- 100 分加权匹配算法。
- 命名预览 TMDB 匹配、候选列表和人工选择回填。
- 待处理文件表、列表、单条移除、清空和批量迁移接口。
- 命名预览页面展示元数据匹配状态、匹配度和待处理文件列表。

## 3. 未包含范围

- 多站点真实接入，当前只落地 TMDB 主站。
- DeepSeek / AI 解析。
- 元数据缓存表。
- 元数据批量自动匹配任务队列。
- 用户权限审批和配置审计页面。
- 文件迁移失败的逐项重试界面。

## 4. 数据库设计

新增基础元数据表：

- `app_meta`：记录 `schema_version`，当前版本为 `5`。
- `system_settings`：保存可热更新配置。
- `pending_files`：保存待处理文件任务。

扩展表：

- `rename_previews.metadata_source`
- `rename_previews.metadata_match_status`
- `rename_previews.metadata_match_score`
- `rename_previews.metadata_message`

迁移原则：

- 服务启动时由 `ensure_database()` 补齐缺失表和字段。
- 旧 M3 数据库可直接升级，不需要手动迁移脚本。
- 新增字段均不影响 M3 已有预览和重命名数据。

## 5. 后端设计

新增模块：

- `backend/app/service/settings_service.py`
- `backend/app/service/metadata_matcher.py`
- `backend/app/service/metadata_service.py`
- `backend/app/service/tmdb_client.py`
- `backend/app/service/pending_file_service.py`

新增 API：

```text
GET /api/settings
PUT /api/settings
POST /api/rename-previews/{preview_id}/metadata-match
GET /api/rename-previews/{preview_id}/metadata-candidates
POST /api/rename-previews/{preview_id}/metadata-candidate
GET /api/pending-files
DELETE /api/pending-files/{pending_file_id}
POST /api/pending-files/clear
POST /api/pending-files/move
```

配置优先级：

```text
环境变量 > 数据库热配置 > 默认值
```

## 6. 匹配算法

匹配总分为 100 分：

- 标题相似度：0-60 分。
- 年份：一致 20 分，相差 1 年 15 分，否则 0 分。
- 媒体类型：一致 10 分，类型不一致直接 0 分。
- 季/集：剧集季集一致 10 分；电影默认 10 分。

状态映射：

- `high_confidence`：85 分及以上，自动回填。
- `low_confidence`：30 到 84 分，展示候选供人工选择。
- `failed`：30 分以下或服务降级，不修改目标文件名。

## 7. 文件分流

扫描阶段读取 `scan.minimum_file_size`。

- 文件大小小于阈值：进入 `pending_files`，原因标记为 `size_filtered`。
- 文件大小达到阈值：进入正常 `media_files` 扫描结果。

待处理列表操作只修改任务记录。单条移除和清空不会删除源文件；批量迁移会移动用户选中的真实文件。

## 8. 前端设计

新增设置页：

- 左侧配置分类。
- 右侧 TMDB 和扫描阈值配置表单。
- 敏感密钥脱敏展示，保存时可覆写。

命名预览页新增：

- TMDB 匹配图标按钮。
- 元数据状态列。
- 匹配度列。
- 低置信候选弹窗。
- 待处理文件列表。
- 待处理文件单条移除、清空和批量迁移入口。

## 9. 验证记录

发布前需要执行：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
```

当前验证结果：

- 后端全量测试：66 个通过。
- 前端 API 测试：9 个通过。
- 前端构建通过，保留既有 Vite chunk size 提示。
