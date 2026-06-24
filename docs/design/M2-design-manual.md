# MediaAI Renamer M2 设计手册

版本：v0.2.0 预览版
阶段：M2 标准命名与预览
日期：2026-06-24

## 1. 阶段结论

M2 实现本地文件名解析、标准命名预览、预览记录保存和目标文件名手动编辑。

本阶段不会调用外部元数据服务，不会调用 AI，也不会修改真实文件。M2 生成的 `rename_previews` 记录会作为后续 M3 安全重命名的输入。

## 2. 已包含范围

- 本地文件名清洗和解析。
- 电影、剧集和待识别三类基础识别。
- 标准目标文件名生成。
- `rename_previews` 数据表。
- 预览批量生成、查询和编辑 API。
- 命名预览前端页面。
- 状态、类型、媒体源、扫描任务和关键字筛选。
- 预览列表分页和默认更新时间排序。
- 编辑目标文件名弹窗。

## 3. 未包含范围

- 真实文件重命名。
- 冲突检测。
- 目标目录规划。
- 外部元数据匹配。
- DeepSeek 或其他 AI 解析。
- 批量确认和执行流程。

## 4. 后端设计

新增模块：

- `backend/app/utils/filename_parser.py`：本地文件名解析。
- `backend/app/utils/naming_template.py`：标准目标文件名生成。
- `backend/app/service/preview_service.py`：预览生成、查询和编辑。
- `backend/app/api/rename_previews.py`：命名预览 API。

新增数据模型：

- `ParsedMediaName`
- `RenamePreview`
- `PreviewGenerationSummary`

## 5. 数据库设计

新增表：`rename_previews`

关键字段：

- `media_file_id`
- `media_type`
- `parsed_title`
- `parsed_year`
- `season`
- `episode`
- `original_extension`
- `suggested_name`
- `edited_name`
- `status`
- `message`
- `created_at`
- `updated_at`

约束：

- 一个媒体文件只保留一条当前预览。
- 重新生成预览会更新同一个 `media_file_id` 的预览记录。
- 已编辑记录在默认情况下会保留用户编辑结果。

## 6. API 设计

生成预览：

```text
POST /api/rename-previews/generate
```

查询预览：

```text
GET /api/rename-previews
```

编辑预览：

```text
PUT /api/rename-previews/{preview_id}
```

查询支持参数：

- `media_source_id`
- `scan_job_id`
- `status`
- `media_type`
- `keyword`

## 7. 前端设计

左侧菜单新增“命名预览”，位于“扫描结果”之后。

页面包含：

- 顶部操作区：刷新、生成预览。
- 筛选区：媒体源、扫描任务、状态、类型、关键字。
- 统计区：总数、可使用、需检查、已编辑。
- 预览表格：原文件名、类型、解析标题、年份、季集、目标文件名、状态、更新时间、操作。
- 编辑弹窗：修改目标文件名。

页面不提供“执行重命名”入口。

## 8. 验证记录

已执行：

```powershell
$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest discover backend\tests -v
npm.cmd run frontend:test
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts frontend/src/stores/pagination.test.ts frontend/src/stores/tableSort.test.ts frontend/src/stores/preview.test.ts frontend/src/utils/displayFormat.test.ts
npm.cmd run frontend:build
```

结果：

- 后端 25 项测试通过。
- 前端 API 测试 6 项通过。
- 前端 store 和工具测试 21 项通过。
- 前端构建通过。

已知提示：

- 前端构建仍保留包体积超过默认阈值提示，不影响 M2 功能。
