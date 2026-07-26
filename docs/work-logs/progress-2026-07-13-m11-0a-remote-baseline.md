# M11-0A 远程协议基线工作日志

时间：2026-07-13 17:04:14

## 范围

- 启动 M11 远程协议扩展的第一段后端基线，不实现真实 WebDAV、SFTP、S3 或 FTP 文件操作。
- 为后续远程协议接入补齐协议能力枚举、媒体源远程字段和远程操作锁 / 明细表。

## 已完成

- 新增 `RemoteProtocolCapability` 能力枚举，覆盖浏览、扫描、读取元数据、原子重命名、复制删除式重命名、条件写入和可恢复能力。
- 为 `local`、`unc`、`mounted_nfs` 声明当前可用能力，保持现有本地 / SMB / 已挂载 NFS 行为不变。
- 为 WebDAV、FTP、SFTP、S3 候选协议补充能力声明，仅用于能力展示和后续接入判断，不开放真实执行。
- 数据库 schema 升级到 19，`media_sources` 增加远程端点、认证方式、凭据版本、远程根路径、能力快照和证书指纹字段。
- 新增 `remote_operation_locks` 和 `remote_operation_items`，为远程写操作租约、幂等和恢复记录预留结构。
- 补充旧 schema 18 数据库迁移测试和协议能力测试。

## 验证

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_shared_protocols backend.tests.test_database_migrations
```

结果：25 个测试通过。

## 未完成

- 版本化凭据加密和旧 SMB 密文迁移。
- WebDAV / SFTP / S3 真实 Provider。
- 前端媒体源协议动态字段和远程能力提示。
- 远程操作锁在重命名执行链路中的实际使用。
