# M11-0B 版本化凭据迁移工作日志

时间：2026-07-14

## 范围

- 在不改变媒体源 API 的前提下，升级媒体源凭据密文格式。
- 保持旧 SMB 密文可读，并在数据库初始化时迁移到新格式。
- 不实现 WebDAV、SFTP、S3 或 FTP 的真实连接和文件操作。

## 已完成

- 新增 `v2:` 媒体源密文格式，包含随机 nonce、派生密钥流和 HMAC-SHA256 完整性校验。
- `decrypt_secret` 同时支持 `v2:` 新密文和旧 base64/XOR 密文。
- 新保存的 UNC / SMB 凭据写入 `v2:` 密文，并将 `credential_version` 标记为 `2`。
- 数据库 schema 升级到 `20`，初始化时将旧 `credential_version=1` 的媒体源密文迁移为 `v2:`。
- 迁移失败的历史坏数据不会阻断启动，保留旧值并记录警告日志。

## 验证

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_media_sources backend.tests.test_database_migrations
```

结果：35 个测试通过。

## 未完成

- 旧 SMB 密文之外的全局凭据模型仍未建设。
- 前端媒体源协议动态字段仍未接入。
- 远程操作锁尚未接入真实重命名执行链路。
