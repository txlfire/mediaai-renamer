# MediaAI Renamer

MediaAI Renamer 是一套面向 NAS 和家庭媒体库的影视文件扫描、元数据匹配与安全重命名工具。它将媒体源管理、增量扫描、命名预览、外部元数据补充、真实重命名、回滚和审计集中在一个 Web 界面中。

[![Release](https://img.shields.io/badge/release-v1.0.1-2f80ed)](https://github.com/txlfire/mediaai-renamer/releases/tag/v1.0.1)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3-42b883)](https://vuejs.org/)
[![License](https://img.shields.io/badge/license-MIT-4c566a)](LICENSE)

当前稳定版本为 `v1.0.1`。项目已进入稳定维护阶段，后续以问题修复、兼容性和使用体验优化为主。

## 核心能力

### 媒体源与扫描

- 管理本地路径、Windows UNC / SMB、已挂载 NFS 路径和 HTTPS WebDAV 媒体源。
- 支持全量扫描和增量扫描，记录新增、修改、跳过、缺失和失败结果。
- 支持递归扫描、隐藏文件过滤、视频扩展名识别和最小文件大小阈值。
- 低于阈值或暂不处理的文件进入待处理列表，可迁移、排除或重新处理。
- 扫描任务、扫描结果和失败原因均可在 Web 页面查询。

### 识别与元数据

- 从原文件名、解析标题或上级文件夹名称识别电影和剧集标题。
- 支持 TMDB 搜索、详情读取、候选选择、字段回填和匹配度展示。
- 支持 IMDb 元数据补充，以及 Bangumi、TVDB 和用户自备豆瓣代理等补充源。
- 支持 DeepSeek、OpenAI-compatible 和自定义兼容 Provider，可切换当前模型。
- 支持单条与批量 AI 解析，以及 TMDB 未命中后转 AI 的后备匹配流程。
- 外部提交前执行敏感词规则检查，命中时阻止向刮削站点或 AI 服务提交。

### 命名与文件安全

- 电影和剧集使用独立命名模板，支持元素组合、格式设置、预设、导入导出和规则测试。
- 预览阶段展示目标文件名、状态、冲突、元数据来源和匹配结果，支持手动编辑与排除。
- 真实重命名前执行 dry-run、目标冲突、空文件名和文件可访问性检查。
- 支持单条、选中项和当前列表批量重命名，并锁定运行中的重复操作。
- 成功批次可生成回滚计划；WebDAV 支持反向 MOVE、失败恢复和幂等保护。

### 管理与审计

- 提供本地用户、直接权限、管理员初始化和默认密码修改提醒。
- 支持浏览器密码保存，以及由管理员配置有效期的“一周免登录”会话。
- 记录登录、配置、媒体源、外部提交、重命名和回滚等审计事件。
- 统一查看扫描任务、重命名批次、回滚计划和操作日志，并支持归档与恢复。
- 系统设置覆盖刮削、补充元数据源、AI、扫描、敏感词、命名规则、共享目录和通用配置。

## 支持范围

| 类型 | 当前支持 | 使用边界 |
| --- | --- | --- |
| 本地路径 | 扫描、预览、重命名、回滚 | 路径必须对应用进程或容器可见 |
| Windows UNC / SMB | 连接检查、扫描、预览、重命名、回滚 | 依赖运行进程已有共享访问权限；系统不会自动执行 `net use` |
| 已挂载路径 / NFS | 扫描、预览、重命名、回滚 | 需先由操作系统或容器宿主机完成挂载 |
| HTTPS WebDAV | 无认证、Basic、Bearer、浏览、扫描、MOVE、回滚、恢复 | 仅支持 HTTPS；不支持 Digest 或跳过 TLS 校验 |

FTP、FTPS、SFTP、S3 / MinIO 和 HTTP WebDAV 当前未实现，不应按可用功能部署。

## 技术架构

- 前端：Vue 3、TypeScript、Element Plus、Pinia、Vite。
- 后端：Python、FastAPI、SQLite。
- 正式镜像：一个 FastAPI 进程同时提供 Vue SPA 和 `/api`。
- 持久化目录：`data/`、`logs/`、`config/`。

正式 Docker 发布只运行一个 `mediaai` 服务和一个容器。`8971` 是 Web 主入口，`8970` 是兼容入口，两者映射到同一应用进程。

## Docker 快速部署

推荐使用 GHCR 中的稳定镜像：

```bash
git clone https://github.com/txlfire/mediaai-renamer.git
cd mediaai-renamer
git checkout v1.0.1

cp config/config.example.toml config/config.toml
printf "MEDIAAI_IMAGE_TAG=v1.0.1\n" > .env

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
```

访问地址：

```text
Web: http://<NAS_IP>:8971
健康检查: http://<NAS_IP>:8971/api/health
兼容入口: http://<NAS_IP>:8970
```

首次打开登录页时初始化管理员。除非用于受控的无人值守部署，否则不要启用示例配置中的默认管理员自动创建；如需启用，必须先修改示例口令。

容器只能访问已映射的宿主机目录。请在 Compose 的 `volumes` 中添加媒体目录映射，并在系统中填写容器内路径：

```yaml
services:
  mediaai:
    volumes:
      - /path/to/media:/app/media
```

完整的 fnOS、持久化目录、版本更新和旧双容器迁移步骤见 [fnOS Docker 部署说明](docs/deployment/fnos-ghcr-docker.md)。

## 配置

项目只跟踪 [config/config.example.toml](config/config.example.toml)。首次运行前复制为正式配置：

```bash
cp config/config.example.toml config/config.toml
```

Windows PowerShell：

```powershell
Copy-Item config/config.example.toml config/config.toml
```

`config/config.toml`、数据库、日志、媒体路径、密码、Token 和 API Key 均不应提交到 Git。生产环境建议同时限制配置文件权限，并定期备份 `data/`。

## 本地开发

环境要求：

- Python 3.11 或更高版本。
- Node.js 20 或更高版本。
- npm。

Windows 后台启动与停止：

```powershell
npm install
npm run dev:start
npm run dev:stop
```

Linux 后台启动与停止：

```bash
npm install
npm run dev:start:linux
npm run dev:stop:linux
```

开发环境默认地址：

```text
前端: http://127.0.0.1:5173
后端: http://127.0.0.1:8970
健康检查: http://127.0.0.1:8970/api/health
```

常用验证命令：

```powershell
npm run backend:test
npm run frontend:test
npm run frontend:build
npm run check:encoding
```

## 安全说明

- 真实重命名前应先检查预览和 dry-run 结果，并备份重要媒体目录。
- 外部元数据和 AI 服务由用户主动配置、测试和触发；敏感词保护不会替代人工判断。
- 密码、Token 和 API Key 在界面、接口响应、运行日志及审计详情中按敏感字段处理。
- 不要在 Issue、日志附件、截图或配置示例中公开真实密钥、账号、内网地址和媒体路径。

## 文档

- [完整用户手册](docs/manuals/MediaAI-Renamer-用户手册.md)
- [fnOS Docker 部署说明](docs/deployment/fnos-ghcr-docker.md)
- [WebDAV 部署说明](docs/deployment/webdav.md)
- [开发指南](docs/development/development-guide.md)
- [常用命令与脚本](docs/development/common-tasks.md)
- [系统设计](docs/design/project-design.md)
- [v1.0.1 发布说明](docs/releases/v1.0.1.md)
- [历史文档归档](docs/archive/README.md)

## 发布与分支

- `main`：正式发布分支。
- `develop`：日常维护与开发分支。
- 正式版本与统一发布包见 [GitHub Releases](https://github.com/txlfire/mediaai-renamer/releases)。
- Docker 镜像：`ghcr.io/txlfire/mediaai-renamer:<tag>`。

## License

本项目使用 [MIT License](LICENSE)。
