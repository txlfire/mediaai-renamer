# Harness 开发服务启动规约

类型：审核协作运行规约。适用于 Harness、Codex 及其他自动化审核工具在本仓库启动本地开发服务。

## 1. 结论

开发服务必须通过项目脚本管理。Windows 首选 `dev-windows.ps1` 包装入口；不得自行拼接 `uvicorn`、Vite、`Start-Process` 或 `cmd /c` 命令。

标准开发实例使用默认配置和默认开发数据库。隔离验证实例必须使用独立端口、独立配置、独立数据目录，并在验证结束时停止；不得与标准实例共用 `5173/8970`。

## 2. Windows 标准命令

以下命令均从仓库根目录执行：

```powershell
# 启动：前端 5173，后端 8970
npm.cmd run dev:windows -- start

# 查看状态
npm.cmd run dev:windows -- status

# 重启
npm.cmd run dev:windows -- restart

# 停止
npm.cmd run dev:windows -- stop
```

端口冲突且确认不能停止占用方时，可显式指定一组端口。启动、状态和停止必须始终使用同一组参数：

```powershell
npm.cmd run dev:windows -- start -FrontendPort 5173 -BackendPort 8971
npm.cmd run dev:windows -- status -FrontendPort 5173 -BackendPort 8971
npm.cmd run dev:windows -- stop -FrontendPort 5173 -BackendPort 8971
```

脚本会同步设置 Vite 的后端代理目标。禁止先单独启动前端，再让它沿用另一实例留下的 `VITE_BACKEND_URL`。

## 3. Linux、macOS 与 NAS 命令

```bash
npm run dev:linux -- start
npm run dev:linux -- status
npm run dev:linux -- restart
npm run dev:linux -- stop
```

自定义端口通过同一次命令的环境变量传入：

```bash
FRONTEND_PORT=5173 BACKEND_PORT=8971 npm run dev:linux -- start
FRONTEND_PORT=5173 BACKEND_PORT=8971 npm run dev:linux -- stop
```

## 4. 隔离验证实例

只有测试确实需要独立数据库或配置时才能启动隔离实例，并同时满足以下条件：

1. 配置文件、数据库、凭据密钥文件和日志全部位于 `.codex/test-artifacts/<任务名>/`。
2. 使用独立端口，例如前端 `5183`、后端 `8981`，不占用标准实例端口。
3. 仍通过项目启停脚本运行，以便生成 PID 文件并统一清理。
4. 启动前记录配置路径和端口；结束时在 `finally` 或等价清理路径中停止服务并清除进程环境变量。
5. 不在交流文档、日志或命令输出中记录真实账号、密码、令牌或私钥。

Windows 示例：

```powershell
$env:MEDIAAI_CONFIG_PATH = (Resolve-Path ".codex/test-artifacts/<任务名>/config.toml").Path
try {
    npm.cmd run dev:windows -- start -FrontendPort 5183 -BackendPort 8981
    # 仅在此处执行隔离验证。
} finally {
    npm.cmd run dev:windows -- stop -FrontendPort 5183 -BackendPort 8981
    Remove-Item Env:MEDIAAI_CONFIG_PATH -ErrorAction SilentlyContinue
}
```

如用户要求验证后继续保留服务，只允许保留标准开发实例；隔离实例仍应停止。需要保留非默认端口时，必须在交接信息中写明前端端口、后端端口和配置类型。

## 5. 启动后验证

不能只以“端口可连接”判定启动成功。至少验证后端健康接口和前端代理健康接口，并确认二者版本一致：

```powershell
$backend = Invoke-RestMethod "http://127.0.0.1:8970/api/health"
$frontendProxy = Invoke-RestMethod "http://127.0.0.1:5173/api/health"

if ($backend.status -ne "ok" -or $frontendProxy.status -ne "ok") {
    throw "开发服务健康检查失败"
}
if ($backend.version -ne $frontendProxy.version) {
    throw "前端代理连接了错误的后端实例"
}
```

自定义端口时同步替换上述端口。还应核对：

- `.codex/run-logs/backend.pid` 与 `frontend.pid` 存在；
- `.codex/run-logs/backend-uvicorn.err.log` 和 `frontend-vite.err.log` 没有启动错误；
- `Get-NetTCPConnection -State Listen` 不存在同一后端端口同时被 `127.0.0.1` 与 `0.0.0.0` 的不同进程监听；
- 标准实例没有设置 `MEDIAAI_CONFIG_PATH`，隔离实例的配置路径明确指向 `.codex/test-artifacts/`。

## 6. 禁止事项

Harness 不得执行以下做法：

- 直接运行 `python -m uvicorn ...`、`node .../vite.js` 或其他等价命令；
- 使用 `Start-Process`、`cmd /c`、WMI 或脱离任务生命周期的子进程长期驻留；
- 为隔离实例复用 `5173/8970`，或在标准实例运行时抢占相同端口；
- 仅保存自定义 PID 文件而绕过 `.codex/run-logs/backend.pid`、`frontend.pid`；
- 启动后不执行健康检查，或验证结束后不执行停止命令；
- 看到端口占用后直接杀死无法确认归属的进程；应先查看进程命令行，无法确认时改用独立端口；
- 把测试配置、测试数据库、日志或凭据提交到 Git。

## 7. 故障处理

### 7.1 端口被占用

1. 先按原端口执行 `stop`。
2. 再执行 `status`，确认是否仍有监听。
3. 使用 `Get-NetTCPConnection` 和 `Get-CimInstance Win32_Process` 核对占用进程命令行。
4. 归属不明确时不强杀，改用一组独立端口。

### 7.2 登录账号或数据与预期不一致

先检查运行进程是否继承了 `MEDIAAI_CONFIG_PATH`，再检查健康接口和代理目标。不要立即重置密码或修改数据库；错误实例可能使用另一份数据库。

2026-10-11 已发生一次典型事故：主题验证的隔离后端长期占用 `127.0.0.1:8970`，标准后端同时监听 `0.0.0.0:8970`，浏览器优先命中隔离实例，表现为默认开发账号密码“失效”。该问题不是正式开发库密码变化，而是非规范启动和未清理造成的实例串线。

### 7.3 脚本启动失败

查看以下文件，不要绕过脚本直接启动：

- `.codex/run-logs/backend-uvicorn.out.log`
- `.codex/run-logs/backend-uvicorn.err.log`
- `.codex/run-logs/frontend-vite.out.log`
- `.codex/run-logs/frontend-vite.err.log`

依赖或脚本本身确有问题时，记录错误并修复入口；不得以临时手工命令作为长期替代方案。

## 8. 交接要求

每次启动或停止服务后，Harness 的交接信息至少包括：

- 使用的项目脚本和完整参数；
- 前端、后端访问地址；
- 标准实例或隔离实例；
- 健康接口返回的应用版本；
- 是否按要求停止服务；若按用户要求保留，明确当前 PID 文件和日志目录；
- 未完成的验证或异常，不把端口可访问等同于功能验证通过。

项目脚本的详细行为以 [`scripts/README-zh.md`](../../../../scripts/README-zh.md) 和脚本源码为准。本规约只约束审核工具的启动方式与生命周期。
