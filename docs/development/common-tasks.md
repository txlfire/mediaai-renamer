# 常用脚本和任务

本文档记录 MediaAI Renamer 开发、验证、打包和发布中经常使用的命令。

## 1. 本地启动

启动后端：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8970 --reload --app-dir backend
```

启动前端：

```powershell
npm run frontend:dev
```

访问地址：

```text
http://127.0.0.1:5173
```

后端健康检查：

```text
http://127.0.0.1:8970/api/health
```

停止占用端口的本地服务：

```powershell
Get-NetTCPConnection -LocalPort 8970,5173 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
```

## 2. 测试和构建

后端完整测试：

```powershell
npm run backend:test
```

前端 API 测试：

```powershell
npm run frontend:test
```

前端重点测试：

```powershell
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts frontend/src/stores/pagination.test.ts frontend/src/stores/tableSort.test.ts frontend/src/stores/preview.test.ts frontend/src/stores/renameOperation.test.ts frontend/src/utils/displayFormat.test.ts frontend/src/utils/localDirectory.test.ts frontend/src/api/client.test.ts
```

前端生产构建：

```powershell
npm run frontend:build
```

## 3. 打包发布包

按 `package.json` 当前版本打包：

```powershell
npm run release:package
```

指定版本打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/package-release.ps1 -Version 0.3.0
```

输出文件：

```text
releases/mediaai-renamer-frontend-v<version>.zip
```

## 4. 发布到 GitHub Releases

发布前确认：

```powershell
git status --short --branch
git tag --list
gh auth status
```

GitHub CLI 登录：

```powershell
gh auth login -h github.com
```

创建或更新当前版本的 GitHub Release：

```powershell
npm run release:publish
```

指定版本发布：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/package-release.ps1 -Version 0.3.0 -Publish
```

如果命令行访问 GitHub 需要代理，可先设置：

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
```

## 5. Git 发布流程

常规发布顺序：

```powershell
git switch develop
git status --short --branch
git push origin develop
git switch main
git pull --ff-only origin main
git merge --ff-only develop
git push origin main
git tag -a v0.3.0 -m "Release v0.3.0"
git push origin v0.3.0
npm run release:publish
```

## 6. Docker 启动

```powershell
docker compose up --build
```

默认地址：

```text
Web: http://localhost:8971
API: http://localhost:8970
```
