# MediaAI Renamer WebDAV 与 v1.0.0 稳定版收口设计

日期：2026-07-26

## 1. 决策结论

MediaAI Renamer 的功能开发范围收敛为现有本地、SMB / UNC、已挂载 NFS、WebDAV、元数据匹配、AI 解析、安全重命名、回滚、审计和任务治理能力。

M11 只完成 WebDAV，不实现 FTP、FTPS、SFTP、S3 / MinIO。后者仅保留在文档的“未来候选能力”章节，不再作为已排期里程碑或稳定版验收项。

WebDAV 完成产品化收口后发布 `v1.0.0`。此版本表示产品核心范围完成，后续默认进入维护周期：

- `1.0.x`：问题修复、安全修复、兼容性修复。
- `1.1.x`：不破坏现有流程的体验、性能和可观测性优化。
- 新协议或大功能只有在重新评估实际价值后，才建立新的独立设计和里程碑。

## 2. v1.0.0 正式支持边界

### 2.1 正式支持

- 本地路径。
- Windows UNC / SMB。
- Linux、NAS、Docker 或 NFS 的已挂载路径。
- HTTPS WebDAV：
  - 无认证。
  - Basic 用户名和密码认证。
  - Bearer Token 认证。
  - 连接测试和目录浏览。
  - 递归扫描、大小、修改时间和 ETag 读取。
  - 命名预览和 MOVE dry-run。
  - 禁止覆盖的真实 MOVE 重命名。
  - 反向 MOVE 回滚。
  - 失败操作查询、状态判定、安全重试和数据库状态补齐。
  - 媒体源级远程写锁、幂等明细和审计记录。

### 2.2 明确不支持

- 明文 HTTP WebDAV。
- 跳过 TLS 证书校验。
- Digest 认证。
- 未受操作系统或容器信任链信任的自签名证书。
- FTP、FTPS、SFTP、S3 / MinIO 的连接、扫描或文件写入。
- 跨媒体源移动、跨 WebDAV 站点复制和远程目录自动挂载。

数据库中的远程协议扩展字段可以保留，作为兼容和未来候选能力预留，但稳定版前端不展示未实现协议的可操作入口，也不宣传为可用能力。

## 3. 当前基线与剩余缺口

当前版本 `0.11.11` 已完成 WebDAV 后端主链路：

- HTTPS 配置、凭据加密、连接测试和目录浏览。
- PROPFIND 递归扫描。
- MOVE dry-run、真实重命名和远程写锁。
- 回滚计划反向 MOVE。
- 失败恢复 API 和恢复状态机。

稳定版前还需完成：

1. 前端仍存在“WebDAV 暂不支持真实重命名”的过时文案。
2. 失败恢复只有后端接口，用户无法查看远程操作明细或主动恢复。
3. 缺少真实 WebDAV 协议集成环境和可重复的验收脚本。
4. M11、总设计、用户手册和部署说明仍包含 SFTP / S3 的实施计划或过时能力描述。
5. 缺少 M11 验收清单、验收报告和 `v1.0.0` 发布材料。

## 4. 后端收口设计

### 4.1 远程操作列表

新增远程操作分页查询接口：

```http
GET /api/remote-operations
```

查询参数：

- `page`：页码，默认 1。
- `page_size`：每页条数，默认 10，最大 100。
- `media_source_id`：媒体源筛选。
- `operation_type`：`rename` 或 `rollback`。
- `status`：`pending`、`recovering`、`completed`、`failed`、`recovery_required`。

响应：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 10
}
```

现有接口保持不变：

```http
GET  /api/remote-operations/{item_id}
POST /api/remote-operations/{item_id}/recover
```

列表和详情只返回脱敏后的媒体源、路径、状态和恢复信息，不返回密码、Token 或解密后的连接上下文。

### 4.2 恢复规则

恢复继续使用现有状态机：

- 已完成：幂等返回，不重复 MOVE。
- 源存在且目标不存在：重新执行禁止覆盖的 MOVE。
- 源不存在且目标存在：认为远端 MOVE 已完成，仅补齐本地数据库。
- 源和目标同时存在，或同时不存在：标记 `recovery_required`，停止自动写入。
- 有效媒体源写锁存在：返回 HTTP `409`。

恢复成功后同步：

- `remote_operation_items`。
- `media_files`。
- `rename_previews`。
- `rename_operations` 和 `rename_operation_items`，或 `rename_rollback_plans` 和 `rename_rollback_items`。
- 已存在的增量扫描索引。
- 审计事件和操作结果。

## 5. 前端收口设计

### 5.1 入口位置

在“任务治理”页面增加两个 Tab：

- 任务列表。
- WebDAV 恢复。

默认进入“任务列表”，不改变现有用户操作路径。WebDAV 恢复使用独立条目列表，不参与任务归档和恢复语义。

### 5.2 WebDAV 恢复列表

筛选项：

- 媒体源。
- 操作类型。
- 恢复状态。

表格列：

- 操作 ID。
- 媒体源。
- 操作类型。
- 源路径。
- 目标路径。
- 状态。
- 失败原因。
- 更新时间。
- 操作。

源路径、目标路径和失败原因单行截断，悬停显示完整内容。状态使用现有状态标签颜色。列表使用项目统一分页组件，初次进入页面不产生额外滚动条。

### 5.3 恢复交互

`pending`、`failed`、`recovering`、`recovery_required` 状态显示恢复按钮；`completed` 只允许查看详情。

点击恢复前弹出警告确认：

> 系统将检查 WebDAV 源路径和目标路径，并在状态明确时重试 MOVE 或补齐数据库。状态冲突时不会自动修改远端文件。是否继续？

恢复期间禁用该行按钮并显示加载状态。完成后刷新当前页：

- `retried`：提示“WebDAV MOVE 已安全重试并完成恢复”。
- `reconciled`：提示“远端文件已移动，数据库状态已补齐”。
- `409`：显示后端返回的锁冲突或人工恢复原因。

### 5.4 文案修正

媒体源页面和用户手册统一描述 WebDAV 已支持连接、浏览、扫描、dry-run、真实重命名、回滚和失败恢复。

不再使用“暂不支持真实重命名”“最小闭环”等开发阶段文案。

## 6. WebDAV 集成验证设计

新增独立测试环境，不接触用户真实媒体目录或生产凭据：

- 测试 WebDAV 服务使用容器运行。
- 测试目录使用仓库忽略的临时目录或容器卷。
- 测试证书由临时测试 CA 签发，测试进程显式信任该 CA，不关闭证书校验。
- 测试账号和密码只存在于测试环境变量或临时配置，不写入普通日志。

集成场景：

1. HTTPS 和 Basic 认证连接成功。
2. 错误密码连接失败。
3. 目录浏览返回预期目录。
4. 递归扫描读取媒体文件和 ETag。
5. MOVE dry-run 检测目标冲突。
6. 真实 MOVE 成功且禁止覆盖。
7. 反向 MOVE 回滚成功。
8. 模拟远端已移动、本地未提交，恢复时只补齐数据库。
9. 源和目标同时存在时进入 `recovery_required`。
10. 并发写锁阻止重复恢复。

本机当前没有 Docker，因此设计和脚本可以在本地完成静态检查；真实容器验证必须在 GitHub Actions 或 fnOS Docker 环境运行。稳定版发布门槛要求至少一个受控环境完整通过上述集成场景。

## 7. 文档收口

稳定版前必须同步：

- M11 WebDAV-only 开发计划。
- M11 设计手册。
- M11 验收清单。
- M11 验收报告。
- M11 WebDAV 用户手册。
- 完整用户手册。
- WebDAV 部署与兼容性说明。
- README 当前能力、支持边界、版本和路线图。
- 系统设计和总设计。
- `v1.0.0` 发布说明与工作日志。

FTP、FTPS、SFTP、S3 / MinIO 只在“未来候选能力”中说明，不再出现“下一阶段实施”或已承诺排期。

## 8. 稳定版验证和发布门槛

发布前必须全部通过：

```powershell
npm.cmd run backend:test
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
npm.cmd run release:package
```

同时要求：

- WebDAV 协议集成测试通过。
- `docker compose config` 和 GHCR 部署 Compose 校验通过。
- 前端手动验证媒体源、扫描、重命名、回滚和 WebDAV 恢复入口。
- 发布包只包含 `config.example.toml`，不包含正式 `config.toml`、数据库、日志或测试凭据。
- `develop` 提交并推送。
- 合并到 `main`。
- 创建 `v1.0.0` 标签。
- 创建 GitHub Release 并上传前端发布包。
- GitHub Actions 成功生成 `v1.0.0` 和 `latest` GHCR 镜像。
- 发布后重新核对 GitHub Release、镜像标签和部署文档。

## 9. 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| 网络中断导致重复 MOVE | 幂等明细、源目标双向检查、媒体源写锁 |
| 远端已移动但数据库未提交 | `reconciled` 路径只补齐数据库，不重复 MOVE |
| 两端文件状态不明确 | 标记 `recovery_required`，禁止自动写入 |
| 证书或凭据泄露 | 仅 HTTPS、系统信任链、凭据加密、日志和 API 脱敏 |
| 前端误触恢复 | 明确确认弹窗、行级加载锁、后端权限和写锁 |
| 集成测试污染真实文件 | 独立容器、临时目录、专用测试账号 |
| 稳定版文档夸大能力 | 明确 Basic/Bearer 和受信任证书边界，未实现协议只列为候选 |

## 10. 完成定义

满足以下条件后，M11 和产品功能开发阶段视为完成：

- WebDAV 用户可从前端完成配置、连接、扫描、预览、重命名、回滚和失败恢复。
- WebDAV 集成测试在受控环境通过。
- 全量自动化测试、构建、编码和打包检查通过。
- 文档只描述实际可用能力。
- `v1.0.0` GitHub Release、发布包和 GHCR 镜像发布并验证完成。
- 路线图转为维护和优化，不再承诺 FTP、SFTP 或 S3 实施时间。
