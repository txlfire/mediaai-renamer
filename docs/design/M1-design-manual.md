# MediaAI Renamer M1 设计手册

版本：v0.1.0 预览版  
阶段：M1 本地目录扫描与页面框架  
日期：2026-06-24

## 1. 阶段结论

M1 功能范围已完成。

本阶段已经实现媒体源配置、本地或已挂载目录扫描、视频文件识别、扫描任务记录、扫描结果保存、操作日志查看、前端主框架、亮暗主题切换、侧栏展开收缩和默认端口调整。

已通过本地后端测试、前端 API 测试、主题状态测试和前端构建验证。Docker Compose 配置已更新，但仍需要在具备 Docker 的目标环境中执行部署启动验证。

## 2. M1 范围

### 2.1 已包含

- 媒体源新增和列表查看。
- 媒体源路径基础校验。
- 本地或已挂载目录全量扫描。
- 常见视频扩展名识别。
- 分批扫描，默认每批 100 个文件。
- 批间隔，默认 1 秒。
- 扫描任务状态和统计记录。
- 扫描结果保存到 SQLite。
- 操作日志弹出查看和 TXT 导出。
- 左侧可收缩侧栏和右侧操作台布局。
- 页面亮色、暗色和系统主题解析能力。
- 左下状态区、语言占位框、版本号、主题按钮和退出入口。
- 右上全局搜索入口占位。
- 后端默认端口 `8970`。
- Web 默认端口 `8971`。
- 工作日志统一存放到 `docs/work-logs/`。

### 2.2 未包含

- TMDB、Bangumi、TVDB、豆瓣等外部元数据匹配。
- DeepSeek 或其他 AI 解析。
- 标准命名预览。
- 真实文件重命名。
- 增量扫描。
- 用户登录、权限和会话管理。
- 全局搜索实际检索逻辑。
- 语言切换实际逻辑。

上述内容进入后续 M2 及以后阶段。

## 3. 系统结构

### 3.1 后端

后端使用 Python、FastAPI 和 SQLite。

主要模块：

- `backend/app/core/config.py`：应用配置、日志配置、扫描配置。
- `backend/app/core/database.py`：数据库初始化和 M1 表结构创建。
- `backend/app/api/media_sources.py`：媒体源 API。
- `backend/app/api/scan_jobs.py`：扫描任务和扫描结果 API。
- `backend/app/api/logs.py`：日志读取和导出 API。
- `backend/app/service/media_source_service.py`：媒体源业务逻辑。
- `backend/app/service/scan_service.py`：分批扫描、视频识别和结果保存。
- `backend/app/service/log_service.py`：日志读取和导出。
- `backend/app/utils/media_file.py`：视频文件扩展名识别。

### 3.2 前端

前端使用 Vue 3、TypeScript、Pinia、Element Plus 和 Vite。

主要模块：

- `frontend/src/App.vue`：应用主框架、侧栏、状态区、主题入口和操作台。
- `frontend/src/router/index.ts`：M1 页面路由。
- `frontend/src/stores/app.ts`：主题、侧栏和后端健康状态。
- `frontend/src/stores/media.ts`：媒体源、扫描任务、扫描结果和日志抽屉状态。
- `frontend/src/api/client.ts`：后端 API 客户端。
- `frontend/src/views/MediaSourcesView.vue`：媒体源页面。
- `frontend/src/views/ScanJobsView.vue`：扫描任务页面。
- `frontend/src/views/ScanResultsView.vue`：扫描结果页面。
- `frontend/src/components/LogDrawer.vue`：操作日志抽屉。
- `frontend/src/styles.css`：全局布局、主题变量、暗色适配和侧栏动画。

## 4. 数据设计

M1 新增三张核心表。

### 4.1 `media_sources`

保存媒体源目录。

关键字段：

- `id`
- `name`
- `path`
- `enabled`
- `created_at`
- `updated_at`

### 4.2 `scan_jobs`

保存扫描任务。

关键字段：

- `id`
- `media_source_id`
- `status`
- `batch_size`
- `batch_interval_seconds`
- `scanned_count`
- `video_count`
- `warning_count`
- `error_message`
- `started_at`
- `ended_at`
- `created_at`

### 4.3 `media_files`

保存扫描识别到的视频文件。

关键字段：

- `id`
- `media_source_id`
- `scan_job_id`
- `file_path`
- `file_name`
- `extension`
- `file_size`
- `modified_at`
- `created_at`

## 5. API 设计

所有 M1 API 使用 `/api` 前缀。

媒体源：

- `GET /api/media-sources`
- `POST /api/media-sources`

扫描：

- `POST /api/scan-jobs`
- `GET /api/scan-jobs`
- `GET /api/media-files`

日志：

- `GET /api/logs`
- `GET /api/logs/export`

健康检查：

- `GET /api/health`

## 6. 扫描设计

扫描入口为媒体源目录。

执行过程：

1. 校验媒体源存在且路径可访问。
2. 创建扫描任务记录。
3. 递归遍历媒体源目录。
4. 按配置批量处理文件。
5. 识别支持的视频扩展名。
6. 将视频文件写入 `media_files`。
7. 更新扫描任务统计和状态。

默认配置：

```toml
[scan]
batch_size = 100
batch_interval_seconds = 1
```

支持的视频扩展名：

`.mp4`、`.mkv`、`.avi`、`.mov`、`.wmv`、`.flv`、`.m4v`、`.ts`、`.m2ts`、`.rmvb`、`.mpg`、`.mpeg`、`.webm`

扩展名大小写不敏感。

## 7. 页面设计

### 7.1 左侧侧栏

展开状态显示：

- Logo
- 产品名称
- 菜单文字和图标
- 提示语
- 后端状态
- 语言占位框
- 版本号
- 主题切换按钮
- 退出入口和当前用户

收缩状态显示：

- Logo 简写
- 菜单图标
- 状态点
- 主题按钮
- 退出图标
- 小三角展开按钮

侧栏支持 220ms 展开和收缩动画，并支持系统减少动态效果设置。

### 7.2 右侧操作台

操作台按菜单切换页面：

- 媒体源
- 扫描任务
- 扫描结果

右上角保留全局搜索入口，M1 只提供界面占位。

### 7.3 操作日志

操作日志不作为常驻菜单页，而是在扫描任务页面通过抽屉查看。

支持：

- 查看日志列表。
- 刷新日志。
- 导出 TXT。

## 8. 主题设计

主题状态由前端 store 维护。

支持模式：

- `system`
- `light`
- `dark`

当前页面入口是一个明暗切换按钮。底层仍保留系统主题解析能力，便于后续扩展。

暗色主题覆盖范围：

- 应用背景。
- 侧栏。
- 页面头。
- 表单。
- 输入框。
- 表格标题行和数据行。
- 抽屉。
- 弹层。
- 普通按钮。

## 9. 端口和部署

默认端口：

- 后端 API：`8970`
- Web 入口：`8971`

开发模式：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
npm run frontend:dev
```

生产或 NAS 部署入口：

```text
http://localhost:8971
```

Docker Compose 配置已经更新，但需要在目标 Docker 环境执行：

```powershell
docker compose up --build
```

## 10. 验证记录

2026-06-24 已执行：

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest discover backend\tests -v
npm.cmd run frontend:test
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts
npm.cmd run frontend:build
```

结果：

- 后端 12 个测试通过。
- 前端 API 5 个测试通过。
- 主题状态 4 个测试通过。
- 前端构建通过。

已知提示：

- Vite 构建存在第三方库 `#__PURE__` 注释警告。
- Vite 构建存在包体积提示。
- 上述提示不影响 M1 功能。
- Docker Compose 尚未在目标 Docker 环境完成实机启动验证。

## 11. 后续建议

M2 建议进入标准命名和预览能力：

- 扫描结果筛选和搜索。
- 文件名解析规则。
- 标准命名预览。
- 预览结果确认流程。
- 为后续安全重命名做数据结构准备。
