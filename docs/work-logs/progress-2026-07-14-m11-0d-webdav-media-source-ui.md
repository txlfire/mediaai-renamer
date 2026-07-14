# M11-0D WebDAV 媒体源可用性收尾记录

日期：2026-07-14

## 范围

- 补齐完整用户手册中 0.11.4 / 0.11.5 已实现能力的说明，修正 WebDAV 当前边界。
- 媒体源前端增加 WebDAV 路径类型、HTTPS 地址提示、账号 / 密码或 Token 输入和连接测试支持。
- 后端媒体源编辑支持 WebDAV 地址校验、凭据更新、认证类型和协议端点同步。
- 版本号提升至 `0.11.5`。

## 已完成

- `update_media_source` 按当前媒体源类型处理 WebDAV 路径和凭据。
- WebDAV 媒体源编辑后仍保持 `path_type=webdav`、`protocol=webdav`，凭据继续加密保存并脱敏返回。
- 前端不再把 WebDAV URL 当作不支持协议拦截；WebDAV 新增和编辑时不显示本地目录选择按钮。
- 完整用户手册更新到 `0.11.5`，明确 WebDAV 只支持连接测试和目录浏览，暂不支持扫描和重命名。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_media_sources
npm.cmd run frontend:build
```

目标后端测试和前端构建已通过；Vite 大包警告为项目既有警告。

## 未完成

- WebDAV 递归扫描、ETag 入库、MOVE dry-run 和真实重命名未实现。
- SFTP、S3 和协议集成测试环境未开始。
