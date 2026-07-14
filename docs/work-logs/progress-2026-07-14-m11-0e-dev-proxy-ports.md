# M11-0E 开发服务代理端口收尾记录

日期：2026-07-14

## 背景

本地默认后端端口 `8970` 出现旧进程占用且普通权限无法释放的情况。为保证后续开发和验证不被固定端口阻塞，需要让前端开发代理支持跟随指定后端端口。

## 已完成

- `frontend/vite.config.ts` 支持从 `VITE_BACKEND_URL` 读取 `/api` 代理目标，默认仍为 `http://127.0.0.1:8970`。
- `scripts/start-dev-lan.ps1` 和 `scripts/start-dev-lan.sh` 启动前端前会按后端端口设置 `VITE_BACKEND_URL`。
- `scripts/start-frontend-dev.ps1` 增加 `-BackendUrl` 参数，便于单独启动前端时指定代理目标。
- 更新 `docs/development/common-tasks.md` 和 `scripts/README-zh.md`，记录自定义端口用法。
- 版本号提升至 `0.11.6`。

## 验证

已用备用端口完成运行态验证：

```text
后端：http://127.0.0.1:8972/api/health -> 0.11.6
前端：http://127.0.0.1:5174 -> HTTP 200
```

已完成 `0.11.6` 后端测试、前端测试、前端构建、编码检查和运行态验证。

## 注意

- 默认 `8970` 被外部旧进程占用时，停止脚本可能需要管理员权限或系统重启才能彻底释放。
- 备用端口只用于开发验证，不改变生产部署默认端口。
