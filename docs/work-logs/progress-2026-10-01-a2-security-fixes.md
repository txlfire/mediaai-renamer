# A2 远程访问边界与凭据安全

日期：2026-10-01
类型：实施与验证记录
状态：A2 核心实施与全量自动化通过，历史磁盘清理及实机验收待办

## 范围与检查点

用户确认继续 A2，并允许必要时实际启动服务。沿用已确认 P04，不读取或输出真实凭据，不提交、升级版本或发布。保留 A1 和用户原有工作树修改。

- A2-1：WebDAV 同源、端口、根目录、href、MOVE 目标和重定向边界；拒绝 URL 内嵌凭据，控制错误返回。
- A2-2：独立随机密钥与成熟认证加密，迁移媒体源 v1/v2、元数据 Provider 密文及 system_settings 中 AI/TMDB 明文凭据，备份恢复和日志历史脱敏。

实际实现发现：metadata_provider_configs 复用 media_source_secret；system_settings 的 ai.provider_profiles 内含 API Key，不能只加密媒体源表。当前数据库 schema_version=20，迁移失败旧数据保留与密钥丢失处理必须独立验证。A2-2 不能提前标为修复。

## A2-1 决策

保留现有 HTTPS 约束；拒绝 userinfo、控制字符、反斜线、查询/片段以及编码路径逃逸。href 必须同 scheme/host/有效端口且在请求目录内；带凭据请求进一步受配置根目录限制。MOVE 源/目标均受根限制，无配置 context 时仅允许源所在目录内目标。

自动重定向统一拒绝（包括同源），使用受控错误提示要求配置最终地址，不自动把 Authorization 转交下一地址。此兼容调整须写入手册。实际运行验证仅使用临时配置/合成数据，不调用真实 NAS 或媒体写操作。

## 恢复入口

先读本日志、A1 日志及 remediation-plan-2026-10-01.md，再核对 git status、实际 diff。R08/R13/R33 均尚未关闭；R34 可读能力误报留在 C3，不混入本批。

## A2-2 实施检查点

- 使用 cryptography Fernet 认证加密，密文前缀 v3，凭据版本 3，schema_version 21；依赖范围 >=46,<51，本机验证版本 50.0.2。参考：[Fernet 官方文档](https://cryptography.io/en/latest/fernet/)。
- 优先环境秘密 MEDIAAI_CREDENTIAL_KEY，其次 paths.credential_key_file，默认 data_dir/secrets/credentials.key。新密钥完整写入临时文件后以硬链接原子发布，不能覆盖并发创建的已有密钥；Unix 临时文件权限 0600，Windows 需要宿主 ACL 管理。
- 新写入及迁移覆盖媒体源、metadata_provider_configs、敏感 system_settings 与整个 ai.provider_profiles。后端读取透明解密，对外 API 路径和成功响应不变，仍返回脱敏值。
- 旧 v1/v2 迁移必须在原数据库路径首次完成（旧算法依赖该路径）；新 v3 恢复只依赖原独立密钥。迁移同事务，失败不提交部分更新；已存在 v3 而密钥丢失不会生成替代密钥，也不清空旧凭据。
- MEDIAAI_CONFIG_PATH 支持隔离启动；测试全量命令使用 .codex/test-artifacts/review-fixes/a2/config.toml，未迁移正式实例。
- 新安全回归：WebDAV 13 项、共享协议原有 15 项、凭据 10 项通过；凭据首轮测试发现 SQLite 上下文不关闭连接导致 Windows 临时目录清理失败，已改用 closing 并复验通过。
- 首轮全量发现 3 个测试仍断言凭据版本 2；已按版本 3 更新，最终全量需要复跑。
- URL 凭据在审计/运行日志写入、历史读取和文本导出脱敏。不擅自删改磁盘历史日志、轮转压缩包、旧备份或历史业务路径；这些残留仍需受控清理，R33 不关闭。

## 最终证据与下一步

- 后端最终全量 359 项，349 通过、10 项 NAS 集成跳过，97.957 秒：`.codex/run-logs/fix-a2-backend-final-2026-10-01.log`。首轮失败日志保留用于追溯，不能作为最终结论。
- 最后媒体源读出保护复验：22 项通过，5.581 秒。测试与验证服务均已结束，9001 不再监听。
- 实际以隔离配置启动 uvicorn 到 127.0.0.1:9001，health=200/status=ok、匿名 media-sources=401；服务随后停止。此为 HTTP 应用运行冒烟，不是 HTTPS NAS 验收。首次后台组合命令被工具策略拒绝，改为终端托管服务并完成验证，无提权或系统设置变更。
- `npm.cmd run check:encoding`、`git diff --check` 通过。媒体源历史路径读出脱敏是全量之后的最后小改动，单独复验媒体源用例，证据写入 `.codex/run-logs/fix-a2-media-sources-final-2026-10-01.log`。
- 对外业务 API 成功载荷不变；内嵌凭据/越界请求被明确拒绝。新配置字段只用于实例配置，不增加页面密钥输入，不回显密钥或密文。项目使用 sqlite3，无 ORM 引入。
- 实际迁移 SQL：`UPDATE media_sources SET encrypted_secret = ?, credential_version = ? WHERE id = ?`；`UPDATE metadata_provider_configs SET api_key_encrypted = ? WHERE provider = ?`；`UPDATE system_settings SET value = ? WHERE key = ?`。均参数绑定，同一数据库事务提交；密钥文件的创建不是数据库事务的一部分，迁移失败应保留已创建密钥以便重试。
- 尚未迁移正式数据库、清理磁盘历史日志或轮转包、验证真实 NAS/TLS 与 UI 缩放，不关闭 R33，也不宣称完成全部审查修复。大包警告、日志性能与 G07 应用导入副作用仍待后续批次。

恢复先读本日志及主计划，核对最近验证结果。历史清理工具已补齐（见下节），下一开发批次为 B1 原子认领及预览快照；不得跳过迁移备份/失败恢复边界。无提交、版本升级或发布。

## 历史残留工具收尾

用户确认继续既定工作。本轮补 `app.redact_history` 与 history_redaction_service，默认只预览；apply 必须显式确认停服，不能把参数确认说成工具已检测或执行了停服。未运行正式实例清理。

已实现：

- 不导入 app.main、不初始化/迁移数据库；配置文件必须存在，数据库以 ro/rw URI 打开，不创建不存在的库。
- 审计及运行日志文本逐 500 行读取，更新保留记录 ID 与业务关联；字节计数重算；数据库更新与 started 审计同事务，初始审计失败不提交，完成审计失败在报告中明确标识。
- 文件按有效日志目录和固定日志命名白名单处理；普通日志及 archive ZIP 有界读取，超过大小/行长/展开量/条目数或编码不支持则保留原文件并计数。符号链接不处理，不解压到磁盘。
- 每文件独立原子替换、保留权限和 mtime；不创建含秘密的自动备份。数据库已提交但文件失败明确作为部分结果，不能伪报整体回滚。
- 不改媒体文件、历史业务路径、密钥文件、原凭据字段或备份；不宣称清除数据库空闲页/WAL、文件系统旧块。R33 保留正式清理、业务历史路径和旧副本待办。

验证证据：

- 首轮专项 12 项通过；补审计故障、ZIP 展开上限及有效日志目录/大小上限后，最新专项 16 项通过，5.847 秒：`.codex/run-logs/fix-a2-history-targeted-2026-10-01.log`。
- 全量 371 项，361 通过、10 NAS 集成跳过，103.478 秒：`.codex/run-logs/fix-a2-history-backend-final-2026-10-01.log`。全量运行早于后补 4 个专项及目录枚举异常的窄修复，后补范围由最新 16 项专项复验；不把 371 数字写成含全部最新用例。
- 实际 CLI 隔离实例预览返回 0：apply=false，数据库/文件命中均为 0，auditStatus=not_written。未执行真实服务停服或正式日志改写。
- 部署说明第 8 节记录命令、计数语义、边界、部分失败与常见错误。源码无新 API/数据库 schema 变更，仍为 sqlite3，无 ORM；新维护 SQL 参数绑定值，表/列来自固定白名单。

B1 已定位入口：rename_operation_service.execute_rename_operation 当前仍先读状态后执行；remote_operation_service.acquire_remote_operation_lock 当前仍先查租约再更新。下一步先加并发认领和过期租约竞争回归，再实现条件更新及 dry-run 快照重检。R09/R11/R14 尚未修复，本轮不关闭。

Windows 收尾补充：写临时内容前复制原文件访问控制，避免临时文件继承较宽父目录权限。使用隐藏、限时的系统 Windows PowerShell Get-Acl/Set-Acl，路径通过环境变量传递，不拼入脚本。首轮继承了运行环境的 PowerShell 模块搜索路径，导致 Security 模块自动加载失败；已改为显式加载系统 PSHOME 下的模块，没有放宽 ACL。相关失败日志保留，不作为完成证据。

最新专项 18 项通过，13.653 秒，包含受保护 DACL 的实际保留验证及权限复制故障保护：`.codex/run-logs/fix-a2-history-targeted-latest-2026-10-01.log`。所有收尾修改后的最终全量 377 项，367 通过、10 NAS 集成跳过，108.158 秒：`.codex/run-logs/fix-a2-history-verified-2026-10-01.log`；旧 371 项/16 项数字保留为历史证据。

知识积累：Windows 的文件 mode 与 DACL 不等价；原子替换前不仅要检查数据完整，还要保护临时文件的权限。跨 PowerShell 版本的模块路径继承可能导致子进程模块自动加载失败，应使用系统模块明确定位，失败保持原文件，不通过放宽权限解决。当前普通 ACL 通过回归，SACL、特殊文件系统属性及 Linux 专项仍是平台验收边界。

本轮最终 `check:encoding` 和 `git diff --check` 通过，全部验证进程结束。新工具不增加 HTTP 入口或前端页面，无前端改动；不提交、不升级、不发布。已同步长期记忆恢复入口。下一开发步骤为 B1，正式清理需另行停服并显式执行，不能默认为已完成。
