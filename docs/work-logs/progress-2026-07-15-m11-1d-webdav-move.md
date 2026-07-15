# M11-1D WebDAV 真实 MOVE 工作日志

日期：2026-07-15

## 范围

本阶段在 M11-1C WebDAV MOVE dry-run 基础上，实现 WebDAV 真实 MOVE 的最小安全闭环。

## 已完成

- WebDAV Provider 新增 `move_file`：
  - 使用 `MOVE` 请求。
  - 设置 `Destination` 为目标 WebDAV URL。
  - 设置 `Overwrite: F`，禁止覆盖已有目标。
  - 保留 Basic / Bearer 认证头和统一超时。
- 重命名执行链路接入 WebDAV 真实 MOVE：
  - 执行前继续要求条目来自 dry-run ready 结果。
  - 同一 WebDAV 媒体源执行真实 MOVE 前申请远程写操作锁。
  - 每个远程条目写入 `remote_operation_items` 幂等明细。
  - MOVE 成功后更新媒体文件、命名预览、重命名条目和重命名批次统计。
  - MOVE 失败时标记当前条目失败，并写入远程操作明细错误信息。
- 远程操作服务新增远程明细状态更新方法。
- WebDAV 协议能力声明更新为支持真实重命名。
- 版本号提升至 `0.11.9`。

## 边界

- 本阶段不实现 WebDAV 回滚计划真实 MOVE。
- 本阶段不实现中断后的自动恢复入口。
- 本阶段不引入 SFTP、S3 或 FTPS / FTP。
- 本阶段不新增数据库迁移。

## 验证

已完成针对性验证：

```text
backend.tests.test_remote_operation_service + backend.tests.test_shared_protocols + backend.tests.test_rename_operations -> 32 tests OK
```

完整验证将在本阶段收尾时执行：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```
