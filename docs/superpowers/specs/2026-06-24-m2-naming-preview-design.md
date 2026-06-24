# M2 标准命名与预览设计

## 1. 目标

M2 的目标是在不调用外部 API、不修改真实文件的前提下，基于 M1 已扫描出的媒体文件，生成可查看、可编辑、可保存的标准命名预览。

本阶段要让用户能从扫描结果进入“命名预览”，看到原文件名、本地解析结果、建议的新文件名和预览状态，并能手动修正目标文件名。M2 产出的预览记录会作为 M3 安全重命名的输入。

## 2. 非目标

M2 不实现以下能力：

- 不执行真实文件重命名。
- 不调用 TMDB、Bangumi、TVDB、豆瓣等外部元数据源。
- 不调用 DeepSeek 或任何 AI/LLM。
- 不做候选元数据匹配和置信度排序。
- 不做 dry-run 冲突检测和批量重命名执行。
- 不做增量扫描。

真实重命名、冲突检测和导出能力归入 M3；外部元数据和 AI 解析归入 M4/M5。

## 3. 输入与输出

### 3.1 输入

M2 使用 M1 的 `media_files` 表作为输入，至少需要以下字段：

- `id`
- `media_source_id`
- `scan_job_id`
- `file_path`
- `file_name`
- `extension`

前端页面可以按媒体源、扫描任务、媒体类型和预览状态筛选。

### 3.2 输出

M2 新增命名预览记录。每条记录对应一个媒体文件，保存本地解析结果和目标文件名：

- 原媒体文件 ID。
- 解析出的媒体类型：`movie`、`episode`、`unknown`。
- 解析出的标题。
- 解析出的年份。
- 解析出的季号。
- 解析出的集号。
- 原扩展名。
- 建议目标文件名。
- 用户编辑后的目标文件名。
- 预览状态：`generated`、`edited`、`needs_review`。
- 解析说明或错误信息。
- 创建和更新时间。

目标文件名只保存文件名，不保存目标目录路径。M3 再负责目标路径、冲突检测和真实重命名。

## 4. 本地文件名解析

### 4.1 清洗规则

解析前先对文件名主体做基础清洗：

- 去掉扩展名。
- 将 `_`、`-`、空格、连续点号统一视为分隔符。
- 去掉常见清晰度和来源标记，例如 `1080p`、`2160p`、`720p`、`WEB-DL`、`BluRay`、`BDRip`、`HDRip`、`x264`、`x265`、`H.264`、`H.265`、`AAC`、`DDP`。
- 去掉常见字幕和发布组标记的方括号内容，但保留标题中必要的中文、英文、数字和年份。
- 多个连续分隔符合并为一个空格。

清洗只影响解析和建议命名，不改变真实文件。

### 4.2 剧集识别

优先识别剧集模式，支持以下常见写法：

- `S01E02`
- `s1e2`
- `Season 1 Episode 2`
- `第1季第2集`
- `第02集`
- `E02`

识别规则：

- 出现明确季集信息时，媒体类型为 `episode`。
- 只有集号、没有季号时，默认季号为 `1`。
- 集号和季号输出为整数，命名时补齐两位。
- 标题取季集标记之前的清洗后文本。

### 4.3 电影识别

未识别为剧集时，尝试识别电影：

- 文件名中出现 `19xx` 或 `20xx` 年份时，媒体类型为 `movie`。
- 标题取年份之前的清洗后文本。
- 没有年份但能得到有效标题时，仍可生成 `unknown` 类型预览，状态为 `needs_review`，用户可以手动编辑。

### 4.4 解析失败

以下情况标记为 `needs_review`：

- 标题为空。
- 只能识别出清晰度、编码、发布组等噪声信息。
- 季号或集号不合法。
- 生成的目标文件名为空。

`needs_review` 项仍然显示在预览列表中，允许用户编辑目标文件名。

## 5. 标准命名模板

M2 初版固定使用默认模板，不开放高级模板配置。

电影默认模板：

```text
{title}.{year}{extension}
```

年份缺失时：

```text
{title}{extension}
```

剧集默认模板：

```text
{title}.S{season:02d}E{episode:02d}{extension}
```

统一要求：

- 标题内部使用英文点号 `.` 分隔。
- 非法文件名字符替换为空格后再归一化为英文点号。
- Windows 非法字符必须移除：`< > : " / \ | ? *`。
- 保留原扩展名，并统一使用扫描记录中的小写扩展名。
- 不生成目录结构，只生成目标文件名。

## 6. 数据库设计

新增 `rename_previews` 表：

```text
id INTEGER PRIMARY KEY AUTOINCREMENT
media_file_id INTEGER NOT NULL UNIQUE
media_type TEXT NOT NULL
parsed_title TEXT NOT NULL
parsed_year INTEGER
season INTEGER
episode INTEGER
original_extension TEXT NOT NULL
suggested_name TEXT NOT NULL
edited_name TEXT
status TEXT NOT NULL
message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
FOREIGN KEY(media_file_id) REFERENCES media_files(id)
```

约束：

- 一个媒体文件只保留一条当前预览记录。
- 重新生成预览时覆盖同一 `media_file_id` 的解析结果。
- 如果用户已经编辑过 `edited_name`，重新生成时默认保留用户编辑结果，除非请求显式要求覆盖编辑。

## 7. 后端服务

新增本地解析和预览服务：

- `filename_parser`：负责清洗文件名、识别电影/剧集、返回解析结构。
- `naming_template`：负责根据解析结构生成标准目标文件名。
- `preview_service`：负责批量生成、查询、编辑和保存预览记录。

服务边界：

- 解析器不访问数据库。
- 模板生成器不访问数据库。
- 预览服务负责读取 `media_files` 和写入 `rename_previews`。
- API 层只做请求参数处理和响应转换。

## 8. API 设计

所有接口继续使用 `/api` 前缀。

### 8.1 生成预览

```text
POST /api/rename-previews/generate
```

请求字段：

- `media_source_id`：可选，按媒体源生成。
- `scan_job_id`：可选，按扫描任务生成。
- `media_file_ids`：可选，按指定文件生成。
- `overwrite_edited`：可选，默认 `false`。

规则：

- 如果三个筛选条件都为空，则对全部媒体文件生成预览。
- 如果指定多个条件，则取交集。
- 请求只生成或更新数据库预览记录，不修改真实文件。

响应字段：

- `generated_count`
- `needs_review_count`
- `edited_kept_count`

### 8.2 查询预览

```text
GET /api/rename-previews
```

查询参数：

- `media_source_id`
- `scan_job_id`
- `status`
- `media_type`
- `keyword`

响应列表字段：

- 预览 ID。
- 媒体文件 ID。
- 原文件名。
- 原文件路径。
- 媒体类型。
- 解析标题、年份、季号、集号。
- 建议目标文件名。
- 当前目标文件名：优先返回 `edited_name`，否则返回 `suggested_name`。
- 状态和说明。
- 更新时间。

### 8.3 编辑预览项

```text
PUT /api/rename-previews/{preview_id}
```

请求字段：

- `target_name`

规则：

- 只允许编辑文件名，不允许包含目录路径。
- 文件名不能为空。
- 必须保留扩展名；如果用户未输入扩展名，后端自动补回原扩展名。
- 编辑后状态改为 `edited`。

## 9. 前端页面

### 9.1 菜单

在左侧菜单新增“命名预览”，位置放在“扫描结果”之后。

### 9.2 页面布局

页面右侧操作台新增命名预览页，包含：

- 顶部工具区：生成预览按钮、媒体源筛选、扫描任务筛选、状态筛选、类型筛选。
- 统计区：总数、可直接使用数量、需检查数量、已编辑数量。
- 预览表格：原文件名、类型、解析标题、年份、季集、目标文件名、状态、操作。
- 编辑弹窗或抽屉：修改目标文件名。

表格在亮色和暗色主题下都必须可读，遵循 M1 的全局主题变量。

### 9.3 操作规则

- 用户点击“生成预览”后，前端调用生成接口并刷新列表。
- 状态为 `needs_review` 的行要明显标识，但不阻断列表展示。
- 用户编辑目标文件名后，列表立即显示编辑后的值。
- 页面不提供“执行重命名”按钮，避免越过 M2 范围。

## 10. 测试与验收

后端测试：

- 文件名清洗测试。
- 电影解析测试。
- 剧集解析测试。
- 非法字符清理测试。
- 命名模板生成测试。
- 预览生成不会修改真实文件的测试。
- 编辑预览项测试。
- 查询筛选测试。

前端测试：

- API client 新增接口测试。
- 命名预览 store 或页面数据转换测试。
- 主题下表格字段和状态文案稳定显示。

验收标准：

- 对 M1 扫描出的混乱文件名，可以生成可编辑的新文件名。
- 预览阶段不修改任何真实文件。
- 默认命名符合主流刮削器识别习惯：电影 `{title}.{year}`，剧集 `{title}.S01E02`。
- 用户可以手动编辑预览项，并保存到数据库。
- 生成预览和编辑预览都有可追踪日志或工作记录。

## 11. 文档与工作日志

M2 开始和完成记录继续写入：

```text
docs/work-logs/
```

M2 完成后生成：

- `docs/design/M2-design-manual.md`
- `docs/manuals/M2-user-manual-cn.md`
- `docs/manuals/M2-user-manual.md`
