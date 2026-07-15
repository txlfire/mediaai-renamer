# M11-1C WebDAV MOVE dry-run 工作日志

日期：2026-07-15

## 范围

本阶段在 M11-1B WebDAV 递归扫描基础上，接入 WebDAV 重命名 dry-run 基础能力，不开放真实远程 MOVE。

## 已完成

- WebDAV Provider 的 `check_rename_ready` 改为执行 dry-run 校验：
  - 校验源路径和目标路径均为 HTTPS WebDAV URL。
  - 使用 `PROPFIND` 确认源文件存在且不是目录。
  - 使用 `PROPFIND` 确认目标文件不存在，目标已存在时返回冲突。
- 重命名 dry-run 服务支持 WebDAV URL：
  - WebDAV 源不再被本地 `Path.exists()` 拦截。
  - 目标路径按源 URL 所在目录拼接远程目标文件名。
  - 批次内重复目标仍使用远程 URL 进行去重。
- 真实执行阶段增加边界保护：
  - WebDAV ready 项若进入真实执行，明确失败为“WebDAV 真实 MOVE 尚未启用”。
  - 本阶段不尝试本地 `Path.rename()`，避免误报“源文件不存在”。
- 版本号提升至 `0.11.8`。

## 边界

- 本阶段不执行 WebDAV `MOVE`。
- 本阶段不接入远程操作锁、幂等远程操作明细、失败恢复和回滚计划。
- 本阶段不新增数据库迁移。

## 验证

已完成针对性验证：

```text
backend.tests.test_shared_protocols + backend.tests.test_rename_operations -> 26 tests OK
```

完整验证将在本阶段收尾时执行：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```
