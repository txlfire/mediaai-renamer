# M11-0C 远程操作锁服务工作日志

时间：2026-07-14

## 范围

- 基于 M11-0A 已创建的 `remote_operation_locks` 和 `remote_operation_items` 表，补齐后端服务层基础能力。
- 本阶段不新增 HTTP API，不接入真实 WebDAV、SFTP、S3 或 FTP Provider。

## 已完成

- 新增 `remote_operation_service`，提供远程媒体源写操作租约申请、心跳刷新和释放能力。
- 同一 `lock_key` 的有效租约会阻止竞争写操作，过期或已释放租约允许重新获取。
- 新增远程操作明细创建服务，按 `idempotency_key` 返回已有记录，避免重复创建远程写操作。
- 补充远程操作锁冲突、过期重获、旧 token 心跳拒绝和幂等明细测试。

## 验证

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_service
```

结果：3 个测试通过。

## 未完成

- 远程操作锁尚未接入真实重命名执行链路。
- 远程操作明细尚未接入恢复 / 回滚入口。
- 前端媒体源协议动态字段和能力提示尚未实现。
