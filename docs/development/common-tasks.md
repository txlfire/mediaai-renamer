# 常用脚本和任务

类型：命令手册。适用 v1.3.0；命令在项目根目录执行。环境与工程约束见[开发指南](development-guide.md)，安装更新见[部署说明](../deployment/fnos-ghcr-docker.md)。

## 1. 开发服务

Windows 默认只查状态：

```powershell
npm.cmd run dev:windows -- status
```

仅未运行时启动：

```powershell
npm.cmd run dev:windows -- start
```

显式需要改端口且目标端口空闲时：

```powershell
npm.cmd run dev:windows -- start -FrontendPort 5174 -BackendPort 8972
```

仅经明确停服/重启要求执行，不能当成每次验证的准备步骤：

```powershell
npm.cmd run dev:windows -- stop
# 需要重新启动已有实例时，选择 restart，而不是再创建一组进程。
npm.cmd run dev:windows -- restart
```

上面 stop 与 restart 是不同操作示例，不需要依次执行。Linux/macOS 对应：

```bash
npm run dev:linux -- status
# 仅服务未运行时：
npm run dev:linux -- start
```

Linux 自定义端口用 `FRONTEND_PORT=5174 BACKEND_PORT=8972 npm run dev:linux -- start`；stop/restart 同样需明确意图。Windows 的 `dev:start/dev:stop/dev:lan` 及 Linux 的旧启停别名保留兼容，新文档统一用带动作的管理入口。

预期：前端5173、后端8970及前端 `/api/health` 代理均可达；健康接口返回 `status=ok` 和实际版本。日志/PID 在 `.codex/run-logs/`。端口监听不等于服务健康，脚本报告 running 后仍核对：

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173 -TimeoutSec 5).StatusCode
Invoke-RestMethod http://127.0.0.1:8970/api/health -TimeoutSec 5
Invoke-RestMethod http://127.0.0.1:5173/api/health -TimeoutSec 5
```

未知进程占用时不强杀；检查依赖、端口归属、配置和当前日志。后台启动不弹可见 Python 窗口，前端使用 Node/Vite；不要以 Python HTTP 服务替代。

### 独立调试

仅在相应标准实例未运行、端口空闲时使用。后端前台调试：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
```

前端前台调试：

```powershell
.\node_modules\.bin\vite.cmd --config frontend/vite.config.ts --host 127.0.0.1 --port 5173 --strictPort
```

`npm.cmd run frontend:dev` 和 `frontend:dev:lan` 实际为后台启动脚本，不是上述前台方式。后端 `backend:dev:lan` 是前台 Uvicorn；只在可信局域网且确有需要时监听全部地址。

## 2. 安装与验证

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend/requirements.lock
npm ci
```

已有虚拟环境不重复创建。Linux 使用 `python3` 和 `.venv/bin/python`。维护依赖锁时才需要 uv：

```powershell
uv pip compile backend/requirements.txt --python-version 3.11 --universal --generate-hashes --no-annotate --output-file backend/requirements.lock
```

更新锁后验证完整回归，不把解析成功当运行兼容。常用验证：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```

Linux 后端入口为 `npm run backend:test:linux`。指定后端模块示例：

```powershell
.\.venv\Scripts\python.exe backend/run_tests.py test_background_jobs -v
```

测试入口创建临时实例；禁止直接用默认配置运行 unittest discovery。受控 WebDAV 环境与清理边界见[集成验收](../deployment/webdav.md#13-专用目录集成验收)。文档-only变更仅验证事实、链接、编码和敏感信息，不冒充重新执行发布测试。

## 3. Docker 管理

正式部署固定 `v1.3.0`；在 Compose 同级 `.env` 设置 `MEDIAAI_IMAGE_TAG=v1.3.0`，保留原有其他变量。以下命令不会备份数据、配置和凭据密钥，升级前先按部署手册完成备份。

```bash
docker compose -f docker-compose.ghcr.yml config --quiet
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

预期：一个 mediaai 服务，Web8971/兼容8970映射同一应用；健康版本1.3.0。脚本包装支持：

```bash
COMPOSE_FILE=docker-compose.ghcr.yml npm run dev:docker -- status
COMPOSE_FILE=docker-compose.ghcr.yml npm run dev:docker -- logs
```

源码镜像使用 `docker compose -f docker-compose.yml up -d --build`，不要与同名标准实例同时启动。真实媒体目录映射、证书、权限及升级回退见部署手册。

## 4. 打包与发布

只有获得发布授权、版本尚未发布、测试通过且文档已同步时，才按当前 `package.json` 打包：

```powershell
npm.cmd run release:package
```

Linux 对应 `npm run release:package:linux`。产物为 `releases/mediaai-renamer-v版本号.zip`，包含后端、`frontend-dist/`、示例配置，不含真实配置、数据库、密钥、日志和测试数据。

正式流程：确认新版本及一致性 → 按风险验证 → develop提交推送 → main合入 → 创建新版本固定标签 → 核对main/标签CI → 打包检查 → 创建GitHub Release并上传 → 验证远端下载哈希及镜像来源。不要在运行服务的检出中随意切分支；不移动现有标签，不覆盖已经发布的固定版本制品。

`release:publish` / `release:publish:linux` 是带上传副作用的打包入口，已有Release时脚本可能覆盖同名资产；不能用于普通文档同步或“刷新”已发布包。本轮稳定版已发布，不要复制命令重新发布v1.3.0。

CI工作流 `.github/workflows/docker-ghcr.yml` 在 main或v*标签推送后执行前后端验证、Compose检查、独立真实TLS测试和隔离容器健康/版本/SPA/认证/schema门禁，通过后才上传GHCR。main可变镜像更新不改变固定版本镜像，不以工作流开始代替最终成功。

只读检查发布状态：

```powershell
git status --short --branch
gh release view v1.3.0 --repo txlfire/mediaai-renamer
gh run list --repo txlfire/mediaai-renamer --limit 5
```

Release正文引用仓库文件时使用完整GitHub链接；仓库内Markdown保留相对链接。更新正文不重传资产、不重建固定镜像。完整制品信息和历史证据见[v1.3.0发布说明](../releases/v1.3.0.md)。
