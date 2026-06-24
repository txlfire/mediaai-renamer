# MediaAI Renamer

MediaAI Renamer 是一个面向 NAS、fnOS、Emby、Jellyfin、Plex、Kodi 等场景的影视文件重命名工具。

当前处于 M1 阶段，已支持媒体源保存、本地 / 已挂载目录扫描、视频文件识别、扫描结果保存、日志查看和前端主题切换。

## 默认端口

- 后端 API：`http://localhost:8970`
- 前端 Web：`http://localhost:8971`

前端页面通过同源 `/api` 访问后端，不硬编码后端主机地址。

## 本地启动

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

开发访问地址：

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

## 扫描配置

示例配置位于 `config/config.example.toml`：

```toml
[scan]
batch_size = 100
batch_interval_seconds = 1
```

M1 使用分批扫描，默认每批 100 个文件，每批之间间隔 1 秒。

## 验证命令

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
npm run frontend:test
npm run frontend:build
```
