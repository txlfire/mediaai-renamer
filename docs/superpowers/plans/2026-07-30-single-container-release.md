# MediaAI Renamer 单容器发布实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Docker 正式发布收敛为一个同时提供 Vue 页面和 FastAPI 接口的镜像与容器。

**架构：** Docker 多阶段构建先生成 Vue `dist`，再复制到 Python 运行镜像；FastAPI 在 API 路由之后挂载 SPA 静态服务。源码开发继续使用 Vite 与 Uvicorn 双进程，正式 Compose 和 GHCR 仅保留一个应用服务。

**技术栈：** Python 3.11、FastAPI、Starlette StaticFiles、Vue 3、Vite、Docker Buildx、Docker Compose、GitHub Actions、GHCR

---

## 文件结构

- 创建 `backend/app/web.py`：封装 SPA 静态资源服务、资源 404 和前端目录探测。
- 创建 `backend/tests/test_web.py`：验证首页、SPA 回退、API 优先级、资源 404 和无构建目录场景。
- 修改 `backend/app/main.py`：在所有 API 路由注册后挂载前端。
- 创建 `docker/Dockerfile`：统一前端构建和后端运行镜像。
- 删除 `docker/Dockerfile.backend`：移除旧后端独立镜像入口。
- 删除 `docker/Dockerfile.frontend`：移除旧前端独立镜像入口。
- 删除 `docker/nginx.conf`：静态资源和 API 不再需要 Nginx 反向代理。
- 修改 `docker-compose.yml`：源码构建改为单服务。
- 修改 `docker-compose.ghcr.yml`：GHCR 部署改为单镜像。
- 修改 `.github/workflows/docker-ghcr.yml`：取消矩阵构建，仅发布 `mediaai-renamer`。
- 修改 `.codex/skills/mediaai-test-release/SKILL.md`：更新单镜像发布约定。
- 修改 `.codex/skills/mediaai-test-release/references/release-context.md`：更新镜像名和验收入口。
- 修改 `docs/deployment/fnos-ghcr-docker.md`：更新 fnOS 安装、升级、日志与旧双容器迁移步骤。
- 修改 `docs/development/development-guide.md`：更新 Docker 运行结构，保留源码开发说明。
- 修改 `docs/development/common-tasks.md`：更新常用 Docker 命令和镜像名称。
- 修改 `scripts/dev-docker.sh`：更新 GHCR 单镜像示例版本表达。

### 任务 1：为 FastAPI 添加可测试的 SPA 静态托管

**文件：**
- 创建：`backend/app/web.py`
- 创建：`backend/tests/test_web.py`
- 修改：`backend/app/main.py`

- [ ] **步骤 1：编写失败的静态托管测试**

在 `backend/tests/test_web.py` 中创建临时目录和以下测试：

```python
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


class FrontendHostingTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.frontend_dir = Path(self.temp_dir.name)
        (self.frontend_dir / "assets").mkdir()
        (self.frontend_dir / "index.html").write_text(
            "<html><title>MediaAI Renamer</title></html>",
            encoding="utf-8",
        )
        (self.frontend_dir / "assets" / "app.js").write_text(
            "console.log('ok')",
            encoding="utf-8",
        )
        self.client = TestClient(create_app(frontend_dir=self.frontend_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_serves_frontend_index(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("MediaAI Renamer", response.text)

    def test_falls_back_to_index_for_spa_route(self):
        response = self.client.get("/settings/scraping")
        self.assertEqual(200, response.status_code)
        self.assertIn("MediaAI Renamer", response.text)

    def test_keeps_api_routes_ahead_of_frontend_mount(self):
        response = self.client.get("/api/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["status"])

    def test_returns_404_for_missing_static_asset(self):
        response = self.client.get("/assets/missing.js")
        self.assertEqual(404, response.status_code)

    def test_starts_without_frontend_build(self):
        missing_dir = self.frontend_dir / "missing"
        client = TestClient(create_app(frontend_dir=missing_dir))
        self.assertEqual(200, client.get("/api/health").status_code)
        self.assertEqual(404, client.get("/").status_code)
```

- [ ] **步骤 2：运行测试并确认失败**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_web -v
```

预期：FAIL，`create_app()` 尚不支持 `frontend_dir`。

- [ ] **步骤 3：实现 SPA 静态服务**

在 `backend/app/web.py` 中实现：

```python
"""前端静态资源托管。"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    """为无扩展名的 Vue 路由返回 index.html。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def resolve_frontend_dir(frontend_dir: Path | None = None) -> Path:
    if frontend_dir is not None:
        return frontend_dir
    return Path(os.getenv("MEDIAAI_FRONTEND_DIR", "/app/frontend-dist"))


def mount_frontend(app: FastAPI, frontend_dir: Path | None = None) -> bool:
    directory = resolve_frontend_dir(frontend_dir)
    if not (directory / "index.html").is_file():
        return False
    app.mount("/", SpaStaticFiles(directory=directory, html=True), name="frontend")
    return True
```

在 `backend/app/main.py` 中把签名调整为：

```python
def create_app(
    settings: AppSettings | None = None,
    *,
    frontend_dir: Path | None = None,
) -> FastAPI:
```

引入 `Path` 和 `mount_frontend`，并在所有 `include_router()` 之后调用：

```python
frontend_mounted = mount_frontend(app, frontend_dir)
logger.info("前端静态资源%s挂载", "已" if frontend_mounted else "未")
```

- [ ] **步骤 4：运行定向测试**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend.tests.test_web backend.tests.test_health -v
```

预期：全部 PASS。

- [ ] **步骤 5：运行后端完整测试**

运行：

```powershell
npm.cmd run backend:test
```

预期：全部测试通过。

- [ ] **步骤 6：提交静态托管实现**

```powershell
git add backend/app/main.py backend/app/web.py backend/tests/test_web.py
git commit -m "feat: 由 FastAPI 托管前端页面"
```

### 任务 2：合并 Docker 镜像和 Compose 服务

**文件：**
- 创建：`docker/Dockerfile`
- 删除：`docker/Dockerfile.backend`
- 删除：`docker/Dockerfile.frontend`
- 删除：`docker/nginx.conf`
- 修改：`docker-compose.yml`
- 修改：`docker-compose.ghcr.yml`

- [ ] **步骤 1：创建统一多阶段 Dockerfile**

`docker/Dockerfile` 内容：

```dockerfile
FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json /frontend/package.json
RUN npm install
COPY frontend /frontend
RUN npm run build

FROM python:3.11-slim

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY config/config.example.toml /app/config/config.example.toml
COPY --from=frontend-build /frontend/dist /app/frontend-dist

ENV PYTHONPATH=/app/backend
ENV MEDIAAI_FRONTEND_DIR=/app/frontend-dist

EXPOSE 8970

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8970"]
```

- [ ] **步骤 2：把源码构建 Compose 改为单服务**

`docker-compose.yml` 只保留：

```yaml
services:
  mediaai:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: mediaai-renamer
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config:/app/config:ro
    ports:
      - "8970:8970"
      - "8971:8970"
```

- [ ] **步骤 3：把 GHCR Compose 改为单镜像**

`docker-compose.ghcr.yml` 使用：

```yaml
services:
  mediaai:
    image: ghcr.io/txlfire/mediaai-renamer:${MEDIAAI_IMAGE_TAG:-latest}
    container_name: mediaai-renamer
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config:/app/config:ro
      # - /vol1/1000/Movies:/app/media/Movies
    ports:
      - "8970:8970"
      - "8971:8970"
```

- [ ] **步骤 4：删除旧双镜像文件**

删除：

```text
docker/Dockerfile.backend
docker/Dockerfile.frontend
docker/nginx.conf
```

- [ ] **步骤 5：校验 Compose**

运行：

```powershell
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.ghcr.yml config --quiet
docker compose -f docker-compose.yml config --services
docker compose -f docker-compose.ghcr.yml config --services
```

预期：两次校验成功，两次服务列表都只输出 `mediaai`。

- [ ] **步骤 6：构建统一镜像**

运行：

```powershell
docker build -f docker/Dockerfile -t mediaai-renamer:single-container-test .
```

预期：构建成功，前端和后端均进入同一镜像。

- [ ] **步骤 7：提交 Docker 结构调整**

```powershell
git add docker docker-compose.yml docker-compose.ghcr.yml
git commit -m "build: 合并前后端 Docker 镜像"
```

### 任务 3：将 GHCR 调整为单镜像发布

**文件：**
- 修改：`.github/workflows/docker-ghcr.yml`

- [ ] **步骤 1：删除矩阵并统一镜像元数据**

将工作流的 job 名称改为 `Build and push unified image`，删除 `strategy.matrix`，Docker metadata 使用：

```yaml
images: ghcr.io/${{ github.repository }}
```

构建步骤使用：

```yaml
file: docker/Dockerfile
cache-from: type=gha,scope=mediaai
cache-to: type=gha,mode=max,scope=mediaai
```

- [ ] **步骤 2：静态核查工作流**

运行：

```powershell
rg -n "matrix|Dockerfile\\.backend|Dockerfile\\.frontend|image_suffix|mediaai-renamer-(backend|frontend)" .github/workflows/docker-ghcr.yml
```

预期：无匹配。

运行：

```powershell
rg -n "docker/Dockerfile|ghcr.io/\\$\\{\\{ github.repository \\}\\}" .github/workflows/docker-ghcr.yml
```

预期：匹配统一 Dockerfile 和统一镜像地址。

- [ ] **步骤 3：提交 CI 调整**

```powershell
git add .github/workflows/docker-ghcr.yml
git commit -m "ci: 发布单一 Docker 镜像"
```

### 任务 4：更新部署说明和项目发布技能

**文件：**
- 修改：`.codex/skills/mediaai-test-release/SKILL.md`
- 修改：`.codex/skills/mediaai-test-release/references/release-context.md`
- 修改：`docs/deployment/fnos-ghcr-docker.md`
- 修改：`docs/development/development-guide.md`
- 修改：`docs/development/common-tasks.md`
- 修改：`scripts/dev-docker.sh`

- [ ] **步骤 1：更新项目技能中的发布约定**

将双镜像清单替换为：

```text
ghcr.io/txlfire/mediaai-renamer:<tag>
```

保留源码构建 Compose 与 GHCR Compose 分离的规则，并增加“两份 Compose 都应只有 `mediaai` 服务”的检查。

- [ ] **步骤 2：更新 fnOS 部署和升级步骤**

文档明确：

```bash
docker compose -f docker-compose.ghcr.yml down --remove-orphans
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 mediaai
```

旧版迁移章节补充停止并删除 `mediaai-renamer-backend`、`mediaai-renamer-frontend` 旧容器，但不删除 `data`、`logs`、`config`。

- [ ] **步骤 3：更新开发文档和脚本示例**

Docker 部署统一写成一个镜像、一个容器；源码开发继续说明前端 `5173`、后端 `8970`。把 `scripts/dev-docker.sh` 示例标签改为通用 `<tag>` 说明，不再引用历史版本。

- [ ] **步骤 4：检查过期引用**

运行：

```powershell
rg -n "mediaai-renamer-(backend|frontend)|Dockerfile\\.backend|Dockerfile\\.frontend|logs --tail=100 (backend|frontend)" .codex/skills/mediaai-test-release docs/deployment/fnos-ghcr-docker.md docs/development/development-guide.md docs/development/common-tasks.md scripts/dev-docker.sh
```

预期：无匹配。

- [ ] **步骤 5：执行编码检查**

运行：

```powershell
npm.cmd run check:encoding
```

预期：通过且无乱码报告。

- [ ] **步骤 6：提交文档和技能**

```powershell
git add .codex/skills/mediaai-test-release docs/deployment/fnos-ghcr-docker.md docs/development/development-guide.md docs/development/common-tasks.md scripts/dev-docker.sh
git commit -m "docs: 更新单容器部署说明"
```

### 任务 5：运行单容器端到端验收

**文件：**
- 修改：`docs/work-logs/progress-2026-07-30-single-container-release.md`

- [ ] **步骤 1：运行完整自动化验证**

运行：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.ghcr.yml config --quiet
```

预期：测试、构建、编码检查与 Compose 校验全部通过。

- [ ] **步骤 2：启动单容器**

为避免覆盖正式标签，运行：

```powershell
docker compose -p mediaai-single-test -f docker-compose.yml up -d --build
docker compose -p mediaai-single-test -f docker-compose.yml ps
```

预期：只有 `mediaai-renamer` 一个容器处于运行状态。

- [ ] **步骤 3：验证页面和两个兼容入口**

运行：

```powershell
Invoke-WebRequest http://127.0.0.1:8971/ -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:8971/api/health
Invoke-RestMethod http://127.0.0.1:8970/api/health
```

预期：页面返回 200，两个健康接口均返回 `status: ok`。

- [ ] **步骤 4：验证重启和数据持久化**

运行：

```powershell
docker compose -p mediaai-single-test -f docker-compose.yml restart
Start-Sleep -Seconds 5
docker compose -p mediaai-single-test -f docker-compose.yml ps
Test-Path data/mediaai.sqlite3
```

预期：单容器恢复运行，数据库文件仍存在。

- [ ] **步骤 5：停止验收容器**

运行：

```powershell
docker compose -p mediaai-single-test -f docker-compose.yml down
```

预期：测试容器和网络移除，持久化目录保留。

- [ ] **步骤 6：记录验收证据**

在 `docs/work-logs/progress-2026-07-30-single-container-release.md` 记录：

- 测试和构建命令结果。
- 统一镜像名称。
- Compose 服务数量。
- 页面与健康接口结果。
- 重启和持久化验证结果。
- 本次未包含的工作区既有修改。

- [ ] **步骤 7：提交验收记录**

```powershell
git add docs/work-logs/progress-2026-07-30-single-container-release.md
git commit -m "test: 记录单容器发布验收结果"
```

