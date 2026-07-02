# 2026-06-30 M5 正式版发布收尾

## 发布结论

用户已确认 M5 NAS / SMB / NFS 共享目录支持完成，本次按 `v0.5.3` 正式版收尾。

## 发布范围

- 本地路径、Windows UNC / SMB、已挂载路径 / NFS 三类媒体源。
- 共享协议能力接口、路径校验、连接测试、目录浏览、扫描前校验和重命名前校验。
- SMB 凭据复用媒体源自身加密字段保存，接口、页面和日志脱敏。
- NFS / mounted_nfs 不保存用户名、密码或 Kerberos 凭据，只访问系统已挂载路径。
- 扫描任务连接中断、部分完成、权限错误、NFS 文件句柄过期和超时等异常处理。
- 系统设置中的文件扫描、解析重命名、系统运维和共享目录配置。
- 计划外完成项：IMDb 元数据补充、命名构建器优化、统一批量操作进度、表格全屏修复、TMDB 匹配来源选择。

## 发布边界

- WebDAV、FTP / SFTP、S3 / 对象存储不属于 M5 可执行文件操作能力，已调整为 M10 或独立阶段评估。
- M6 将进入 AI 智能解析与敏感词外部提交保护。
- M7 聚焦命名规则高级配置二期。

## 发布验证

发布前已执行：

- `npm.cmd run backend:test`：通过，120 个测试通过。
- `npm.cmd run frontend:test`：通过，10 个测试文件、50 个测试通过。
- `npm.cmd run check:encoding`：通过。
- `npm.cmd run frontend:build`：通过，仅保留已知 Vite chunk size 警告。
- `git diff --check`：通过。
- `npm.cmd run release:package`：通过，生成 `releases/mediaai-renamer-frontend-v0.5.3.zip`。

## 发布后动作

- 将 `develop` 合并到 `main`。
- 创建 `v0.5.3` 标签。
- 推送 `develop`、`main` 和 `v0.5.3`。
- 切回 `develop`，准备 M6 开发。

## v0.5.4 正式发布补充

用户要求 M5 正式版继续发布并同步 Release，正式发版 README 不包含 M6 内容。本次 `v0.5.4` 作为 M5/M5+ 收尾正式版，范围限定在 M5 已完成能力、M5+ UI 与元数据回填修正、Docker/GHCR 发布基础，不包含 M6 AI 或敏感词外部提交保护功能说明。

本次调整：

- 版本号从 `0.5.3` 升级到 `0.5.4`。
- README 当前阶段改为 M5 NAS / SMB / NFS 共享目录支持正式版，不写 M6 后续规划。
- fnOS GHCR 部署默认镜像标签同步到 `v0.5.4`。
- M5 设计手册和用户手册版本同步到 `0.5.4`。
验证结果：

- `npm.cmd run backend:test`：通过，138 个测试通过。
- `npm.cmd run frontend:test`：通过，11 个测试文件、55 个测试通过。
- `npm.cmd run check:encoding`：通过。
- `npm.cmd run frontend:build`：通过，仅保留已知 Vite chunk size 警告。
- `git diff --check`：通过。
- `npm.cmd run release:package`：通过，生成 `releases/mediaai-renamer-frontend-v0.5.4.zip`。