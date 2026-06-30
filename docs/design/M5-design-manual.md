# MediaAI Renamer M5 设计手册

版本：`0.5.3`
阶段：M5 NAS / SMB / NFS 共享目录支持
日期：2026-06-28

## 1. 阶段结论

M5 在既有本地目录扫描、命名预览、TMDB 匹配和安全重命名基础上，完成共享目录接入的第一版闭环。系统支持三类媒体源路径：本地路径、Windows UNC / SMB、已挂载路径 / NFS。

本阶段不自动挂载 SMB 或 NFS，不执行 `net use`、`mount` 等系统命令。程序只验证当前服务进程是否能访问目标路径，并在扫描和重命名前执行统一协议校验。

## 2. 支持范围

- `local`：服务所在机器可访问的本地路径。
- `unc`：Windows UNC / SMB 路径，例如 `\\nas\media`。
- `mounted_nfs`：Linux / NAS / Docker 中已经挂载到本机或容器内的路径，例如 `/mnt/media`、`/volume1/media`。
- WebDAV、FTP、SFTP、S3 仅作为后续候选协议展示，不在 M5 执行真实文件操作。

## 3. 后端设计

共享协议统一位于：

```text
backend/app/service/shared_protocols/
```

核心接口包含：

- `capabilities()`：返回协议能力边界。
- `validate_config()`：校验媒体源配置。
- `test_connection()`：连接测试。
- `list_directories()`：目录浏览。
- `check_scan_ready()`：扫描前校验。
- `check_rename_ready()`：重命名前校验。
- `normalize_path()`：路径规范化。

扫描、目录浏览、dry-run 和真实重命名前校验均通过协议注册表调用，不在业务层写死 SMB / NFS 判断。

## 4. 凭据安全

- SMB 账号密码只保存在媒体源自身加密字段中。
- API 响应不返回明文密码，只返回是否已配置凭据。
- 修改 SMB 密码采用覆写模式；留空表示保留旧密码。
- NFS 不显示用户名、密码、域、Kerberos、keytab 等配置项。
- 连接测试和运行日志不输出明文密码。

## 5. 异常处理

- 路径不存在、权限不足、共享不可达返回中文错误和排查建议。
- 扫描前会校验媒体源启用状态和路径可访问性。
- 扫描过程中出现可跳过的单文件异常时，记录 warning 并继续。
- 扫描存在 warning 时任务状态为 `partial_completed`。
- NFS 超时或文件句柄过期等连接类异常映射为 `connection_lost`。
- 重命名前继续复用 M3 dry-run、冲突检测和二次确认机制。

## 6. 系统设置

M5 补齐系统设置中非 TMDB 分类：

- 文件扫描配置。
- 解析重命名配置。
- 系统运维配置。
- 共享目录配置。

其中扫描配置、目录浏览上限、批量操作上限已经接入业务链路；部分运维类配置作为可保存参数，为后续更完整的任务治理保留。

## 7. 部署边界

Docker / NAS / NFS 使用时，必须先确保服务进程或容器内能看到路径。推荐方式是宿主机先完成 NFS 挂载，再通过 bind mount 映射进容器。

系统不会替用户创建挂载，也不会保存 NFS 密码或 Kerberos 凭据。

## 8. 验证

本阶段通过以下自动化验证：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```

外部 NAS / SMB / NFS 实机可达性仍需在目标部署环境中按验收清单手工验证。
