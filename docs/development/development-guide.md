# MediaAI Renamer 开发指南

类型：开发与操作手册。适用 v1.3.0、schema39；[当前状态与剩余边界](project-status.md)，[全部文档](../README.md)。

## 1. 环境与依赖

- 后端使用 Python、FastAPI、SQLite；Python 3.11 是锁文件和发布验证基线。新 Python 版本需验证依赖及完整回归，不以安装成功代替兼容验收。
- 前端使用 Vue 3、TypeScript、Element Plus、Pinia、Axios、Vite；Node 要求 20.19+ 或 22.12+，本次发布本机使用 24.15.0。Node 16 不可用。
- `backend/requirements.txt` 是直接依赖输入，`backend/requirements.lock` 是传递依赖与下载哈希锁；前端使用根目录 `package-lock.json` 和 `npm ci`。
- Docker 仅用于镜像构建/部署验证，不是本地源码启动的必需条件。

首次安装，在项目根目录执行；已有虚拟环境不重复创建：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend/requirements.lock
npm ci
```

Linux 使用 `python3 -m venv .venv` 和 `.venv/bin/python`。镜像源缺包时可仅为本次 pip 命令增加 `--index-url https://pypi.org/simple`，不修改全局配置。更新依赖后必须重建锁并验证，不能绕过哈希安装。

实例配置不入 Git。Docker 示例中的 `/app/data`、`/app/logs` 是容器路径；本地自建配置应调整为实际实例路径，不盲目覆盖已有 `config/config.toml`。`MEDIAAI_CONFIG_PATH` 可指定隔离配置；执行前确认文件存在，避免回退到默认实例。数据库与凭据密钥成对备份、分开保管。

## 2. 规范启动

Windows 先检查状态：

```powershell
npm.cmd run dev:windows -- status
```

仅未运行时启动：

```powershell
npm.cmd run dev:windows -- start
```

Linux 对应 `npm run dev:linux -- status`，仅停止时 `npm run dev:linux -- start`。`start` 会替换脚本管理的旧实例，不是无副作用的健康查询。用户要求保持运行时，不执行 stop/restart，不因测试或文档工作重启服务。

| 检查 | 预期结果 |
| --- | --- |
| `http://127.0.0.1:5173` | Vue 页面可访问 |
| `http://127.0.0.1:8970/api/health` | 200，`status=ok`，版本与当前源码一致 |
| `http://127.0.0.1:5173/api/health` | 200，Vite 代理可达同一后端 |

脚本的 running 提示包含端口探测，不替代上述健康检查。默认后台启动前后端，日志/PID 在 `.codex/run-logs/`；前端直接使用 Node/Vite，不用 Python 静态服务器替代，不另开可见窗口。`frontend:dev` 也是后台脚本，不是前台终端命令。

脚本仅管理本项目的可验证进程，未知进程占端口时先核对归属或选择其他端口，不强杀。日志中不应包含凭据，分享前仍需检查路径、账号等隐私。桌面宿主退出可能连带结束开发服务，脚本不是系统级守护服务；恢复后核对健康和任务意图，不重放未知操作。

需要明确停服/重启、改端口或单进程调试时见[命令目录](common-tasks.md)。局域网监听仅用于可信网络，不能当作互联网安全部署方案。

## 3. 隔离测试与构建

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```

- 后端命令通过 `backend/run_tests.py` 创建临时配置/数据/日志，避免模块导入初始化真实实例。**不要直接对默认环境执行 `python -m unittest discover`**。
- 指定测试使用 `.\.venv\Scripts\python.exe backend/run_tests.py test_background_jobs -v`，模块名须来自 `backend/tests/`；真实 WebDAV 集成默认跳过，只能在已授权专用目录启用。
- Linux 后端测试为 `npm run backend:test:linux`。前端单测覆盖组件、状态和客户端，不仅是 API；构建同时运行 `vue-tsc`。
- 文档变更检查事实、链接、编码、差异和敏感信息，不需要无差别重跑产品测试；引用发布阶段证据必须标明来源及日期。
- 涉及 Docker/Compose、协议或发布流程时，在具备条件的环境验证相应门禁；本机无 Docker 时明确记录，不能声称本机容器通过。CI 通过也不能代替真实 NAS/NFS 或正式实例升级验收。
- `npm audit --audit-level=moderate` 用于依赖风险核查；失败时核对具体依赖和影响，不未经评估自动升级。

## 4. 代码与接口规范

- API 接收与校验参数，`service/` 承载业务，`schema/` 定义契约，`core/` 管理配置、数据库、迁移和基础设施。SQLite 使用参数化 `sqlite3`，没有 ORM 映射。
- 前端请求集中在 `frontend/src/api/`，使用相对 `/api`，不硬编码主机；状态放 Pinia，表单沿用 Element Plus。
- 用户可见文案集中在 `frontend/src/locales/zh-CN.ts`；主题注册在 `themeRegistry.ts`，颜色值以 `styles.css` 为唯一来源，不引入第二套配色常量。
- 源码与 Markdown 使用 UTF-8 no BOM、LF（`.bat/.cmd` 例外）；手工修改优先 `apply_patch`，避免 PowerShell 5 写入 BOM。
- 注释使用中文，解释职责、风险、边界和异常，不堆砌赋值说明；标识符使用英文并遵循所属模块约定，不为命名风格改动公共契约。
- 异常要区分明确拒绝、已生效但返读失败、未知外发/文件结果；不能将所有异常写为“失败可重试”。日志包含可追踪关联信息，但不能记录密码、完整令牌、URL 内嵌凭据或完整 prompt。

当前主要 API 分域如下，详细字段以运行实例的 `/docs`、路由模型和专项手册为准；私有业务接口需要会话及相应直接权限：

| 分域 | 路径/约束 |
| --- | --- |
| 健康与登录 | `/api/health`、`/api/auth/*`；不是供应商认证令牌 |
| 媒体源与扫描 | `/api/media-sources`、`/api/scan-jobs/async`、`/api/media-files/page`；扫描受理返回202 |
| 预览与选择范围 | `/api/rename-previews/page`、`/api/rename-previews/selection`；全部操作不能仅处理当前页 |
| 文件执行 | `/api/rename-operations/{id}/execute-async`、`/api/rename-rollback-plans/{id}/execute-async`、`/api/pending-files/move-async`；确认、dry-run和执行前复核不可省略 |
| 探测与匹配任务 | `/api/background-jobs`；稳定UUID、202、摘要观察及终态结果，详见[接口定义与调用示例](background-jobs/api-and-maintenance.md) |
| 日志与治理 | `/api/logs`、`/api/operation-logs`、`/api/audit-events`、`/api/tasks`；各自保留策略不可混用 |
| 配置与用户 | `/api/settings`、`/api/users`；权限直接授予用户，不引入角色 |

## 5. 外部服务与文件安全

- 本地解析优先；匹配只产生或回填候选，不由 AI 执行文件动作。外发必须复用权限、确认和敏感词保护；未知结果先查询/核查，不自动重试。
- `[external_requests]` 控制向配置的 AI/元数据站点发出的实际 HTTP 请求，不是本地 API 限流。默认全局4并发、同源1并发和2次/秒，等待计入预算；相同 origin 共用槽，修改配置后重启全部相关 worker。
- 真实重命名、回滚和迁移共用写任务 FIFO，动作前再次检查当前权限、配置、快照和源身份；槽位释放须证明进程退出。
- HTTPS WebDAV 严格验证证书/主机名，拒绝自动跳转；端点条件 MOVE 验证未通过时保持只读，不降级 HTTP、忽略 TLS 或省略强 ETag。
- 支持协议以[系统设计](../design/project-design.md)为准；未实现的 FTP/FTPS/SFTP/S3 不写成当前功能或下一阶段承诺。

## 6. 日志、清理与维护

运行日志通过 `app.core.logger`，不重复配置 `basicConfig()`，不使用 `print()` 写业务日志。`config.toml` 的 `[logging]` 控制基础目录/级别/轮转，系统热设置控制运行维护策略；`app.log/error.log/llm.log/batch.log` 按职责分流。`llm.log` 只记录允许的模型、用量、耗时等摘要。

文件轮转/归档、数据库操作日志、后台结果、安全审计及审计辅助意图是不同对象：

- 文件日志与操作日志按系统设置的保留期、大小和有界维护执行；轮转/维护使用目录锁，变化中的文件保留到后续核对，不清理锁文件腾容量。
- 操作日志按游标增量查询，限制渲染和请求并发，关闭后停止额外观察；清理后提示或禁用查看，不伪装成无操作历史。
- 后台普通结果按 `[background_jobs]` 管理，默认单任务4MiB、总逻辑64MiB、7天；清理不删除幂等UUID、不重发请求，详见[维护手册](background-jobs/api-and-maintenance.md)。
- 安全审计不套用普通日志保留期；未知/待补写意图不为腾空间删除。逻辑删除不承诺 SQLite/WAL 物理缩小；未批准政策前不自动清审计或在线 VACUUM。

维护 CLI 默认只读；执行参数需要核对目标实例、停止服务及显式确认，不能拿生产配置跑合成测试。详细恢复/脱敏操作见[完整手册](../manuals/MediaAI-Renamer-用户手册.md)与[WebDAV 维护](../deployment/webdav.md)。

## 7. 文档、Skill 与提交

设计只写已确认目标；实施/演进记录差异和迁移；手册写可用操作；验收记录场景与证据。只同步受影响的权威文档，不覆盖历史报告。当前状态统一在 `project-status.md`，本机互审目录不作为公开入口。

本机可提供 `mediaai-docs-lifecycle`、`mediaai-frontend-ui`、`mediaai-backend-api`、`mediaai-test-release` 等项目 Skill；目录不随公开仓库分发，缺少 Skill 不妨碍按本指南开发。是否启用及流程强度遵循当前用户/代理指令，不因已安装而自动全部加载。

提交前检查实际 diff，保留用户已有改动，不提交配置、数据库、日志、密钥和本机证据。按变更范围验证，再在 `develop` 提交；需要同步公开文档时推进 `main`，确认远端并保留 CI 结果。纯文档更新不升版本、不移动既有标签、不覆盖同版包。完整发布操作见[命令目录](common-tasks.md)。
