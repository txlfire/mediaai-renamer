# WebDAV 部署与兼容性说明

类型：部署与维护手册。适用v1.3.0、schema39；安全与后台改造自v1.2.0交付。下文旧schema编号表示历史引入点。已验证范围见[项目状态](../development/project-status.md)，本文不代表所有NAS端点均支持安全写入。

## 1. 地址和根路径

- 只支持 `https://`。
- 地址必须指向实际 WebDAV 根目录或子目录，例如 `https://nas.example.com/dav/movies/`。
- 建议保留末尾 `/`，避免反向代理重定向改变 `Destination`。
- 不要把浏览器管理页面 URL 当作 WebDAV URL。

## 2. 认证

- 无认证：用户名和密钥均留空。
- Basic：填写用户名和密码。
- Bearer：用户名留空，密钥填写 Token。
- 使用只允许访问目标媒体目录的独立账号，不使用 NAS 管理员账号。

## 3. TLS

系统始终验证服务器证书：

- 公网证书应包含完整证书链和正确主机名。
- 私有 CA 或自签名体系必须把 CA 证书加入后端运行环境信任链。
- Docker 可通过镜像或宿主挂载安装 CA；安装方式取决于基础镜像和 NAS 平台。
- 产品不支持 `verify=false`、忽略证书错误或自动信任首次连接。

## 4. 反向代理

至少保证以下能力不被拦截：

- 方法：`OPTIONS`、`PROPFIND`、`MOVE`。
- 请求头：`Authorization`、`Depth`、`Destination`、`Overwrite`、`If-Match`；响应需保留文件ETag。
- WebDAV XML 响应体和 `207 Multi-Status`。
- 足够的连接超时和路径长度。

若代理重写 Host、Scheme 或路径前缀，必须确保 PROPFIND 返回的 `href` 与外部 HTTPS 地址可正确对应。

当前安全约束：自动重定向被拒绝，请配置最终 HTTPS 地址（包括必要的末尾 `/`）；href 必须与请求地址同源、同有效端口并在请求目录内。源目录浏览、扫描和 MOVE 进一步限制在配置根目录内。URL 不允许内嵌用户名/密码、查询参数、片段或编码目录跳转，认证使用独立字段。

## 5. 防覆盖和恢复

- MediaAI Renamer 的 MOVE 固定使用 `Overwrite: F`。
- 服务器必须在目标已存在时拒绝覆盖，通常返回 HTTP 412。
- 连接/浏览成功后仍需在授权目录验证当前端点的条件MOVE；强ETag/If-Match不足或验证未通过时保持只读，不以普通MOVE成功放宽门禁。
- 同一媒体源使用数据库租约锁防止并发写入。
- 网络中断后到“任务治理 -> WebDAV 恢复”处理。
- 源和目标状态有歧义时系统不会自动写入，需要先在 WebDAV 服务端确认文件状态。

## 6. 上线前检查

1. 使用专用测试目录和测试账号。
2. 测试连接和目录浏览。
3. 扫描一个小型测试目录。
4. 对测试文件执行预览、dry-run 和重命名。
5. 验证同名目标不会被覆盖。
6. 执行回滚。
7. 查看审计、操作日志和 WebDAV 恢复列表。
8. 确认所有步骤后再接入正式媒体目录。

## 7. 凭据迁移与备份

以下适用于从旧版本升级至当前稳定版；先停止目标实例并按[部署手册](fnos-ghcr-docker.md#9-历史版本升级与回退边界)准备备份。

1. 停止服务，单独备份数据库和已有密钥；不要把密钥加入源码、发布包或普通日志。首次从旧 v1/v2 升级必须保持原 database_path 字符串，旧密文依赖该路径。
2. 使用 `pip install --require-hashes -r backend/requirements.lock` 安装锁定依赖。密钥来源优先级为环境秘密 `MEDIAAI_CREDENTIAL_KEY`、`[paths].credential_key_file`、默认 `data_dir/secrets/credentials.key`。环境值需为 Fernet 格式的独立随机密钥，不是密码或数据库路径。
3. 启动服务：旧媒体源、元数据Provider与敏感热配置迁移为v3（自schema21引入），当前数据库终点为schema39；包含扫描策略/操作者、后台授权、共同文件写槽、外发预算、迁移及探测/匹配任务。旧任务不猜历史操作者或进程。迁移失败保留原数据并阻止启动，不能删库或清空凭据绕过错误；早期v1.0.1包不包含这些后续能力。
4. 备份新数据库与原独立密钥，分开存储。Unix 默认新建密钥文件权限 0600；Windows 应用服务账号及管理员限制 ACL，默认文件权限不等于 Windows 访问控制。默认密钥目录与数据库位于同一 data_dir，分离挂载需配置 credential_key_file。
5. 在隔离目录恢复数据库和同一密钥，再启动并测试脱敏配置读取。v3 数据库可改变路径；缺密钥或密钥不匹配应明确失败，不能生成替代密钥。需更换密钥时先设计受控重加密，直接改环境值不能完成轮换。

回退必须恢复升级前数据库及原配置。旧程序不认识 v3 密文，不能直接打开升级后数据库。原日志、轮转压缩包和旧备份可能仍含历史凭据，读取脱敏不等于磁盘擦除；须按保留策略受控清理，涉及真实账号时应更换已暴露凭据。

可通过 `MEDIAAI_CONFIG_PATH` 指定隔离配置进行验证，或使用本机命令已有的 `--config` 参数；显式参数优先。环境配置文件不存在时保持现有默认回退行为，因此部署前必须检查文件实际存在且指向正确实例。

## 8. 历史 URL 凭据脱敏

本机维护工具默认只预览计数，不输出原始日志、账号或密码，不导入服务入口或执行数据库初始化。必须传入实际存在的实例配置，不能用缺失配置的默认回退维护其他实例。

```powershell
$env:PYTHONPATH = "backend"
# 只预览，不改日志或数据库内容。
.\.venv\Scripts\python.exe -m app.redact_history --config config/config.toml
# 停止目标实例后，显式执行；工具本身不负责停服。
.\.venv\Scripts\python.exe -m app.redact_history --config config/config.toml --apply --confirm-service-stopped
```

预期结果：JSON 输出 apply、databaseRows、matchedFiles、changedFiles、skippedFiles、failedFiles、auditStatus。预览中的 databaseRows/matchedFiles 为命中数量，changedFiles 为 0；执行后 changedFiles 为实际替换数量。成功后重跑应无内容命中，维护审计仍会记录每次执行。

范围与限制：

- 仅替换 HTTP(S) URL 内嵌 userinfo；保留 URL 主机和路径。维护 audit_events 的 summary/detail_json、operation_logs 的 message/detail_json，保留 ID、任务关联、时间及状态，并重算运行日志字节数。
- 日志目录优先读取数据库 logging.path，否则使用实例 logging.log_dir。只处理 app.log、batch.log、error.log、llm.log 及其轮转后缀，以及 archive 下同名 ZIP；不遍历其他目录、不提取 ZIP、不改媒体文件和业务历史路径。
- 单文件默认 128 MB，可用 `--max-file-mb` 指定 1-1024 MB；单行上限 1 MB，ZIP 展开总量受同一文件上限约束，最多 256 个条目。GZ、非 UTF-8、超限、非日志条目、加密 ZIP、符号链接等跳过；目录或文件访问错误计入失败。原文件保留，不默默截断或删除。
- 数据库分批读取、同事务更新并写入维护意图；每个文件使用同目录临时文件原子替换，保留权限与修改时间。数据库与所有文件不是一个全局事务，文件失败时数据库脱敏可能已经完成，不能按全局回滚处理。
- Windows 在写临时文件前通过系统 Get-Acl/Set-Acl 复制访问控制，复制失败不替换原文件；后台子进程隐藏、逐文件执行且限时，不弹出窗口。需要系统 Windows PowerShell Security 模块可用，不自动安装额外依赖或退化为放宽权限。其他平台保留基本文件 mode；特殊 ACL/所有者及非本机文件系统仍需现场核对。
- skippedFiles/failedFiles 非零或审计不可用/完成写入失败时命令返回 1。auditStatus=completion_failed 表示维护可能已生效，但完成审计失败，应核对 started 记录及报告后重试；数据库锁或停服确认缺失会在文件处理前拒绝执行。
- 同一日志目录应归属此实例，执行前核对配置与授权范围。旧备份、非标准压缩包、数据库 WAL/空闲页、文件系统旧块和曾导出的副本不保证安全擦除；历史业务路径仍需专项迁移。曾暴露真实凭据时应更换凭据，不以脱敏工具代替密钥轮换。

历史脱敏工具已有合成数据验证；正式实例清理必须由操作者停服、核对预览后显式执行。此维护不改变原保留期/大小治理设置，也不代替定期日志清理。

## 9. 干跑与条件重命名

重命名干跑保存预览、媒体源和文件快照。执行前快照变化、源禁用或预览排除时，返回失败原因并要求重新干跑，不执行旧目标。

WebDAV 通过 PROPFIND 取得文件 ETag、大小和修改时间；干跑需要强 ETag，弱 ETag 或缺失 ETag 返回“无法安全执行重命名”。此限制不影响目录浏览及只读扫描。MOVE 使用 `If-Match: <干跑时的 ETag>` 与 `Overwrite: F`；服务端版本变化或目标已存在应拒绝写入。部署验收必须核对实际服务是否遵守条件头，不能仅以连接成功作为可安全重命名的证据。

远程执行状态区分 pending（意图已记录）、remote_applied（远端已移动、业务待同步）及 completed（业务已提交）；任务页显示“已移动待同步”。数据库提交故障保留恢复状态，不把远端动作当作回滚成功。旧 schema 21 干跑计划没有安全快照，升级到 22 后必须重新生成干跑；升级不自动执行旧计划。

恢复自动重试必须有匹配的源版本；远端已移动的补齐必须核对目标版本（没有目标版本时核对原源版本）。历史记录无版本或版本变化时提示人工处理，不只凭目标路径存在认定成功。移动后的实际元数据和增量索引与业务记录一起提交。

回滚干跑快照、本地中断恢复及历史completed只读一致性检查已交付；Windows隔离中断和标签CI已有证据，不代表每个NAS端点或正式实例已验收。

本地动作在schema23增加持久化意图，24增加跨盘目标身份，25增加回滚干跑快照；WebDAV仍使用远程状态，不通过本地维护命令恢复远程URL。升级前备份库与密钥，旧计划重新干跑。具体核对命令见[完整手册](../manuals/MediaAI-Renamer-用户手册.md)；真实NFS不可中断状态、实际跨盘和断电场景须单独验收。

## 10. 文件动作审计补写

自schema27起，重命名/回滚执行先记录请求意图，远端恢复也消费同一意图表。初始审计不可用时拒绝执行；完成审计失败时返回实际结果及 `X-MediaAI-Audit-State: pending`，页面提示审计未完成，不应因此重复文件操作。

```powershell
$env:PYTHONPATH = "backend"
# 默认只读，必须指定存在的目标实例配置；不会初始化数据库。
.\.venv\Scripts\python.exe -m app.recover_file_audits --config config/config.toml --limit 200
# 停止目标实例后，仅补写审计，不重放重命名或回滚。
.\.venv\Scripts\python.exe -m app.recover_file_audits --config config/config.toml --apply --confirm-service-stopped --limit 200
```

预览输出待补写意图 ID、任务类型/ID、是否已有结果快照及错误类型，不输出原始请求、路径或凭据。执行输出 completed/pending/has_more/next_id；pending 或 has_more 非零返回 1。has_more=1 时用 `--after-id <next_id>` 继续本轮，下一轮从 0 开始复核此前未完成意图，避免运行中的旧任务阻挡后续记录。limit 为 1-5000。

未捕获本次请求结果的意图继续保留pending，即使对应业务任务已是终态。任务状态可能属于旧请求或另一执行者，补写不据此推断当前请求成功；已捕获的请求结果使用脱敏快照。请求被拒绝时审计记failed，详情的计数只作为当时业务快照，不归为该请求的执行数量。任务中断需先完成相应文件/业务恢复；进程崩溃或数据库不可用导致结果未保存时，保留初始意图等待人工核对。最终事件和意图completed同事务，重跑不重复生成最终审计。

远端恢复意图有保存结果时，补写保留原请求动作和结果，不调用MOVE或恢复函数。缺少本次请求结果快照时继续pending：即使业务条目已completed，也可能属于旧请求或另一个执行者，不能提前把本次请求记成功。进程崩溃且结果未保存的意图需人工核对，本命令不猜测或重放。

该命令不负责停服、文件恢复或清理日志。当前版本已有有界周期补写，每轮最多200条按游标推进；意图容量/已知终态保留有自动化证据，未知/pending不删除。审计独立保留政策仍待确认；容量边界见项目状态，合成测试不代替正式实例维护验收。

## 11. 异步扫描与遗留槽核对

扫描使用202受理与真实任务查询，旧路径 `POST /api/scan-jobs` 也返回202排队结果，不再承诺同步完成。后台使用隐藏隔离进程，同库一次执行一个异步扫描；`[scan] task_timeout_seconds = 3600` 控制总预算，不是逐次NFS调用硬超时。超过预算或停服先标记中断，确认进程退出后释放槽，不自动重放。

应用异常退出后，过期租约保留执行PID和槽位。先停止目标实例，再核对遗留任务：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.recover_scans --config config/config.toml --limit 100
# 只针对预览中的指定任务；工具再次确认PID已不存在后才释放槽位。
.\.venv\Scripts\python.exe -m app.recover_scans --config config/config.toml --apply --confirm-service-stopped --job-id 123
```

默认预览使用只读连接，不初始化数据库；输出id/status/execution_pid/execution_expires_at、has_more和next_id，不输出执行token。超过一页用`--after-id <next_id>`继续，limit允许1-1000。核对范围为保留槽的interrupted/completed/partial_completed/failed任务。进程仍存在、查询权限不足、PID未知或任务仍在运行时拒绝释放；错误退出码1，缺少执行门禁为参数错误。释放只清除执行槽：中断任务仍为interrupted，不恢复旧扫描；完成、部分完成、失败任务保留原状态、计数和业务摘要。

业务状态已完成不等于执行进程已退出。包括终态在内，只要存在未释放的执行token，同库就不认领下一个异步任务；监督者确认退出后释放，异常退出遗留槽按上述命令核对。不用变更任务状态或强制清空token绕过进程检查。

该命令不终止进程、不修改媒体、也不负责停服。Linux NFS内核阻塞可能延迟进程退出，此时保留槽位并继续检查挂载/进程，不通过强制清空数据库绕过。Windows合成目录子进程及自动化回归通过；真实NAS、Linux不可中断状态和组合缩放尚未验收。

## 12. 开发实例启动与外发结果核对

Windows先运行 `npm.cmd run dev:windows -- status`，仅停止时start，不重复启动；stop/restart需明确停服意图。启动成功要求前端页面、后端/api/health和前端代理/api/health均200；后台不弹窗口，热更新只观察backend/app。停止先核对根进程/PID创建时间并清理其后代，未知进程占端口时不强杀。具体见[开发指南](../development/development-guide.md)。

桌面宿主退出可能连带结束由它启动的开发服务，不将开发脚本视为系统级守护服务。恢复后核对健康和任务意图，不自动重放中断的扫描、文件操作或外发。未取得最终摘要的测试必须重跑。

外发覆盖使用一次性授权，查询只读；明确拒绝为failed，网络/结果快照缺失为unknown。后台只补写已保存结果的审计，不再访问AI/TMDB；未知需人工核查。授权过期/容量准入和有界核对CLI已交付，人工核对不自动重发、不删除pending。升级前核对schema39、库/密钥备份和验收门禁；具体工具见[用户手册](../manuals/MediaAI-Renamer-用户手册.md)。

## 13. 专用目录集成验收

前提：用户批准专用可写目录和账号，HTTPS证书通过信任链及主机名校验。测试环境安全配置以下变量，不把密码写入文档、命令历史或日志：

- `MEDIAAI_WEBDAV_TEST_URL`：专用目录的最终HTTPS地址，不带内嵌凭据或跳转。
- `MEDIAAI_WEBDAV_TEST_USERNAME`、`MEDIAAI_WEBDAV_TEST_PASSWORD`：测试账号，通过本机秘密渠道设置。
- `MEDIAAI_WEBDAV_TEST_CA_CERT`：信任CA证书路径；同时将同一信任链提供给产品协议使用的 `SSL_CERT_FILE`。不能设置忽略证书校验。
- `MEDIAAI_WEBDAV_INTEGRATION=1`：仅在以上条件和测试范围确认后设置；否则10项受控集成默认跳过。

在已配置环境运行，不使用正式实例数据库：

```powershell
.\.venv\Scripts\python.exe backend/run_tests.py integration.test_webdav_e2e -v
```

预期：连接/认证、浏览/扫描、条件MOVE/禁止覆盖、逆向MOVE、恢复/租约共10项执行。每项只在专用目录下创建随机 `case-<UUID>` 子目录，先确认不存在再创建；只清理本轮确认新建的子目录，删除后再独立PROPFIND确认404。临时业务数据库自动清理，不更改正式配置或已有媒体。

错误：证书/认证/权限或根范围错误时停止，不改用HTTP或跳过校验；弱ETag等能力不足据实失败，不放宽文件安全判断。清理失败或创建结果未知必须保留失败提示和临时目录位置，由操作者只读核对后决定处理，不能把“测试结束”当“全部还原”。此入口已有本地模拟安全回归，但不是实际NAS10项通过证明。
