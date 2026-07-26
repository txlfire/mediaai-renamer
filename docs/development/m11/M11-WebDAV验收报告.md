# M11 WebDAV 验收报告

验收日期：2026-07-26

## 1. 结论

WebDAV 后端主链路、恢复前端、容器集成环境和 GitHub Actions 真实协议测试已经完成。fnOS / 实际 NAS 的人工兼容性验证尚未执行，因此该项明确保留为部署验收项，不以自动化结果替代。

## 2. 已验证

| 范围 | 环境 | 结果 | 证据 |
| --- | --- | --- | --- |
| WebDAV 真实协议 10 个场景 | GitHub Actions，Python 3.13，HTTPS WsgiDAV | 10/10 通过 | Actions run `30195539240` |
| 后端单元与服务测试 | Windows 项目虚拟环境 | 待最终发布门槛补录 | `npm.cmd run backend:test` |
| 前端测试与构建 | Windows，项目固定 Node 运行时 | 待最终发布门槛补录 | `frontend:test` / `frontend:build` |
| 编码和差异检查 | Windows | 待最终发布门槛补录 | `check:encoding` / `git diff --check` |
| 发布包内容与哈希 | Windows | 待打包后补录 | `releases/` |

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

只有最终后端测试、前端测试、类型检查、构建、编码检查、打包检查、发布包内容检查和发布工作流全部通过后，才可将本报告结论更新为 `v1.0.0` 已发布。
