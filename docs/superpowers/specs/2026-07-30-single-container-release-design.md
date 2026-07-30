# MediaAI Renamer 单容器发布设计

## 目标

将正式 Docker 发布从前端、后端两个镜像和两个容器收敛为一个镜像、一个容器，同时保持本地开发时前后端独立启动的工作方式不变。

## 方案选择

### 采用方案：FastAPI 直接托管 Vue 静态资源

- Docker 多阶段构建先编译 Vue，再把 `frontend/dist` 复制到 Python 运行镜像。
- Uvicorn 作为容器内唯一常驻进程。
- FastAPI 继续提供 `/api/*`，并在 API 路由之后挂载前端静态资源。
- 前端历史路由回退到 `index.html`，静态资源不存在时返回标准 404。

该方案只有一个应用进程，不需要在同一容器中引入 Supervisor、Nginx 或额外进程管理。

### 未采用方案

1. Nginx 与 Uvicorn 同容器：能复用现有 Nginx 配置，但需要管理两个进程，停止、日志和故障恢复更复杂。
2. 保持双镜像、由 Compose 封装：用户仍需下载两个镜像并运行两个容器，没有解决发布包过多的问题。

## 镜像与端口

- 新镜像：`ghcr.io/txlfire/mediaai-renamer:<tag>`。
- 容器名：`mediaai-renamer`。
- 容器内部仅监听 `8970`。
- 页面主入口保留为宿主机 `8971`，映射为 `8971:8970`。
- 为兼容已有脚本和 API 调用，过渡期同时保留 `8970:8970`。
- 前端继续使用相对地址 `/api`，不需要修改运行时后端地址。

## 数据与升级兼容

- 保持 `./data:/app/data`、`./logs:/app/logs`、`./config:/app/config:ro` 不变。
- 数据库、配置和日志目录不迁移。
- 旧双容器升级时先执行 `docker compose down`，再使用新 Compose 启动，避免旧容器占用端口。
- 本地源码开发仍使用 Vite `5173` 和 FastAPI `8970`，不受正式发布结构影响。

## 代码与发布改动

1. 新增统一 Dockerfile，包含 Node 构建阶段和 Python 运行阶段。
2. 后端新增可测试的 SPA 静态资源挂载逻辑。
3. `docker-compose.yml` 与 `docker-compose.ghcr.yml` 改为单服务。
4. GHCR 工作流改为构建并发布单一镜像。
5. 更新发布技能、fnOS 部署文档及相关镜像名称说明。
6. 旧 `Dockerfile.backend`、`Dockerfile.frontend` 和 `nginx.conf` 在切换完成后删除，避免继续维护两套正式发布路径。

## 路由与错误处理

- `/api/*` 始终由 FastAPI API 路由处理。
- `/assets/*` 等真实静态文件按文件返回，并保留浏览器缓存能力。
- Vue 前端路由在目标文件不存在时返回 `index.html`。
- 不存在的静态资源文件不回退到 HTML，返回 404，避免脚本或样式请求收到错误内容。
- 未生成前端资源的开发与测试环境只启动 API，不因静态目录缺失而失败。

## 验证标准

1. 后端测试覆盖静态首页、SPA 路由回退、API 优先级和静态资源 404。
2. 前端测试与构建通过。
3. 单镜像 Docker 构建成功。
4. 两份 Compose 配置校验通过且都只有一个服务。
5. 容器启动后以下地址可用：
   - `http://<host>:8971/`
   - `http://<host>:8971/api/health`
   - `http://<host>:8970/api/health`
6. 重启容器后数据库、日志和配置仍可用。

## 发布边界

本次只调整 Docker 构建、运行和发布结构，不修改业务 API、页面功能、登录行为或数据库结构，也不把当前工作区中其他未提交功能混入该改动。
