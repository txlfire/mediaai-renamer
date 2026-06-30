# MediaAI Renamer 开发文档

## 1. 环境要求

后端：

- Python 3.11+
- FastAPI
- Uvicorn

前端：

- Node.js
- npm
- Vue 3
- Vite
- TypeScript
- Element Plus
- Axios

部署：

- Docker
- Docker Compose

## 2. 本地启动

后端启动：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
```

前端启动：

```powershell
npm install
npm run frontend:dev
```

局域网可访问的一键启动：

```powershell
npm run dev:lan
```

该命令会后台启动前后端，并将日志写入 `.codex/run-logs/`。如果当前 PowerShell 环境中同时存在 `Path` 和 `PATH`，脚本会在当前进程内临时规整，避免 Windows 后台启动报重复键错误。

访问地址：

```text
http://127.0.0.1:5173
```

后端健康检查：

```text
GET http://127.0.0.1:8970/api/health
```

## 3. Docker 启动

源码构建启动：

```powershell
docker compose up --build
```

镜像方式启动：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

默认地址：

```text
Web: http://localhost:8971
API: http://localhost:8970
```

fnOS 推荐使用镜像方式部署，避免在 NAS 上执行前后端构建。详细步骤见 `docs/deployment/fnos-ghcr-docker.md`。

## 4. 测试命令

后端测试：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
```

前端测试：

```powershell
npm run frontend:test
```

前端构建：

```powershell
npm run frontend:build
```

安全检查：

```powershell
npm audit --audit-level=moderate
```

## 5. 编码规范

### 5.1 编码和换行

- 所有源码、配置和 Markdown 文档统一使用 UTF-8 no BOM。
- 除 `.bat`、`.cmd` 外，文本文件统一使用 LF 换行。
- 新增或修改文件前优先使用 `apply_patch`；如需脚本批量处理文本，必须显式使用 UTF-8 no BOM 写入。
- Windows PowerShell 5 的 `Set-Content -Encoding UTF8` 会写入 BOM，除非明确需要，否则不要用于源码和文档。
- 提交前执行：

```powershell
npm run check:encoding
git diff --check
```

### 5.2 前端国际化

- 用户可见文本统一维护在 `frontend/src/locales/zh-CN.ts`。
- Vue 页面、Pinia store、API client 中不得直接硬编码中文提示、按钮、标题、表头和空状态文案。
- 测试用例和内部字符串判断可以保留必要中文样例，但不得作为页面展示文案来源。
- 新增页面或功能前先检查 `zh-CN.ts` 是否已有可复用文案，避免重复提取。
- 后续增加其他语言时，以 `zh-CN.ts` 的 key 结构作为基准，不在页面中新增临时文本。

## 5. M1 API

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

## 6. 扫描配置

配置示例：

```toml
[scan]
batch_size = 100
batch_interval_seconds = 1
```

扫描采用分批执行，默认每批 100 个文件，每批之间间隔 1 秒。

## 7. 编码规范

通用要求：

- 新功能按里程碑范围实现，不提前扩张。
- 真实文件操作必须可预览、可确认、可记录。
- 敏感信息不得写入代码、日志和提交记录。
- 关键逻辑必须有测试。

后端要求：

- API 层只做请求接收和返回。
- 核心业务放在 service。
- 请求和响应模型放在 schema。
- 文件名清洗、路径处理等通用逻辑放在 utils。
- 日志统一使用 `app.core.logger`，不得使用 `print()` 或重复调用 `logging.basicConfig()`。

前端要求：

- 接口请求统一放在 `frontend/src/api/`。
- 使用 Axios 统一实例，基础路径为 `/api`。
- 页面不得硬编码后端主机和端口。
- 状态放在 Pinia store。
- 表单使用 Element Plus 校验。

## 8. 注释要求

注释使用中文，命名保持英文。

硬性要求：

- 顶层公共注释使用中文，包括模块说明、类说明、公共函数和公共方法说明。
- 行内注释使用中文。
- 变量名、函数名、类名、文件名仍使用英文命名，保持代码国际化和生态兼容。

必须添加注释：

- 关键文件顶部说明职责。
- 公共函数和公共方法说明用途、参数、返回值和异常。
- 文件重命名、路径校验、AI 调用、共享挂载、数据库写入等高风险逻辑。
- 插件接口和抽象基类。

不应添加注释：

- 简单赋值。
- 重复代码字面含义的说明。
- 已经过期或与代码不一致的说明。

## 9. 日志标准

日志配置位于后端 `LoggingSettings`，后续接入 `config.toml` 后由配置文件控制。

标准日志文件：

- `app.log`：应用启动、配置加载、普通业务流程。
- `error.log`：ERROR 及以上错误。
- `llm.log`：AI 模型、token 数、耗时、费用估算等统计信息，不记录 prompt 原文。
- `batch.log`：扫描、预览、重命名等批量任务历史。

日志级别：

- `DEBUG`：开发调试信息，不记录敏感内容。
- `INFO`：关键业务节点和正常状态变化。
- `WARNING`：可恢复异常或降级处理。
- `ERROR`：单项功能失败，但服务仍可继续运行。
- `CRITICAL`：配置缺失、数据库不可用等导致任务或服务无法继续的严重错误。

敏感信息规则：

- 不记录 API Key、密码、完整 token、共享目录凭据。
- LLM 日志可以记录 token 数、模型名、耗时和费用估算。
- 批量重命名日志可以记录原路径和目标路径，但后续如果路径中包含账号或凭据必须脱敏。

Docker 部署时，`./logs` 会挂载到后端容器的 `/app/logs`，便于 NAS 页面查看和导出。

## 10. 提交前检查

提交前至少执行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
npm run frontend:test
npm run frontend:build
npm audit --audit-level=moderate
```

如果 Docker 相关文件有变化，还需要在具备 Docker 的环境中执行：

```powershell
docker compose up --build
```

如果变更 Docker 镜像发布流程，还需要检查：

```powershell
git status --short --branch
```

并确认 `.github/workflows/docker-ghcr.yml` 能在推送 `main` 或 `v*` 标签后自动发布 GHCR 镜像。

## 11. 文档更新

每次完成阶段性开发后，必须更新：

- `docs/design/` 中的设计文档。
- `docs/development/` 中的开发文档。
- `docs/work-logs/` 中的工作日志、阶段复盘和验证记录。
- `docs/manuals/` 中的当前阶段用户手册和总用户手册。
- README 中的启动和验证说明。

阶段收尾时必须执行设计一致性检查：

- 对照总设计文档、当前阶段设计文档、开发计划和验收清单，确认实际实现是否存在偏差。
- 如果实现与设计不一致，必须先补全文档，再进入下一阶段。
- 偏差修正内容必须记录到 `docs/work-logs/` 下对应日期或阶段的工作日志中，并在收尾汇报中明确告知。
- 如果确认无偏差，也需要在工作日志中记录本次比对结论。

阶段用户手册汇总要求：

- 每个阶段完成后，必须先更新对应阶段用户手册，再同步更新 `docs/manuals/MediaAI-Renamer-用户手册.md`。
- 总用户手册只记录已完成、可使用的功能，不写入未落地能力的操作步骤。
- 如果阶段实现改变了既有用户流程，必须同步修正总用户手册中的相关章节。
- 用户手册更新内容必须记录到 `docs/work-logs/` 下对应日期或阶段的工作日志中。

API 稳定后，新增接口文档放入 `docs/api/`。

## 12. Project Local Skill Usage

This project now splits repeatable Codex workflows into project-local skills under `.codex/skills/`. These skills are first used by this project and can later be promoted to global `~/.codex/skills/` for similar projects.

- `mediaai-docs-lifecycle`: requirement analysis, requirement design, requirement split, requirement supplementation, design docs, user manuals, acceptance checklists, acceptance reports, README, work logs, and milestone archive.
- `mediaai-frontend-ui`: Vue pages, Element Plus controls, Pinia stores, frontend API calls, tables, dialogs, theme behavior, sidebar behavior, i18n, and UI polish.
- `mediaai-backend-api`: FastAPI routes, service logic, SQLite schema migration, shared protocols, scan, rename, TMDB, settings, encryption, validation, and backend tests.
- `mediaai-test-release`: testing, builds, packaging, version bumps, Git flow, GitHub Release, release notes, and milestone release verification.

Rules:

- Before starting a matching task type, read the matching skill's `SKILL.md` first.
- Keep reusable workflow, paths, and commands in each skill's `references/`; do not store temporary daily notes there.
- When real development reveals a workflow gap, update the matching skill and record the change in `docs/work-logs/`.
- At milestone closeout, use `mediaai-docs-lifecycle` for document consistency and `mediaai-test-release` for validation and release steps.
