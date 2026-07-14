# M11-1A WebDAV 基线工作日志

时间：2026-07-14

## 范围

- 将 WebDAV 从候选协议推进为后端已注册协议。
- 本阶段只开放 HTTPS 地址校验、连接测试、目录浏览和媒体源保存。
- 不开放 WebDAV 扫描、命名预览、dry-run、真实重命名和失败恢复。

## 已完成

- 新增 `WebDavProtocol`，要求 WebDAV 地址必须使用 HTTPS。
- 支持 Basic 和 Bearer 请求头构造，凭据只在后端上下文中使用，不回显明文。
- 支持 `OPTIONS` 连接测试，并将常见 HTTP / 网络错误转换为中文提示。
- 支持 `PROPFIND Depth: 1` 目录浏览，只返回 collection 目录项。
- 媒体源服务允许 `path_type=webdav`，保存时写入 `protocol=webdav`、`protocol_endpoint`、`auth_type` 和 `v2` 密文。
- 保持 `supports_scan=false` 和 `supports_rename=false`，避免未完成能力被前端或业务流程误用。

## 验证

```powershell
$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_shared_protocols backend.tests.test_media_sources backend.tests.test_m1_api
```

结果：32 个测试通过。

## 未完成

- WebDAV 递归扫描和 ETag / 修改时间元数据读取。
- WebDAV MOVE dry-run 和真实重命名。
- 远程操作锁接入 WebDAV 写操作。
- 容器化 WebDAV 集成测试环境。
