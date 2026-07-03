# MediaAI Renamer

MediaAI Renamer 是一个面向 NAS、fnOS、Emby、Jellyfin、Plex、Kodi 等场景的影视文件扫描与安全重命名工具。项目目标是安全整理本地或服务端可访问目录中的媒体文件，并提供元数据匹配、命名规则和共享目录能力。

当前版本：`0.7.1`

最近正式发布版本：`v0.6.9`，发布页：[GitHub Releases](https://github.com/txlfire/mediaai-renamer/releases/tag/v0.6.9)

当前阶段：M7 增量扫描开发中。最近正式发布版本为 `v0.6.9`。

## 当前能力

已完成并可用的主要功能：

- 媒体源管理：新增、编辑、启用、停用、删除和批量删除媒体源。
- 路径类型：支持本地路径、Windows UNC / SMB、已挂载路径 / NFS 的媒体源类型展示和基础保存。
- 本地目录选择：在服务所在 Windows 主机上浏览盘符和子目录，并写回目标路径。
- 扫描任务：按媒体源发起分批扫描，支持单批数量、批间隔、递归扫描、跳过隐藏文件和最小文件阈值等配置。
- 扫描结果：按任务查看视频文件列表，支持分页、排序、详情和自然单位展示。
- 命名预览：基于扫描结果生成目标文件名，支持查询、分页、排序、编辑和状态统计。
- 安全重命名：支持多选、全部和单条重命名；执行前进行 dry-run、冲突检测、空名检测和二次确认。
- TMDB 元数据匹配：支持 V4 令牌优先、V3 API Key 降级、单条匹配、候选回填和匹配度展示。
- 待处理文件：低于最小扫描阈值的文件进入待处理列表，支持移除、清空和批量迁移。
- AI 智能解析：支持 DeepSeek / OpenAI-compatible 配置、连接测试、单条重命名预览 AI 解析、候选确认回填和待处理文件 AI 候选展示。
- 敏感词设置：支持默认影视风险词、自定义敏感词和外部提交拦截记录，命中规则时阻止 TMDB、IMDb、AI 等外部提交。
- 系统设置：支持刮削设置、AI 智能解析、文件扫描、敏感词设置、解析重命名、共享目录和通用设置，保存后无需重启即可读取最新配置。
- 操作日志：后端保留日志文件，前端提供日志查看入口。
- 页面框架：左侧可伸缩菜单、亮色/暗色主题、跟随系统主题、状态显示、语言占位和全局搜索框。
- 共享目录：支持本地路径、Windows UNC / SMB、已挂载路径 / NFS 三类媒体源，扫描和重命名前执行统一协议校验。
- SMB 凭据：复用媒体源自身加密字段保存，接口和日志脱敏，不建立全局凭据库。

M5 实机验收边界：

- SMB / UNC 访问依赖当前后端服务进程已有共享访问权限，系统不会自动执行 `net use`。
- NFS 依赖操作系统、NAS 或 Docker 宿主机预先挂载，系统不会自动执行 `mount`，也不保存 NFS 密码或 Kerberos 凭据。
- WebDAV、FTP、SFTP、S3 暂不支持真实扫描和重命名，仅作为后续候选协议。

M6 AI 使用边界：

- AI 解析必须由用户主动点击触发，扫描任务不会自动调用 AI。
- AI 候选只回填命名预览，不直接修改真实文件。
- 文件名、路径或解析标题命中敏感词时，不会提交 TMDB、IMDb、AI 或后续外部刮削源。

## 端口

- 后端 API：`http://localhost:8970`
- Docker Web：`http://localhost:8971`
- 本地开发前端：`http://127.0.0.1:5173`

前端通过同源 `/api` 访问后端。开发环境由 Vite 代理转发，Docker 环境由前端容器转发。

## 本地启动

建议使用一键局域网启动：

```powershell
npm run dev:start
```

停止服务：

```powershell
npm run dev:stop
```

Linux 环境：

```bash
npm run dev:start:linux
npm run dev:stop:linux
```

启动脚本会先检查 Node.js、npm、`.venv`、前端依赖、后端依赖和关键入口文件；依赖不满足时会直接停止并给出安装提示。启动后脚本会后台启动前后端，并将日志和 PID 写入 `.codex/run-logs/`。如果当前 PowerShell 环境中同时存在 `Path` 和 `PATH`，脚本会在当前进程内临时规整，避免 Windows 后台启动时报环境变量重复键错误。

手动启动后端：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
```

手动启动前端：

```powershell
npm install
npm run frontend:dev
```

启动后访问：

```text
http://127.0.0.1:5173
```

后端健康检查：

```text
GET http://127.0.0.1:8970/api/health
```

## Docker 启动

源码构建启动：

```powershell
docker compose up --build
```

使用 GHCR 镜像启动：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

默认访问地址：

```text
Web: http://localhost:8971
API: http://localhost:8970
```

fnOS / NAS 部署建议使用 GHCR 镜像方式，详见 [docs/deployment/fnos-ghcr-docker.md](docs/deployment/fnos-ghcr-docker.md)。

## 配置文件

配置示例位于 [config/config.example.toml](config/config.example.toml)。

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
minimum_file_size = 0
```

## 常用命令

后端测试：

```powershell
npm run backend:test
```

前端测试：

```powershell
npm run frontend:test
```

前端构建：

```powershell
npm run frontend:build
```

编码检查：

```powershell
npm run check:encoding
```

打包发布：

```powershell
npm run release:package
```

发布到 GitHub Releases：

```powershell
npm run release:publish
```

## 文档索引

- 工作日志：[docs/work-logs/](docs/work-logs/)
- 系统设计文档：[docs/design/project-design.md](docs/design/project-design.md)
- 总设计文档：[docs/design/MediaAI-Renamer-总设计文档.md](docs/design/MediaAI-Renamer-总设计文档.md)
- 初版开发方案：[docs/design/MediaAI Renamer 智能影视重命名工具初版开发方案.md](docs/design/MediaAI%20Renamer%20智能影视重命名工具初版开发方案.md)
- 多语言技术选型评估：[docs/design/MediaAI Renamer 多语言技术选型评估报告.md](docs/design/MediaAI%20Renamer%20多语言技术选型评估报告.md)
- 开发规范：[docs/development/development-guide.md](docs/development/development-guide.md)
- Agent 开发规范：[docs/development/agent.md](docs/development/agent.md)
- fnOS Docker 镜像部署：[docs/deployment/fnos-ghcr-docker.md](docs/deployment/fnos-ghcr-docker.md)
- 初版开发里程碑计划：[docs/development/MediaAI Renamer 初版开发里程碑计划.md](docs/development/MediaAI%20Renamer%20初版开发里程碑计划.md)
- 常用脚本和任务：[docs/development/common-tasks.md](docs/development/common-tasks.md)
- 当前完整用户手册：[docs/manuals/MediaAI-Renamer-用户手册.md](docs/manuals/MediaAI-Renamer-用户手册.md)
- M5 开发计划：[docs/development/m5/M5-NAS-SMB共享目录开发计划.md](docs/development/m5/M5-NAS-SMB共享目录开发计划.md)
- M5 验收清单：[docs/development/m5/M5-NAS-SMB共享目录验收清单.md](docs/development/m5/M5-NAS-SMB共享目录验收清单.md)
- M5 设计手册：[docs/design/M5-design-manual.md](docs/design/M5-design-manual.md)
- M5 用户手册：[docs/manuals/M5-user-manual-cn.md](docs/manuals/M5-user-manual-cn.md)
- M5 部署说明：[docs/development/m5/M5-NAS-SMB-NFS部署说明.md](docs/development/m5/M5-NAS-SMB-NFS部署说明.md)
- M5 实机验收备忘：[docs/development/m5/M5-实机验收备忘.md](docs/development/m5/M5-实机验收备忘.md)
- M6 开发计划：[docs/development/m6/M6-AI智能解析开发计划.md](docs/development/m6/M6-AI智能解析开发计划.md)
- M6 验收清单：[docs/development/m6/M6-AI智能解析验收清单.md](docs/development/m6/M6-AI智能解析验收清单.md)
- M6 设计手册：[docs/design/M6-design-manual.md](docs/design/M6-design-manual.md)
- M6 用户手册：[docs/manuals/M6-user-manual-cn.md](docs/manuals/M6-user-manual-cn.md)
- M7 开发计划：[docs/development/m7/M7-增量扫描开发计划.md](docs/development/m7/M7-增量扫描开发计划.md)
- M7 验收清单：[docs/development/m7/M7-增量扫描验收清单.md](docs/development/m7/M7-增量扫描验收清单.md)
- M7 设计手册：[docs/design/M7-design-manual.md](docs/design/M7-design-manual.md)

## Git 分支

- `main`：正式发布分支。
- `develop`：日常开发分支。

发布时从 `develop` 合并到 `main`，按版本号打 tag；后续功能开发继续在 `develop` 上进行。
