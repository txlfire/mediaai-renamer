# M11 WebDAV 验收报告

验收日期：2026-07-26

## 1. 结论

WebDAV 后端主链路、恢复前端、容器集成环境和 GitHub Actions 真实协议测试已经完成。fnOS / 实际 NAS 的人工兼容性验证尚未执行，因此该项明确保留为部署验收项，不以自动化结果替代。

## 2. 已验证

| 范围 | 环境 | 结果 | 证据 |
| --- | --- | --- | --- |
| WebDAV 真实协议 10 个场景 | GitHub Actions，Python 3.13，HTTPS WsgiDAV | 10/10 通过 | Actions run `30195539240` |
| WebDAV 与 Compose | GitHub Actions，Docker | 两份 Compose 解析通过，WebDAV 10/10 通过 | Actions run `30195986444` |
| 后端单元与服务测试 | Windows 项目虚拟环境 | 314 个执行，304 个通过，10 个容器用例按设计跳过 | `npm.cmd run backend:test` |
| 前端测试 | Windows，固定 Node 运行时 | 17 个测试文件、78 个用例通过 | Vitest |
| 前端类型与构建 | Windows，固定 Node 运行时 | 类型检查通过，Vite 生产构建通过 | vue-tsc / Vite |
| 编码和差异检查 | Windows | 通过 | `check:encoding` / `git diff --check` |
| 发布包内容与哈希 | Windows | 5 个条目，内容检查通过 | `mediaai-renamer-frontend-v1.0.0.zip` |

发布包：

- 大小：`487367` 字节。
- SHA-256：`bfd3ab8da9564d2a3c8bf621e13758aeaa8b7c2f2eadb0e9bdf5fba2c674e946`。
- 包含：前端静态文件和 `config/config.example.toml`。
- 不包含：正式 `config.toml`、SQLite、日志、证书、私钥、测试凭据或真实媒体文件。

真实协议测试覆盖：

1. HTTPS Basic 连接成功。
2. 错误密码认证失败。
3. 目录浏览。
4. 递归扫描、文件大小和 ETag。
5. dry-run 目标冲突。
6. MOVE 和禁止覆盖。
7. 反向 MOVE 回滚。
8. 远端已移动后的数据库恢复。
9. 歧义状态转人工处理。
10. 写锁冲突。

## 3. 未验证

- fnOS / 实际 NAS WebDAV 服务兼容性。
- 特定厂商反向代理对 `Destination`、`Depth` 和 `Overwrite` 请求头的处理。
- 私有 CA 在具体 NAS 容器运行环境中的挂载方式。

这些项目不会阻止通用稳定版发布，但部署前必须按
[WebDAV 部署说明](../../deployment/webdav.md)执行人工验证。

## 4. 支持边界

- 支持：HTTPS、无认证、Basic、Bearer、浏览、扫描、预览、MOVE、回滚和失败恢复。
- 不支持：HTTP、Digest、跳过 TLS 校验、目标覆盖、跨站点移动。
- FTP、FTPS、SFTP、S3 / MinIO 仅为未来候选能力。

## 5. 发布判定

本地与集成自动化门槛已通过。只有 `main` 合并、标签、GitHub Release 和 GHCR 镜像工作流完成后，才可将本报告结论更新为 `v1.0.0` 已发布。
