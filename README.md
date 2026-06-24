# MediaAI Renamer

MediaAI Renamer 是一个面向 NAS、fnOS、Emby、Jellyfin、Plex、Kodi 等场景的影视文件扫描与重命名工具。项目目标是先安全整理本地或已挂载目录中的媒体文件，再逐步接入智能解析、命名规则和批量重命名能力。

当前版本：`0.2.0`  
当前开发阶段：M3 安全重命名流程开发中

## 当前能力

已完成：

- 媒体源管理：保存媒体源名称、路径、启用状态和备注。
- Windows 本地目录选择：在媒体源弹窗内浏览盘符和子目录，并写回目标路径。
- 分批扫描：按配置分批扫描媒体源目录，默认每批 100 个文件、批间隔 1 秒。
- 扫描任务：记录扫描任务状态、开始时间、结束时间、扫描数量和错误信息。
- 扫描结果：识别常见视频扩展名，保存文件路径、文件名、大小和更新时间。
- 命名预览：根据扫描结果生成目标文件名，支持编辑、状态筛选、排序、分页和统计。
- 列表规范：默认 10 条/页，支持 50 条/页和展示全部；长文本截断后通过悬浮气泡展示完整内容。
- 查询策略：除媒体源页外，扫描任务、扫描结果和命名预览页只在用户手动查询或带参跳转时加载数据。
- 安全重命名 M3 初版：支持 dry-run 冲突检测、冲突结果弹窗、确认重命名入口和执行结果记录。
- 操作日志：后端保留日志文件，前端提供日志查看入口。
- 页面框架：左侧可伸缩菜单、亮色/暗色主题、跟随系统主题、状态显示、语言选择占位和全局搜索框。

开发中或后续规划：

- DeepSeek / AI 模型接入。
- 更完整的影视元数据识别和匹配。
- 用户权限与登录退出流程。
- 设置页中配置扫描批大小、列表展示长度等参数。
- 更完整的重命名历史、回滚和异常处理。

## 默认端口

- 后端 API：`http://localhost:8970`
- Docker 前端 Web：`http://localhost:8971`
- 本地开发前端：`http://127.0.0.1:5173`

前端通过同源 `/api` 访问后端。开发环境由 Vite 代理转发，Docker 环境由前端容器转发。

## 本地开发启动

后端：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
```

前端：

```powershell
npm install
npm run frontend:dev
```

访问：

```text
http://127.0.0.1:5173
```

## Docker 启动

```powershell
docker compose up --build
```

默认访问：

```text
Web: http://localhost:8971
API: http://localhost:8970
```

## 配置文件

示例配置位于 [config/config.example.toml](config/config.example.toml)。

```toml
[server]
host = "0.0.0.0"
port = 8970

[paths]
data_dir = "/app/data"

[logging]
level = "INFO"
log_dir = "/app/logs"
max_size_mb = 10
backup_count = 5
console_output = true

[scan]
batch_size = 100
batch_interval_seconds = 1
```

扫描配置说明：

- `batch_size`：每批扫描文件数量，默认 `100`。
- `batch_interval_seconds`：批次之间的间隔秒数，默认 `1`。

## 验证命令

后端测试：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
```

前端重点测试：

```powershell
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts frontend/src/stores/pagination.test.ts frontend/src/stores/tableSort.test.ts frontend/src/stores/preview.test.ts frontend/src/stores/renameOperation.test.ts frontend/src/utils/displayFormat.test.ts frontend/src/utils/localDirectory.test.ts frontend/src/api/client.test.ts
```

前端构建：

```powershell
npm run frontend:build
```

说明：当前构建可能出现 Vite chunk size 提示，这是包体积提示，不影响构建结果。

## 文档索引

- 工作日志：[docs/work-logs/](docs/work-logs/)
- M1 设计手册：[docs/design/](docs/design/)
- 用户手册：[docs/manuals/](docs/manuals/)
- M3 设计规格：[docs/superpowers/specs/2026-06-24-m3-safe-rename-design.md](docs/superpowers/specs/2026-06-24-m3-safe-rename-design.md)
- M3 实现计划：[docs/superpowers/plans/2026-06-24-m3-safe-rename-implementation.md](docs/superpowers/plans/2026-06-24-m3-safe-rename-implementation.md)

## Git 分支约定

- `main`：正式发布分支。
- `develop`：日常开发分支。

发布时从 `develop` 合并到 `main`，并按版本打 tag；后续功能开发继续在 `develop` 上进行。
