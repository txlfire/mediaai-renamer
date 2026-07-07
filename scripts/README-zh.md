# scripts 脚本说明

本文档说明 `scripts/` 目录下每个脚本的用途、推荐调用方式和关键执行步骤。脚本默认应在项目根目录通过 `npm` 命令或直接路径调用。

## 开发服务启停

### `dev-windows.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run dev:windows -- start`。
- 支持动作：`start`、`stop`、`restart`、`status`。
- 作用：Windows 下按输入动作一键后台启动、停止、重启或查看前后端开发服务状态。
- 默认端口：前端 `5173`，后端 `8970`。
- 关键步骤：
  - 根据 `Action` 参数判断操作类型。
  - 启动时调用 `start-dev-lan.ps1`。
  - 停止时调用 `stop-dev-lan.ps1`。
  - 状态检查时读取端口监听状态，不触发服务重启。

### `dev-linux.sh`

- 适用环境：Linux / macOS / NAS shell。
- 推荐调用：`npm run dev:linux -- start`。
- 支持动作：`start`、`stop`、`restart`、`status`。
- 作用：Linux/macOS 下按输入动作一键后台启动、停止、重启或查看前后端开发服务状态。
- 默认端口：可通过环境变量 `FRONTEND_PORT` 和 `BACKEND_PORT` 覆盖。
- 关键步骤：
  - 根据第一个参数判断操作类型。
  - 启动时调用 `start-dev-lan.sh`。
  - 停止时调用 `stop-dev-lan.sh`。
  - 状态检查时使用 `ss` 或 `lsof` 判断端口监听状态。

### `dev-docker.sh`

- 适用环境：Docker Compose。
- 推荐调用：`npm run dev:docker -- start`。
- 支持动作：`start`、`stop`、`restart`、`down`、`status`、`logs`。
- 作用：Docker Compose 下按输入动作一键管理容器化前后端服务。
- 默认 compose 文件：`docker-compose.yml`。
- 可选 compose 文件：通过 `COMPOSE_FILE=docker-compose.ghcr.yml` 使用 GHCR 镜像部署配置。
- 关键步骤：
  - 检查 `docker` 和 `docker compose` 是否可用。
  - 根据 `COMPOSE_FILE` 选择 compose 文件。
  - `start` 执行 `docker compose up -d`。
  - `stop` 执行 `docker compose stop`。
  - `down` 执行 `docker compose down`。
  - `status` 输出 `docker compose ps`。

### `start-dev-lan.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run dev:start` 或 `npm.cmd run dev:lan`。
- 作用：同时启动后端 FastAPI 服务和前端 Vite 服务，并监听局域网地址。
- 默认端口：前端 `5173`，后端 `8970`。
- 关键步骤：
  - 检查 Node、npm、Python 虚拟环境、后端依赖和前端 Vite 入口。
  - 调用 `stop-dev-lan.ps1` 清理旧进程和端口占用。
  - 创建 `.codex/run-logs` 日志目录。
  - 后台启动 `uvicorn app.main:app`。
  - 后台直接启动本地 `node_modules/vite/bin/vite.js`，使用 `frontend/vite.config.ts` 提供前端开发服务。
  - 写入 `backend.pid` 和 `frontend.pid`，供停止脚本使用。

### `stop-dev-lan.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run dev:stop`。
- 作用：停止本项目的后端和前端开发服务。
- 关键步骤：
  - 读取 `.codex/run-logs` 下的 PID 文件并停止对应进程。
  - 按命令行匹配当前项目目录下的 `uvicorn` 和 `vite` 进程做兜底清理。
  - 按端口清理 `5173` 和 `8970` 的监听进程。
  - 校验端口是否已经释放；未释放时提示可能需要管理员权限。

### `start-dev-lan.sh`

- 适用环境：Linux / macOS / NAS shell。
- 推荐调用：`npm run dev:start:linux`。
- 作用：同时启动后端 FastAPI 服务和前端 Vite 服务，并监听局域网地址。
- 默认端口：可通过环境变量 `FRONTEND_PORT` 和 `BACKEND_PORT` 覆盖。
- 关键步骤：
  - 检查 Node、npm、Python 虚拟环境、后端依赖和前端 Vite 入口。
  - 读取并清理旧 PID 文件。
  - 使用 `lsof` 或 `fuser` 清理端口占用。
  - 后台启动后端和前端服务。
  - 写入 PID 文件并输出访问地址和日志路径。

### `stop-dev-lan.sh`

- 适用环境：Linux / macOS / NAS shell。
- 推荐调用：`npm run dev:stop:linux`。
- 作用：停止本项目的后端和前端开发服务。
- 关键步骤：
  - 按 PID 文件停止后端和前端进程。
  - 使用 `lsof` 或 `fuser` 按端口兜底清理。
  - 使用 `ss` 或 `lsof` 检查端口是否仍在监听。
  - 端口无法释放时提示可能需要 `sudo` 或手动处理。

### `start-frontend-dev.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run frontend:dev` 或 `npm.cmd run frontend:dev:lan`。
- 作用：只启动前端 Vite 开发服务，不启动后端。
- 关键步骤：
  - 修正 Windows 进程内 `Path/PATH` 环境变量重复问题。
  - 查找 Node 可执行文件。
  - 检查本地 Vite 入口是否存在。
  - 根据参数以前台或后台模式启动前端服务。

### `serve-frontend.py`

- 适用环境：Windows / Linux / macOS。
- 推荐调用：按需手动调用，本地静态托管 `frontend/dist` 时使用。
- 作用：托管 `frontend/dist` 静态文件，并把 `/api/*` 反向代理到后端服务。
- 关键步骤：
  - 检查 `frontend/dist/index.html` 是否存在。
  - 将自身日志写入 `.codex/run-logs/frontend-vite.out.log` 和 `.codex/run-logs/frontend-vite.err.log`。
  - 对普通页面请求返回静态文件，找不到路径时回退到 `index.html`。
  - 对 `/api/*` 请求转发到 `http://127.0.0.1:8970`。

## 测试与校验

### `run-backend-tests.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run backend:test`。
- 作用：运行后端 unittest 测试套件。
- 关键步骤：
  - 进入项目根目录。
  - 设置 `PYTHONPATH=backend`。
  - 使用 `.venv\Scripts\python.exe` 执行 `unittest discover backend\tests -v`。

### `run-backend-tests.sh`

- 适用环境：Linux / macOS。
- 推荐调用：`npm run backend:test:linux`。
- 作用：运行后端 unittest 测试套件。
- 关键步骤：
  - 进入项目根目录。
  - 设置 `PYTHONPATH=backend`。
  - 优先使用 `.venv/bin/python`，找不到时降级到 `python3` 或 `python`。
  - 执行 `unittest discover backend/tests -v`。

### `check-encoding.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run check:encoding`。
- 作用：检查文本文件编码、换行和前端文案规范。
- 关键步骤：
  - 收集 Git 已跟踪文件和未忽略的新文本文件。
  - 检查 UTF-8 BOM、混合换行和疑似乱码字符。
  - 调用 `scripts/check-mojibake.py` 执行语义级乱码检测，识别 UTF-8 文本被 GBK/PowerShell 管道误写后的可逆乱码。
  - 检查 `frontend/src` 中非 `locales` 文件的中文硬编码。
  - 发现问题时输出问题类型和文件路径。

### `check-mojibake.py`

- 适用环境：Windows / Linux / macOS。
- 推荐调用：由 `check-encoding.ps1` 自动调用。
- 作用：补充检查语义级 mojibake，防止 `Get-Content | Set-Content` 等误操作污染源码。
- 关键步骤：
  - 收集 Git 已跟踪文件和未忽略的新文本文件。
  - 严格按 UTF-8 解码文本文件。
  - 检查替换字符、Unicode 私用区字符。
  - 尝试按 `GBK bytes -> UTF-8` 反向修复单行文本；若修复后中文可读性显著提升，则报告 `SEMANTIC_MOJIBAKE`。

## 发布打包

### `package-release.ps1`

- 适用环境：Windows。
- 推荐调用：`npm.cmd run release:package` 或 `npm.cmd run release:publish`。
- 作用：构建前端正式包，并可选发布到 GitHub Release。
- 关键步骤：
  - 未传版本时读取根 `package.json` 的版本号。
  - 默认执行 `npm.cmd run frontend:build`。
  - 将 `frontend/dist` 复制到临时打包目录。
  - 只复制 `config/config.example.toml`，不打包正式配置 `config.toml`。
  - 压缩为 `releases/mediaai-renamer-frontend-vX.Y.Z.zip`。
  - 使用 `-Publish` 时通过 `gh` 创建或更新 GitHub Release。

### `package-release.sh`

- 适用环境：Linux / macOS。
- 推荐调用：`npm run release:package:linux` 或 `npm run release:publish:linux`。
- 作用：构建前端正式包，并可选发布到 GitHub Release。
- 关键步骤：
  - 解析 `--version`、`--repo`、`--target`、`--notes`、`--publish`、`--skip-build` 参数。
  - 未传版本时读取根 `package.json` 的版本号。
  - 默认执行 `npm run frontend:build`。
  - 将 `frontend/dist` 和 `config/config.example.toml` 复制到临时打包目录。
  - 优先使用 `zip` 压缩；没有 `zip` 时使用 Python 标准库兜底。
  - 使用 `--publish` 时通过 `gh` 创建或更新 GitHub Release。

## 日志与 PID 文件

- 开发服务日志目录：`.codex/run-logs/`。
- 后端标准输出：`.codex/run-logs/backend-uvicorn.out.log`。
- 后端错误日志：`.codex/run-logs/backend-uvicorn.err.log`。
- 前端标准输出：`.codex/run-logs/frontend-vite.out.log`。
- 前端错误日志：`.codex/run-logs/frontend-vite.err.log`。
- 后端 PID：`.codex/run-logs/backend.pid`。
- 前端 PID：`.codex/run-logs/frontend.pid`。

## 常见问题

- 端口仍被占用：先执行停止脚本；如果仍失败，Windows 可能需要管理员终端，Linux/macOS 可能需要 `sudo`。
- 后端依赖缺失：创建虚拟环境并安装 `backend/requirements.txt`。
- 前端依赖缺失：在项目根目录执行 `npm install`。
- 发布包中找不到正式配置：这是预期行为，正式发布包只包含 `config.example.toml`。
