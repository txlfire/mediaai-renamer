# 独立代码审查报告（2026-10-09）

文档类型：审查发现记录。只读审查，未修改任何生产代码。

**复核状态**：本文已经 Codex 逐条复核并回应，见 [Codex 复核评估](discussions/assessment-2026-10-08-independent-review.md) 与 [本方回应](discussions/response-2026-10-09-independent-review-reply.md)。**M1、M3、M18 已被判定不成立并撤回；H2、M14、M25 已修订**，下列条目保留原文并就地标注撤回/降级，以便追溯。

**配套实施指令**：[remediation-plan-2026-10-09.md](remediation-plan-2026-10-09.md)（供 Codex 执行，含批次划分、逐条改法、验收断言与「完成定义」）。本文负责"发现了什么"，该文负责"怎么改、怎么验"。

## 0. 本次审查的定位

项目已有 `code-review-2026-09-30.md`（R01-R37）、`design-review-2026-09-30.md`、`observability-review-2026-10-01.md` 和 `remediation-plan-2026-10-01.md` 这一套成熟台账。本次审查不重复该台账，而是做两件它没做的事：

1. 对**当前仍未关闭的高风险项**做独立复核，确认结论是否仍然成立；
2. 审查**台账尚未覆盖的代码**（本轮新增的未跟踪服务、队列、worker、前端工具）。

功能基线取自 `docs/design/project-design.md`（当前目标设计）与 `MediaAI-Renamer-总设计文档.md`（阶段回溯）。所有结论都以该设计基线为准绳。

审查目标：`develop` / `35e3db9` + 未提交改动（132 个已跟踪文件 +9161/-2021，97 个未跟踪源码文件约 1 万行）。应用 1.0.1 / `CURRENT_SCHEMA_VERSION = 34`。

> **基线变更提示（2026-10-11 核对）**：上述 schema 34 与开发库 30 是**本报告成文时的历史值**。此后 Codex 已按本报告执行 X0 与 B1–B4 批次：schema 推进至 **38**（35/36/37/38 分别对应 H4/H8/H9/H11），开发库已迁移至 38，后端套件增长至 **795 项（785 通过 + 10 受控跳过）**。故下文涉及 schema 编号、数据库版本与测试计数的表述均为历史值；**H4/H8/H9/H10/H11 与 M2–M8 的处置状态已由实施计划第 13 节状态表更新**。正文按原样保留以追溯当时的判断依据。

发现分布（初次提交）：11 项高危、30 项中危、7 项低危与工程卫生，另加 5 条"文档↔代码错位"待判定。

**复核后净状态**（Codex 逐条复核 48 条：22 成立／22 部分成立／3 不成立／1 待验证）：

| 状态 | 条目 |
| --- | --- |
| 维持（成立） | H1/H3/H5/H6/H7/H8/H9/H10、M2/M4/M6/M8/M11/M12/M13/M15/M16/M19/M20/M21/M22/M26/M29/M30、L3/L4/L5 等（**H11 见下行，已移出“成立”**） |
| 降级为防御性/待决策 | **H2**（非业务链漏洞）、**H11** 与 **M17**（需产品决策）、M9/M10/M24/M27/M28、L1/L6 |
| **已并发模拟确认** | **H4**：二轮复核以临时库+双线程+模拟 WebDAV 复现“租约过期接管期间发出第二次 MOVE、且迟到写入污染新归属”（`issuedMockMoveCalls=2`；`finalRemoteItemStatus=failed` 而 `finalBusinessItemStatus=renamed`） |
| **已撤回（不成立）** | **M1、M3、M18** |
| 已修订细节 | **M14**（删去"失败前预置 90%"）、**M25**（"控制器完全不记日志"过强） |
| 待验证 | L7（属实例事实，非源码缺陷） |

统计不得跨来源相加：H/M/L 独立于旧台账 R/S/F/D/V/C/G，两者只做映射。

## 1. 已经核对过的既有结论（不重复报告）

以下项经独立复核后**确认代码已实现设计意图**，不再作为缺口列出：

- 认领协议本身正确：`claimScan` / `claimFileTask` / `acceptScan` / `acceptRename` 都是 `BEGIN IMMEDIATE` + 条件 UPDATE；槽位谓词确实是 `execution_token IS NOT NULL`。
- `finishInterruptedScan` 先写业务终态、后清令牌，进程退出未确认时不会让死进程覆盖已完成结果。
- 本地/UNC 不覆盖已有文件：`moveFileNoReplace` 用 Windows `os.rename` 与 Linux `renameat2(RENAME_NOREPLACE)`，并明确拒绝退化成覆盖式改名；跨设备复制的 `target_identity_json` 在删除源**之前**持久化。
- 令牌机制正确：`secrets.token_urlsafe(32)`，只存 sha256，主键哈希查找，改密/禁用/重置均撤销会话。
- 审计与运行日志脱敏覆盖到位；SQL 全部参数化，排序列走白名单。
- WebDAV 的 URL 约束（仅 HTTPS、禁内嵌凭据、禁重定向并拒绝转发 `Authorization`、同源同端口 + 根目录限定）是本仓库最扎实的一段。
- 外部请求预算的每站点 + 同机全局并发、租约语义、失败关闭都正确。
- 监督者在文件任务上的 kill/释放顺序正确（finish(False) → kill → wait → finish(True)），子进程句柄在每条路径上都被回收。
- 一次性外发许可的绑定与单次消费正确；`isStrongEtag` 严格实现 RFC 9110，ETag 无法注入头部。
- `agent.md:95` 的批量语义在重命名/回滚执行器上是满足的（逐项继续）。
- **没有** `async def` 路由，因此不存在阻塞事件循环的问题；`time.sleep` 只出现在线程池线程或隔离子进程中。
- `must_change_password` 不在服务端强制，**不是缺陷**：`remediation-plan-2026-10-01.md` P02 明确采用"每次登录提醒改密，不新增操作限制"，当前实现与已确认政策一致。

## 2. 高危发现

### H1. 扫描执行槽可被同步端点整体绕过

锚点：`backend/app/api/scan_jobs.py:30-47`、`backend/app/service/scan_service.py:593-618`、`backend/app/service/scan_queue_service.py:83`

`POST /api/scan-jobs` 是**仍然注册的活路由**（仅需 `scan:run`），它直接调用 `run_full_scan(...)` 且不传 `acceptedJobId`。于是 `scan_service.py:593` 走"新建任务"分支：

- `scan_service.py:602-618` 插入 `status='running'` 且 `execution_token` 为 NULL 的任务行；
- `scan_service.py:655-657` 因 `acceptedJobId is None` 而**跳过 `requireScanLease`**，后续每一批和最终提交都没有租约；
- worker 的槽位查询只匹配 `execution_token IS NOT NULL`（`scan_queue_service.py:83`），因此这次同步扫描对调度器**完全不可见**。

失败场景（请求线程 R 与 worker 循环 W）：

1. R1 调 `POST /api/scan-jobs/async` → 任务 job2 进入排队；
2. R2 调 `POST /api/scan-jobs`（同一媒体源）→ `scan_service.py:594-597` 的重复检查只查 `status IN ('queued','running')`，job2 此时是 `queued`，检查通过；
3. R2 插入 job3（`running`、无令牌）并开始在 HTTP 线程里遍历媒体目录；
4. W 认领 job2 并派生 `app.scan_worker`。

结果是同一媒体源上两个扫描并发运行，且 job3 无租约——租约过期、停服、撤权、改根路径都无法中断它。这与设计第 7.1 节"受理接口返回 202 和稳定任务ID / 执行使用隔离 worker 及租约"直接冲突。

影响面说明：前端实际走 `acceptScanJob` → `/scan-jobs/async`（`frontend/src/stores/media.ts:99`），所以这不是主流程路径；但它是可被 `scan:run` 持有者调用的公开写路径，缺陷成立。

最小修复：让该端点改为受理（复用 `acceptScan`），或保留同步语义但先经过同一槽位认领。

### H2. WebDAV 条件写入在缺少强 ETag 时静默放行（**已降级：适配器防御性缺口，非业务链漏洞**）

锚点：`backend/app/service/shared_protocols/webdav.py:532-535`、同文件 `514-548`；调用方 `rename_operation_service.py:510`、`rename_rollback_service.py:646`

```python
if context and context.expected_source_version:
    if not isStrongEtag(context.expected_source_version):
        raise ValueError("WebDAV 条件写入需要有效的强 ETag")
    headers["If-Match"] = context.expected_source_version
```

条件头是**可选**的：`expected_source_version` 为空时，代码直接执行不带 `If-Match` 的 MOVE，服务器端没有任何版本校验。而调用方把快照里的 `remoteIdentity[0]`（即 `get_file_info` 返回的 `version`，也就是 ETag）原样塞进去——当服务器不返回 ETag 或只返回弱 ETag 时，该值是 `None`，保护被静默跳过。

失败场景：预览阶段记录了源文件，服务器未提供 ETag（或提供 `W/"..."`）；用户在预览之后、执行之前修改/替换了该文件（或另一进程改动）；执行时无 `If-Match`，服务器照常完成 MOVE，适配器返回成功，调用方把条目记为 `remote_applied`。用户确认的目标文件已经不是被确认的那个内容。

> **降级说明（2026-10-09 复核）**：`_previewSnapshot`（`rename_operation_service.py:128-130`）在快照生成时**已拒绝**缺失/弱 ETag，且 `remoteIdentity` 仅此一处赋值，因此正常重命名/回滚路径不可能带着空版本到达 `webdav.py:532`。本文原引用路径有误。剩余结论改为：`SharedPathContext.expected_source_version` 默认 `None`，`move_file` 对 `None` 静默跳过 `If-Match`，属**适配器契约失败开放**；修法是把它设为 MOVE 的前置条件（fail-closed），价值在于保护未来调用方。

对比：`remote_operation_recovery_service.py:330` 的恢复路径**有**显式检查（`not isStrongEtag(...) or sourceInfo.version != item.source_version` 就抛冲突），说明团队清楚风险，但正向重命名与回滚路径没有同等保护。

设计依据：`project-design.md` 第 7.4 节要求"执行前再次检查……源身份……变化使旧 dry-run 失效"。当前实现对"身份不可知"的情况是放行而非拒绝，方向相反。

最小修复：`move_file` 改为失败关闭——缺少有效强 ETag 即返回明确的"服务端条件写入能力未验证，拒绝真实移动"，而不是继续发 MOVE。

补充（同一缺陷面）：`RemoteProtocolCapability.CONDITIONAL_WRITE` 只在 `webdav.py:333` 与 `registry.py:52,72` 被**声明**，全仓库没有任何运行时消费者；`media_sources.capability_snapshot_json` 是记录"该端点已验证"的天然位置，但创建时固定写 `None`（`media_source_service.py:576`），没有写入方。即设计要求的"能力未验证则拒绝写入"门禁**在代码中不存在**。

### H3. MOVE把HTTP 204当作确定成功，实际结果必须进入待核查

> **现行结论（三轮定稿）**：204说明请求已经到达并得到异常成功响应，但不能单凭状态码证明目标预先存在，也不能证明远端未修改。它必须记录为“请求已发出、结果待核查”并禁止自动重放；与R37端点条件能力验证联合处理。下方原失败场景及“硬冲突”改法仅保留为历史审查过程，不再作为实施依据。

锚点：`backend/app/service/shared_protocols/webdav.py:546`

```python
if status in (200, 201, 204):
    return ConnectionTestResult(True, "WebDAV MOVE 执行成功", readable=True, writable=True)
```

按 RFC 4918 §9.9.4，MOVE 的 201 表示在目标创建了新资源，而 **204 表示源被移动到了一个已存在的目标资源上**。同时请求里带了 `Overwrite: F`，合规服务器在目标已存在时应当返回 412 而不是替换。因此适配器收到 204 并报成功，等于承认"目标预先存在且已被替换"这一情形被当成了正常成功。

失败场景（TOCTOU）：dry-run 在 T0 用 PROPFIND 确认目标不存在（`webdav.py:499-504`，调用点 `rename_operation_service.py:282`）；T1 目标文件出现；T2 发出 MOVE；若服务器不执行 `Overwrite: F`，目标被替换，返回 204，适配器报成功，调用方随后把数据库改写为目标路径（`rename_operation_service.py:530-546`），被覆盖的文件不可恢复。

现有测试全部 mock transport 且只断言 header 字面量、固定 `status = 201`（`test_webdav_security.py:210-226`、`test_shared_protocols.py:253-287`），没有任何用例覆盖 204、目标已存在、或不执行 `Overwrite: F` 的服务器。

历史最小修复（已作废）：~~204视为硬冲突；成功判定改为要求MOVE后PROPFIND复核。~~ 现行方案见[修复计划3.3](remediation-plan-2026-10-09.md#33-h2h3-webdav-条件写入失败关闭--204-视为冲突)：PROPFIND仅辅助核查，不能消除不遵循条件请求的原子性风险。

### H4. 远端恢复在加锁前读取状态，且租约不续期，可重复执行 MOVE

> **现行结论（三轮定稿）**：原文“两条路径都存在且正向检查成功”与代码相反，已作废。第二轮以临时库、双线程和模拟WebDAV确认更窄场景：旧租约过期后新持有者可再次发出MOVE，旧请求迟到写入还能污染新归属。现行状态/代次围栏设计见[修复计划3.4](remediation-plan-2026-10-09.md#34-h4-远端恢复加锁前读状态--租约不续期)。

锚点：`backend/app/service/remote_operation_recovery_service.py:282-289`（无锁读状态）、`:305-313`（之后才加锁，TTL 300 秒）、`remote_operation_service.py:202`（`heartbeat_remote_operation_lock` **定义但恢复路径从不调用**）、`remote_operation_service.py:361-405`（状态写入是无条件 UPDATE）

失败场景：两个管理员并发 `POST /api/remote-operations/{id}/recover`。A 与 B 都在加锁前读到 `status='failed'`；A 拿到锁、通过正向校验、MOVE 在服务端结果不明（源仍在、目标已建）、把行写回 `failed`；A 的 MOVE 因广域网或服务端卡住超过 300 秒且无人续租，租约过期；B 的 `acquire` 走 ON CONFLICT 重获分支成功（`remote_operation_service.py:188-189`），此时正向就绪检查因两条路径都存在而返回成功（`:326`），B 再次调用 `move_file`（`:333`）——同一文件被 MOVE 两次。

现有 `test_remote_operation_recovery.py` 全是顺序用例，没有 ThreadPoolExecutor 或 barrier；带 barrier 的并发测试只验证锁的单一赢家，从不验证赢家在锁内重读条目状态。

历史最小修复（已作废）：~~锁内重读可恢复状态、调用前续租、释放校验令牌。~~ `recovering`本身属于可恢复集合，调用前续租也不能覆盖完整慢调用；必须使用执行代次、全路径CAS和`move_issued`未知不重放规则。

### H5. `GET /api/settings` 缺少权限校验，任意登录用户可读取全部配置

锚点：`backend/app/api/settings.py:121-125`；路由级守卫 `backend/app/main.py:105`（只挂 `require_authenticated_user()`）

`PUT /api/settings` 有 `require_permission("settings:write")`（`settings.py:132`），但同一个 `GET` 没有任何逐路由权限，只有路由级"已登录"。因此一个 `permissions: []` 的最小权限账号即可读取全部 `SETTING_DEFINITION` 值，包括：

- `privacy.custom_sensitive_words` / `privacy.default_sensitive_words`——正是阻止敏感文件名外发的**敏感词表本身**；
- `logging.path`（服务端绝对路径与挂载布局）、`ai.base_url`、`tmdb.*`、`shared.*`、`operations.force_dry_run`。

密钥字段本身已脱敏（`settings_service.py:1530-1535`），所以这是配置泄露而非密钥泄露。但泄露敏感词表本身即是拦截策略的外泄。（**更正**：原文称其与 M3 组合成“完整绕过链”，而 M3 改名洗白链经复核**已撤回、不成立**；此处仅保留“策略外泄”这一成立部分。）

`test_security_boundary.py:94-105` 反而把"最小权限账号可读列表端点"写成了预期行为，且没有任何测试覆盖 `GET /api/settings` 的授权。

最小修复：给该 GET 加 `settings:write`（或对非该权限者裁剪非公开键）。

### H6. 登出后 Pinia 业务状态不清空，下一个用户会看到上一个用户的数据并可对其发起批量动作

锚点：`frontend/src/stores/auth.ts:41-45`、`frontend/src/App.vue:75-78`、`frontend/src/views/RenamePreviewsView.vue:148` 与 `1671-1686`、`frontend/src/api/client.ts`（无 `$reset`）

已核对：`clearSession` 只做 `clearAuthToken()` + `currentUser = null` + `errorMessage`，**不清任何业务 store**；全仓库 `grep '$reset|$dispose'` 无匹配。

失败场景（同一浏览器，先 A 后 B）：

1. 用户 A 登录，打开 `/rename-previews?media_source_id=1&scan_job_id=10`，生成预览 → `previewStore.previews` 与 `previewStore.filters` 被填充；A 登出。
2. 用户 B 在同一浏览器登录，点侧栏"重命名预览" → 路由是 `/rename-previews`，**不带任何 query**。
3. `onMounted` 中 `routeScanJobId = Number(undefined) = NaN`，`Number.isFinite(NaN)` 为假，于是 `refreshPreviews()` **根本不会被调用**（`RenamePreviewsView.vue:1683-1686`），也没有任何清空动作。
4. 表格直接渲染 store 里 A 遗留的行（`pagedPreviews = previewStore.previews`，`:148`），媒体源/任务选择器仍是 A 的值，"查询"可用（`:1754` 只要求 `filters.scan_job_id` 为真）。
5. B 点"全部重命名"→ `readAllTargets` 用 A 遗留的 `filters` 查询 `/rename-previews/selection` → 对 A 的扫描任务发起 dry-run/重命名。

同类问题：`mediaStore.errorMessage` 只在下一次 `startScan` 开始时清（`media.ts:96`），所以上个会话的错误（后端 detail 可能含路径）会在新访问时由 `ScanJobsView.vue:381` 渲染；`mediaStore.scanModeSuggestion` 同理（`ScanJobsView.vue:357-365`）。`pendingFiles`/`renameOperation`/`pagination`/`tableSort` 同样粘滞。

为什么测试没抓到：每个 view 测试都新建 Pinia 实例；`stores/auth.test.ts:122-137` 只断言 `currentUser`/`token`；`App.test.ts:10` 把 auth store mock 成不含 `clearSession` 的对象，真实登出路径从未被执行。没有任何测试覆盖"登出→登录"。

最小修复：新增一个 `resetWorkspaceState()`，在 `logout()`、`clearSession()`、`App.handleAuthExpired()` 中调用，清空 `previews/filters`、`errorMessage/scanModeSuggestion/logItems`、`pendingFiles/currentMoveTask`（并 `stopMovePolling()`）、`currentOperation/currentRollbackPlan`（并 `stopExecutionPolling()`）以及 `pagination/tableSort`；同时让 `RenamePreviewsView.onMounted` 在路由不带 `scan_job_id` 时主动清空预览与筛选。

### H7. 连接测试结果可由客户端伪造并随后被当作"当前已验证"展示

锚点：`backend/app/api/settings.py:206-214`（`POST /settings/tmdb/test-result`）、`:244-251`（IMDb）、`backend/app/service/settings_service.py:932-953`、`:1096-1136`（其中 `is_valid` 被无条件写成 1）；展示侧 `settings.py:230-234` 的 `matches_current`

已核对：这两个写接口接受客户端自述的 `status == "success"`，用**当前**配置算出 `config_snapshot`/`config_hash` 落库；`GET` 只要 `is_valid` 为真且快照一致就返回 `matches_current: true`，前端 `SettingsView.vue:2170-2174` 将其渲染为已核验状态。对比 `POST /api/settings/ai/test` 走服务端自测（`settings_service.py:1030-1070`）——三个 Provider 的契约不对称。

失败场景：持有 `settings:write` 的调用方直接 `POST {"v4":{"status":"success"},"v3":{}}`，从未真正联网测试，页面即显示"与当前配置一致"的通过状态。任何依赖该指示判断"外发链路可用"的运维/自动化会被误导。

严重性说明（如实标注）：这不是提权——能伪造者已持有配置写权限；它破坏的是**验证指示的可信度**。目前未发现该指示被用作运行时放行门禁（已 grep `is_valid`/`matches_current`），所以定级 HIGH 偏保守，若仅视作运维误导则为 MEDIUM。

最小修复：删掉这两个写接口（服务端在 `test_tmdb_connection`/`test_imdb_connection` 内已自行落库），或改为只接受服务端签发的一次性校验令牌。

### H8. 无令牌的 `running` 文件任务会永久锁死整个文件写队列，且无 API/CLI 可解

锚点：`backend/app/service/file_task_queue_service.py:64-67`、`:110-112`、`:176-178`、`:188-189`；产生方 `rename_operation_service.py:431-440`、`rename_rollback_service.py:549-552`

已核对（逐行读过）：

- `assertFileTaskSlotFree` 对三种任务表都跑两组查询——`execution_token IS NOT NULL` **和** `status IN ('running')`——也就是说**只看状态、不看令牌**，任何 `running` 行都占槽（`:64`）。
- 但三个恢复入口都要求令牌非空：`recoverExpiredFileTasks`（`:110`）、`inspectInterruptedFileTasks`（`:177`）、`releaseInterruptedFileTask`（`:189`）。
- 同步执行路径在 `executionToken is None` 时直接 `UPDATE ... SET status='running'`（`rename_operation_service.py:431-433`；回滚同理 `rename_rollback_service.py:549-551`），**全程不写 `execution_token`**。

失败场景：用户走前端同步路径 `POST /api/rename-operations/{id}/execute`（`client.ts:1595`）→ 任务行变成 `running` 且令牌、PID 均为 NULL → 服务在第一个条目探测期间被杀（docker restart / Ctrl+C / OOM / 断电；在卡住的 NAS/NFS 挂载上这个窗口很长，磁盘写满导致最终提交失败也同样命中）→ 此时还没有 `local_file_actions` 行，所以 `recoverLocalFileActions` 找不到任何东西去修复该任务状态。从此**所有类型**的 `claimFileTask` 都在槽位检查处返回 None：重命名、回滚、待处理迁移全部永久排队。三个 `recover_*.py` CLI 因为令牌过滤而列不出这一行，`recoverScans` 那条等价修复（`main.py:67` + `scan_service.py:184-200`）只覆盖扫描，**文件任务没有对应实现**。最终只能手工改 SQL。

为什么测试抓不到：`test_local_file_recovery.py:64-132` 确实杀过遗留子进程，但是在移动**之后**（此时已存在活动动作，修复路径能把任务改回 `completed`）；`test_file_task_queue_scale.py:81-104` 只用带令牌的合成行；全仓库没有任何测试构造"无令牌 + running"的文件任务。

> **最小修复已作废（二轮复核）**：原文“启动时标 `interrupted` 并写未执行”不可采用——启动一个实例不能证明另一进程已退出，更不能证明未发生文件动作。现行改法见修复指令 3.1：统一同步认领持久化令牌/PID、无令牌行**只读可核对**、停服确认后释放、保留未知意图。
>
> （历史原文保留于交流文档，此处不再重复。）

最小修复：~~在启动时补上文件任务版的 `recover_interrupted_scan_jobs`——把三张表中 `status='running' AND execution_token IS NULL` 的行标为 `interrupted` 并写 `error_message`（不重放）；或让 `recoverExpiredFileTasks`/`inspectInterruptedFileTasks`/`releaseInterruptedFileTask` 也覆盖无令牌行（把 `execution_expires_at IS NULL` 视为已过期）。无需 schema 变更。

### H9. 仅大小写不同的重命名（Windows）无法恢复，该文件被永久冻结

锚点：`local_file_recovery_service.py:30-37`（判定"已生效"要求 `sourceIdentity is None` 或 `source_path == target_path` 的**精确字符串比较**，否则返回 `"manual"`）、`:177-180`（apply 循环对 `"manual"` 不做任何事）、`:189`（状态不变）；叠加 `local_file_action_service.py:44-50`（任何处于 ACTIVE_STATUSES 的行都会阻止该文件后续动作）

失败场景：Windows 上 `C:\m\Movie.2024.mkv`，预览 edited_name 为 `movie.2024.mkv`（纯大小写修正，是本工具的常规用法）。dry-run 仍是 `ready`，因为冲突判定用 `source_path.resolve() != target_path.resolve()`，而 `resolve()` 会抹平大小写；`moveFileNoReplace` 执行真实的大小写重命名。若 worker 在移动之后、业务提交之前被杀，行停在 `prepared`/`applied`。此后 `localFileIdentity` 用旧大小写路径仍能取到身份（Windows 大小写不敏感），于是 `sourceIdentity` 不为 None，而字符串上 `source_path != target_path`，`_outcome` 永远返回 `"manual"`：动作行永不关闭，该文件之后每次重命名/迁移都报"文件存在未完成本地动作"，且**没有任何 CLI 能清掉它**——运维也无法靠删除文件解决，因为源和目标本来就是同一个文件。

为什么测试抓不到：`test_local_file_recovery.py` 用的是 `Movie.2024.mkv` → `Movie.Safe.mkv`（名字不同）；`test_pending_files.py:159-184` 固定的是**部分复制**场景的 `"manual"`，那个是可以通过删除残留目标解决的，与"同一个文件"不同（无测试包含 `normcase`/`samefile`）。

> **最小修复已作废（二轮复核）**：原文的 `normcase`/dev+ino 判据**会误判**——复核方实测大小写改名在**移动前与移动后 normcase 均相等**，会把“尚未移动”判为“已生效”，硬链接同理。现行改法见修复指令 4.1：核对**实际目录项拼写 + 动作阶段**，排除硬链接/符号链接/目标替换，证明不足仍人工核查。

最小修复：~~判定"已生效"时按同一性比较，如 `os.path.normcase(source) == os.path.normcase(target)`（或 dev+ino 相等）；并允许修复工具关闭一个源与目标可证为同一文件的 `"manual"` 动作。

### H10. 重复 `POST /execute` 已完成的批次，会写入一条归因给"没做任何文件操作者"的假成功审计

锚点：`file_action_audit_service.py:190-200`（在还不知道本次是否会真正执行前就建意图）、`:101-135`（结果是从**已存业务状态**推导的：`result.status == 'completed'` → `'success'`）；`api/rename_operations.py:57-65`（只短路 `{queued,running}`）；`rename_operation_service.py:402-403`（非 dry_run 且无令牌时直接返回既有结果，不做文件操作）

失败场景：用户 A `POST /execute` → 文件被改名，审计记 `success`（A）。用户 B 对同一 id `POST /execute`：状态 `completed` 通过 API 守卫 → 提交意图 #2 与一条 `pending` 审计行 → `execute_rename_operation` 未认领、未做任何文件 IO 就返回已完成结果 → `_tryComplete(executionResult=<A 的结果>)` → `_executionAuditResult('completed') = 'success'` → 落库一条 `rename.execute/result=success/actor_name=B` 且携带 A 的计数。审计链条因此断言 B 执行了这次变更。

对比正确模式：`rename_queue_service.py:63-71` / `acceptRename` 把意图创建放在**认领事务内**。

最小修复：让审计意图归属于赢得认领的那次请求（在 dry_run→running 事务内创建，如 `acceptRename` 所做），或让遗留执行路径向 `runAuditedFileAction` 报告"未执行"，从而不落成功事件。

### H11. 没有 `outcome_json` 的审计意图永远无法收敛（**部分成立；容量政策属待决策**），会持续占用有界容量并最终拒绝所有新文件请求

锚点：`file_action_audit_service.py:94-95`（result 与 reason 都缺时返回 False）、`:220-225`（`flushFileActionAuditIntents` 永远不带二者调用 `_tryComplete`）；`recover_file_audits.py:34`（因此永远退出 1）；预算在每次变更前强制（`audit_intent_retention_service.py:22-30`，默认 `audit_intents.max_records = 20000`），而清理只删 `status='completed'` 的行（`:45-51`）

失败场景 A（确定性、不需要故障注入）：`POST /execute-async` 与 `POST /execute` 竞争。`/execute` 读到 `status='dry_run'`；`acceptRename` 赢得写并把状态改成 `queued`；`execute_rename_operation` 返回 queued 的批次；`completeFileActionAuditIntent` 命中 `:104-105` 并返回 False，**且不落 `outcome_json`**；该请求已经返回，之后没有任何东西会再来看这个意图。
失败场景 B：`completeFileActionAuditIntent` 的 `BEGIN IMMEDIATE` 遇到 SQLITE_BUSY，或 `_taskResult` 抛错 → 同样只记 `last_error_type`，无 `outcome_json`。

两种情况下 `inspectFileActionAuditIntents` 都报 `hasOutcome=false`，CLI 永远清不掉；一旦待处理意图达到预算上限，`ensureAuditIntentCapacity` 会在**任何文件动作之前**拒绝所有新的重命名/迁移/回滚请求。

> **最小修复已作废（二轮复核）**：原文的“传哨兵 reason 按当前快照固定结果”**违反** P07 与 `project-design.md:313` 的“未知不推断”。现行改法见修复指令 4.3：区分**拒绝／可证未执行／真实未知**三态，前两者自动收敛，第三态需**独立授权的人工关闭**（证据+原因+审计）；容量政策需先决策。

最小修复：~~给 flush 路径一个显式的重算/放弃模式（例如传一个哨兵 reason 使 `:94-95` 不被走到，按当前快照固定结果），或增加专门的 `unknown`/`abandoned` 终态，使 CLI 能收敛并释放容量。

## 3. 中危发现

> **已撤回（2026-10-09 复核）**：`_publishScanStage`（`scan_service.py:453-454`）自身先 `commit()` 再 `BEGIN IMMEDIATE`，而 `:830` 调用它、`:832` 的 `requireScanLease` 与最终状态写入都在**同一个写事务**内，`recoverExpiredScans` 的写无法插入其中。原竞态场景不成立。

### M1. 唤醒/认领后的状态机空洞：`recoverExpiredScans` 仅凭租约时钟判定中断，无进程检查

锚点：`backend/app/service/scan_queue_service.py:128-133`

把任何 `running` 且 `execution_expires_at` 过期的行翻成 `interrupted` 并写 `ended_at`，但**保留令牌**，注释说"执行槽保留，需确认原进程退出"，代码并未做该确认。子进程只在下次心跳/每批复核时（约 5 秒节奏）才知道租约没了；若它正处在 `scan_service.py:832` 的复核与 `:873-897` 的最终提交之间，就会在行已被标 `interrupted` 之后提交 `completed` 与计数。监督者随后杀掉它并调 `finishInterruptedScan(processExited=True)`——而该函数按设计保留业务状态，于是审计说的是"过期中断"，用户看到的状态/计数却是 completed。

最小修复：恢复前检查 `execution_pid` 存活（`scan_recovery_service.isProcessAlive` 已有），或把租约复核并入最终状态写入的同一事务。

### M2. 扫描失败处理中的二次校验失败会吞掉真实失败原因

锚点：`backend/app/service/scan_service.py:898-904`

except 分支先 `rollback()`，再重新 `BEGIN IMMEDIATE` 并调 `requireScanLease`。若这步抛错（租约刚好过期或被撤），`:905-924` 的状态写入与 `:925` 的审计都不会执行，原始异常被替换。任务于是停在 `running`、无 `error_message`；后续 `recoverExpiredScans` 把它标成带通用租约原因的 `interrupted`，真实失败原因永久丢失。

> **已撤回（2026-10-09 复核）**：① `PUT /rename-previews/{id}` 要求 `rename:execute`（`api/rename_previews.py:184`），非本文所写 `metadata:submit`；② 更关键的是拦截记录只会在敏感词**已命中** `file_name`/`file_path`/解析标题时产生，而 `_snapshot`（`external_submission_authorization_service.py:87-98`）要求解析标题与 `block.match_title` 一致，改 `target_name` 不影响这三个字段，因此不存在“把敏感词洗进原本无敏感词的载荷”。整条链自相矛盾。
>
> 替代的**真实但更窄**问题：block 创建后、`prepare` 之前若运营方收紧了敏感词策略，`_snapshot` 只校验指纹、不重跑当前策略，属策略漂移，待确认是否需处理。

### M3. 敏感词拦截只在外发提交时生效，拦截后改名可"洗白"进一次性覆盖通道

锚点：`external_submission_authorization_service.py:87-98`、`external_submission_guard.py:248-250`、`rename_previews.py:184-196`（可改 `target_name`）

`_snapshot` 的复核只比对新旧快照指纹（`block.file_name == row["file_name"]` 等），**从不重跑敏感词匹配**；而 `check_external_submission` 在带 `authorization` 时直接返回 `allowed=True`。

失败场景：①`POST /api/rename-previews/{id}/metadata-match` 因文件名命中敏感词被拦截并写 `blocked` 记录；②仅有 `metadata:submit` 的用户 `PUT /api/rename-previews/{id}` 把 `target_name` 改成命中敏感词的名字；③`prepare` → `submit` 把该文件名发给 TMDB/AI，敏感词从未被重新评估。

这与 `project-design.md` 第 10 节"文件名、路径或解析标题命中敏感词时，不得提交到 TMDB、IMDb、后续刮削源或 AI"冲突。

最小修复：`_snapshot` 在签发许可前，始终对当前 `file_name`/`file_path`/parsed title 走一次常规敏感词匹配；命中且不在原批准集合内即拒绝（而非覆盖）。

### M4. 并发 `create_remote_operation_item` 抛异常而不是返回既有行

锚点：`backend/app/service/remote_operation_service.py:265-295`

先 SELECT 再 INSERT，无 `BEGIN IMMEDIATE`、无 `ON CONFLICT`/`INSERT OR IGNORE`。两个线程用同一 `idempotency_key`（如 `rename-operation:1:item:1`）会都查不到、都插入，第二个在唯一约束上抛 `sqlite3.IntegrityError`，与文档承诺的"返回既有记录"相悖。

### M5. 维护审计游标可能永久跳过补写失败的意图

锚点：`log_maintenance_service.py:49-57`、`file_action_audit_service.py:216-225`

`has_more` 为真时把 `auditCursor` 推进到本页**最后一条 id**，即视为已消费；而 `_tryComplete` 有意让失败意图保持 `pending`。于是卡住的意图被永久跳过，`has_more` 一直为真，游标只在"一个周期能装进一页"时才归零。稳态下每 60 秒周期若有 ≥200 条意图，被跳过的集合会无界增长，这些文件动作永远拿不到最终审计事件。

### M6. worker 崩溃会让扫描队列永久停滞

锚点：`scan_service.py:184-200`（启动恢复只看 `execution_token IS NULL`）、`scan_queue_service.py:83`

崩溃留下一行 `interrupted` 且令牌非 NULL 时，该行仍满足槽位谓词，`claimScan` 每轮都返回 None：新扫描照常被受理并审计为"等待后台执行"，然后永远不执行，且没有用户可见的错误。`inspectInterruptedScans`（`scan_recovery_service.py:53-55`）又刻意排除 `running`，所以"有令牌但无 PID"的 `running` 行（`isolated_task_service.py:38-45` 认领与写 PID 之间的崩溃窗口）既不可报告也不可释放。

### M7. 可变排序键 + OFFSET 分页会漏行

锚点：`remote_operation_service.py:349-352`

按 `updated_at DESC, id DESC` 加 LIMIT/OFFSET 分页，而 `updated_at` 在每次状态变更时都会被改写（`:389`）。第 2 页某行状态变化后跳到第 1 页窗口之前，OFFSET 10 的下一页就会静默跳过一条记录。这是本项目唯一把可变排序键与 OFFSET 组合起来的分页（其余列表用 id 稳定游标）。

### M8. 日志轮转是进程内的，而读日志与清理是跨进程的

锚点：`core/logger.py:211-216`、`:23-33`、`log_service.py:38-43`、`:96-100`

`RotatingFileHandler` 无 `delay`、无跨进程锁；每个 worker 子进程都对同一 `batch.log`/`app.log` 持有自己的 handler；`guardLogFileMutation` 只持进程内 `RLock`。在 Windows 上，API 进程打开着的文件句柄会阻止子进程轮转 rename 与清理 unlink，轮转抛 `PermissionError` 被 logging 吞掉导致记录丢失，清理则反复报告跳过/`has_more`。

### M9. 远端凭据的历史密文仍可用"数据库路径派生密钥"离线解密

锚点：`service/media_source_secret.py:20-22`、`:73-76`、`core/database.py:654`

`_key = sha256(str(settings.database_path))` 的旧方案仍在代码里，且 `_migrateProtectedCredentials` 每次启动都会跑。任何 v3 迁移之前的数据库副本（NAS 快照、备份、退役磁盘）都能用可猜的默认路径离线解出 SMB/WebDAV 密码与 Provider API Key。

补充：当前开发库 `data/mediaai.sqlite3` 已是全 `v3:`（已核对），`data/secrets/credentials.key` 也存在，所以**现行实例本身没有暴露**；风险在历史副本 + 回退分支长期保留。

### M10. 默认凭据密钥位置与数据库同目录

锚点：`service/credential_key.py:39`、`config/config.example.toml:8-11`

默认密钥落在 `data_dir/secrets/credentials.key`，而数据库在 `data_dir/mediaai.sqlite3`。一次目录级拷贝（`cp -r data`、备份任务打包 `data/`、共享 ACL 配错）同时拿到密文与密钥，使"独立密钥"的设计目的落空。代码只拒绝符号链接，无法察觉二者相邻。

### M11. `TaskGovernanceView.loadRemoteOperations` 缺少请求代次守卫，旧响应会覆盖新筛选

锚点：`TaskGovernanceView.vue:107-126`（写入 `remoteOperations/remoteTotal` 无版本校验）、`:150-154`（切 tab）、对比 `loadTasks`（`:76-100`）已实现 `taskRequestVersion`

失败场景：status 选 `recovery_required` → 查询（R1，慢）→ 立刻改成 `failed` → 查询（R2，快）→ R2 渲染 → R1 落地覆盖表格，而下拉仍显示"failed"，分页也来自 R1。用户随后对**自己并未选择的类型**点"恢复"。`TaskGovernanceView.test.ts:51-62` 只验证 `loadTasks` 的隔离；其 client mock 定义了 `fetchRemoteOperations: vi.fn()` 却从不调用，所以缺口不可见。

### M12. `webdav-recovery` 分页既无后端钳制也不回写 `result.page`，总数下降会把用户困在空页

锚点：`TaskGovernanceView.vue:107-126`（未 `paginationStore.setPage("webdav-recovery", result.page)`）、`remote_operation_service.py:322,337,351`（`safe_page = max(1,page)`，无上限钳制）

失败场景：筛选 `recovery_required` 共 15 条 → 翻到第 2 页（5 条）→ 其中 5 条被他人处理 → 总数变 10 → 点刷新 → `page=2&page_size=10` → 后端返回空列表 + total=10 → 表格空白、分页显示 `currentPage=2 / pageCount=1`（无高亮页、">" 禁用）→ 用户以为已经没有待恢复项。

对比：其余 6 个服务端分页接口都有钳制（`scan_service.py:1027,1087`、`preview_service.py:481`、`pending_file_service.py:137`、`task_governance_service.py:441`、`media_source_service.py:629`），且各自视图都回写了 `result.page`。`audit-events`（`audit_service.py:190-232`）同样缺钳制。

### M13. `SettingsView` 的 `loadAuditEvents` / `loadExternalSubmissionBlocks` / `loadUsers` 无请求代次守卫

锚点：`SettingsView.vue:499-517`、`:1080-1091`、`:537-548`、`:3617-3645`

失败场景：审计 tab 按 `actor_name=alice` 查询（R1，慢）→ 改 `event_type` 触发 `applyAuditFilters` → 查询（R2，快）→ R2 渲染 → R1 落地覆盖行、总数**并回写 `setPage("audit-events", R1.page)`**，于是筛选控件显示"alice + 新类型"，行和页码却属于 R1；下一次翻页基于这个错位页码计算。拦截记录列表同理：在 `updateExternalSubmissionDecision` 的重载在途时点刷新，较旧的列表会覆盖较新的，表现为"决定被回退了"。

**`frontend/src/` 下没有任何 `SettingsView.test.ts`**。

### M14. `MediaSourcesView` 的写路径全部没有 catch，保存/删除/启停失败完全静默

锚点：`MediaSourcesView.vue:433-508`（`submitEditSource` 有 try/finally、**无 catch**）、`:510-514`、`:516-529`、`:531-550`；对比 `submitSource`（`:236-252`）有 `catch { ElMessage.error(...) }`

失败场景：编辑媒体源改"目标路径" → 保存 → 路径变更确认 → `showCleanupProgress` 打开清理对话框，后端返回 400/403（路径被拒、或会话中途被撤权）→ 异常从 click handler 抛出成为 unhandled rejection，清理对话框在 400ms 后关闭（`:504-506`），编辑对话框仍开着，**没有任何提示**。（更正：本文原写“失败前预置 90%”有误——`cleanupDialog.percentage = 90` 在 `:422`，位于 `await work()` 之后，失败时不会执行。更准确的影响是：异常使清理对话框残留且无任何提示，`work()` 抛出后进度停在中途值。此外删除失败也可能留下进度框。）

`MediaSourcesView.test.ts:72-82` 只覆盖 `submitSource` 的失败路径。

### M15. 4xx 透出：拦截器只特判 401，多数读/干跑 helper 没走 `apiErrorMessage`，后端可执行的中文 detail 被替换成 Axios 文案

锚点：`api/client.ts:1465-1472`、`:1474-1483`、`:1524-1534`、`:1571-1580`、`:1590-1597`、`:2123-2128`、`:1202-1229`（均未包装），对比 `:1437-1443` 等已包装；`apiErrorMessage` 在 `:1192-1200`

失败场景 A（403）：具备登录但无 `task:manage` 的用户直接访问 `/tasks`（侧栏隐藏但路由守卫只校验登录态，`router/index.ts:63-86`）→ `GET /api/tasks/page` 返回 403 → 页面显示 `Request failed with status code 403` 与空表，没有任何解释。
失败场景 B（400）：`/rename-operations/dry-run` 抛 ValueError（如"批量操作数量不能超过 N 条"）→ 对话框只显示 `Request failed with status code 400`，用户无法判断是该重新核对还是改选择。

### M16. 已挂载视图忽略路由 query 变化（只在 `onMounted` 初始化），URL 与显示数据不一致

锚点：`RenamePreviewsView.vue:1671-1690`、`ScanResultsView.vue:126-138`、`ScanJobsView.vue:288-306`、`App.vue:112,183`（单个 RouterView，无 `key`）

失败场景：从扫描任务行操作进入 `/rename-previews?media_source_id=1&scan_job_id=10` → 加载完成 → 点侧栏"重命名预览" → vue-router 复用同一组件（仅 query 变化）→ `onMounted` 不再执行 → URL 变成不带 id，表格却仍显示 job 10 的预览与筛选。`/scan-results`、`/scan-jobs` 同理。刷新后才是空页，用户心智与实际状态不一致，且任何"全部…"批量动作都基于过期筛选。

注意修复方式：不要用 `<RouterView :key="route.fullPath">` 一把梭，因为 `RenamePreviewsView` 依赖在提交待处理迁移时自身 `router.replace` 不被重建（`RenamePreviewsView.vue:1465-1466`）。应在三个视图里 `watch` 相关 query 键并重新应用/清空筛选。

### M17. 单个条目失败会中断待处理迁移的整批后续条目

锚点：`service/pending_move_service.py:171-191`（except 块记录该条目后把任务置为 interrupted/partial_failed/failed 并 **直接 return**，日志文案本身写着"后续条目未执行"）；触发点 `pending_file_service.py:274-280`、`:284-285`

失败场景：受理 100 个文件的迁移；第 3 个文件在扫描后被 NAS 上删除/改名 → `source_path.exists()` 为 False → ValueError → 第 4~100 项全部不再执行，任务以 partial_failed 结束，用户必须重新受理剩余 97 个文件。注意这里连"结果可证为未执行"（动作已按 not_applied 释放）的情况也会中断，而这正是规则允许继续的情形。重命名与回滚执行器都是逐项继续的（`rename_operation_service.py:611-643`、`rename_rollback_service.py:751-778`），所以这是不对称的行为，与 `agent.md:95`"单个文件失败不应中断整体任务"冲突。

测试固化：`test_pending_move_queue.py:159-165` 与 `test_pending_files.py:95-108` 都把中断行为写成预期，且两个 fixture 里冲突项都排在最后，所以"继续执行"从未被真正验证。

最小修复：仅当失败可证为未执行（`pending_file_service.py:359-362` 的 unchanged 分支已经算出这一点）时继续下一项；只有结果未知（存在活动 `local_file_actions` 行）或系统级错误（DB 不可写、目标目录消失）才停止整批。

> **已撤回（2026-10-09 复核）**：`_status_for_target`（`preview_service.py:173-181`）在 `original_name == target_name` 时返回 `no_rename`，而 `READY_STATUSES = {"generated","edited"}`（`rename_operation_service.py:38`）不含它，`_build_item_plan:262` 会直接判 `conflict`。本文所述“正常同名预览仍为 ready”不成立。仅历史非法快照可能残留，降为可选的防御性断言。

### M18. `moveFileNoReplace` 对同路径移动返回成功，于是空操作被记为真实变更并审计

锚点：`utils/file_operations.py:16-17`（`if os.path.abspath(source) == os.path.abspath(target): return`）；调用方 `rename_operation_service.py:595`、`rename_rollback_service.py:732`

失败场景：某预览的 edited_name 等于当前文件名时仍为 `ready`（`rename_operation_service.py:271-276` 在 `resolve()` 相等时跳过冲突分支，`local.py:70-90` 的 `check_rename_ready` 也不拒绝同路径），执行后条目被标为 `renamed`、更新 `media_files`/`rename_previews` 并写成功审计，而磁盘上什么都没变。不是数据丢失（后置条件成立），但计数与审计夸大了变更；一个 `rollback_path == current_path` 的回滚条目也会被报成已回滚。

### M19. 重命名/回滚在 `prepareLocalFileAction` 提交与真正移动之间没有源身份复检（残余 TOCTOU）

锚点：`local_file_action_service.py:37-39` 在 `prepareLocalFileAction` 内 stat 源文件后即提交返回；`rename_operation_service.py:589-595` 与 `rename_rollback_service.py:726-732` 从那次提交直接走到 `moveFileNoReplace`；而待处理迁移路径**有**复检（`pending_file_service.py:298-299`）

失败场景：在这几毫秒窗口内（还包含一次 DB 提交与一次租约 SELECT），外部进程（下载器、另一个工具、用户复制）替换了源路径上的文件 → 被移到预览目标名下的其实是另一个文件，却被记录为"预览中那个媒体文件重命名成功"；记录的 `identity_json` 描述的是一个已不存在于任何位置的文件，恢复因此报 `"manual"`（并叠加 H10 的后果）。概率低，但与待处理迁移路径的不对称看起来并非有意。

最小修复：`prepareLocalFileAction` 之后、移动之前复检 `localFileIdentity(source) == identity`（或把该检查并入移动 helper）。

### M20. 控制层直接写库，导致"业务已成功但审计失败"变成 500

锚点：`api/tasks.py:92-120`（先 `set_task_archived` 提交，再在控制器内 `record_audit_event`）、`api/auth.py:123,137,166`、`api/settings.py:145,309`、`api/rename_rollback_plans.py:46,100`

失败场景 A：`PATCH /api/tasks/scan_job/7/archive` → 归档已提交 → `record_audit_event` 遇到短暂写锁 → `sqlite3.OperationalError` 一路抛出 → 客户端收到 500 并认为归档失败，而任务**实际已归档且没有任何审计记录**（治理动作无审计）。
失败场景 B：`POST /api/auth/login` 密码错误时若审计写入失败，客户端收到 500 而不是 401，且失败登录审计丢失（重试/滥用信号消失）。

根因：`agent.md:114` 规定控制器只负责参数接收、调用服务、返回结果；这些控制器内审计既没有与业务变更同事务，也没有错误隔离。注意测试为什么抓不到——现有审计失败测试 patch 的是 `app.service.audit_service.record_audit_event`，而这些控制器在 import 时绑定了名字（`from ... import record_audit_event`），**patch 拦不住**。

最小修复：把审计移入对应 service 并传 `connection=` 同事务提交（`media_source_service._recordSourceAudit`、`external_submission_guard` 已有此模式）；纯诊断性的失败审计加 try/except + `logger.warning`。

### M21. 部分失败的待处理迁移返回 400，但文件其实已经移动并提交

锚点：`api/pending_files.py:116-129`、`service/pending_file_service.py:227-229`、`:344`、`:368`

失败场景：`POST /api/pending-files/move` 迁移 3 项，第 1 项已移动并提交（`UPDATE pending_files SET status='moved'`），第 2 项目标已存在 → `ValueError` → 路由映射为 **400**。客户端据此判定"迁移失败"，但第 1 个文件真的已经移动。

更严重的分类错误：`:344` 与 `pending_move_service.py:176` 把 `(OSError, sqlite3.Error, ValueError)` 一起捕获，于是**数据库/系统级故障被重新包装成业务 ValueError 并以 400 返回**，永远不会以 5xx 暴露——与 `agent.md:95`"系统级错误必须终止并提示"的意图相反。

测试固化问题：`test_pending_files.py:95` 断言的正是"service 抛 ValueError 且带该消息"，即把当前（错误的）分类写进了测试，且从不校验 HTTP 状态码。

### M22. 重命名批次内逐项宽泛 `except Exception`，把环境级故障记成 N 条单文件失败

锚点：`service/rename_operation_service.py:611`、`:620-641`

失败场景：WebDAV 媒体源的凭据密钥文件被移除/轮转。每个条目都抛同一个环境级错误，循环把它们全部记为逐项 `failed`，继续敲打已经坏掉的依赖，最后返回 HTTP 200 且 `status: "partial_failed"`。运维看到的是"部分失败"而不是"凭据存储已损坏"，自动化拿到 200 也看不出异常。除 `RenameLeaseError` 外没有任何异常会让整批中断。

最小修复：引入系统性失败标记（按异常类型判定：`sqlite3.Error`、凭据/密钥错误、`ExternalBudgetUnavailable`、协议不支持），在提交已写入的逐项行之后把它们抛出循环。

### M23. 400 detail 泄露服务器绝对路径

锚点：`service/pending_file_service.py:276`、`:280` → `api/pending_files.py:128-129` 原样作为 `detail` 返回

失败场景：对一条过期记录 `POST /api/pending-files/move` → `400 {"detail":"源文件不存在: D:\\Media\\Private\\Show\\ep01.mkv"}`。任何持有 `rename:execute` 的用户都能借此获知自己无权浏览的目录结构。最小修复：只回文件名，完整路径留在服务端操作日志里。

### M24. `GET /api/media-sources/local-directories?path=` 可枚举宿主机任意目录

锚点：`api/media_sources.py:99-104` → `service/media_source_service.py:350-398`（`:365` 直接 resolve 调用方给的路径，无根目录约束）

失败场景：仅有 `source:write` 的用户请求 `?path=C:\Windows\System32`，即可拿到整个宿主机目录的条目名与 `readable`/`writable` 标志。`MEDIAAI_MEDIA_ROOT`（`:338-341`）只设置**默认起始目录**，不做包含性校验。`test_media_sources.py:337-370` 只断言子目录枚举与容器默认值，没有包含性测试。

### M25. 无全局异常处理，控制器也完全不记日志

锚点：`backend/app/main.py:87-110`（无 `exception_handler`、无中间件）；`backend/app/api/` 下只有 `api/health.py:8` 引入过 logger（更正：本文原写“控制器完全不记日志”过强——服务层有日志，uvicorn 也会记录未处理异常；准确说法是控制器自身不记日志、缺统一入口与关联 ID）

失败场景：任何未预期异常（如 `sqlite3.OperationalError: database is locked`）返回无正文的 `Internal Server Error`，媒体类型与结构与其它错误都不同，且**不写入 `logs/error.log`**（只能靠 uvicorn stderr）。与 `agent.md:115`"文件 IO、网络请求、AI 调用必须有异常处理和日志"冲突。最小修复：注册 `RequestValidationError` / `HTTPException` / `Exception` 处理器，统一 `{"detail": "<字符串>"}` 形状并用 `app.core.logger` 记录关联 id。

### M26. 同类校验失败在路由间返回 422 或 400 不一致，前端无法渲染 422

锚点：`api/scan_jobs.py:83-90`、`:105-114`（`Literal`/`Query(le=200)` → 422，`detail` 是**数组**）对比 `api/rename_previews.py:159-169`、`api/tasks.py:62-79`（`sort_by: str` + service 抛错 → 400，`detail` 是字符串）

失败场景：`GET /api/scan-jobs/page?page_size=500` → 422；`GET /api/rename-previews/page?page_size=500` → 400。前端 `client.ts:1192-1200` 要求 `typeof detail === "string"`，所以 422 一律退化成 `Request failed with status code 422`，而等价的 400 能显示正常中文提示。

### M27. 无统一响应模型（`response_model` 数量为 0）

全 `api/` 目录 `grep response_model|ApiResponse` 无匹配；路由返回服务层 dataclass 或手搓 dict（`api/external_submission_blocks.py:98-101`、`api/tasks.py:59`），`schema/` 里只有请求模型与领域 dataclass。后果是任何 dataclass 字段改名都会静默改变公开 API，且 OpenAPI 没有出参契约。另有一处契约不一致：审计分页返回 camelCase `pageSize`（`audit_service.py:255-263`），其余所有分页端点返回 `page_size`。

### M28. 无 LIMIT 的旧列表端点仍在被前端调用

锚点：`api/scan_jobs.py:50-54`、`:67-79`、`api/rename_previews.py:138-156`、`api/pending_files.py:57-69`、`api/rename_rollback_plans.py:62-70`；调用方 `frontend/src/stores/media.ts:86,118`、`preview.ts:63`、`pendingFiles.ts:128`

失败场景：20 万文件扫描后 `GET /api/media-files?scan_job_id=9` 会把 20 万个 dataclass 序列化进一个 JSON 响应。各 `*_/page` 兄弟端点都已存在并被表格使用，这些是没有任何上限的遗留面。建议删除或在 `operations.batch_limit` 处封顶并返回 409。

### M29. `PUT /rename-previews/{id}` 提交后重读全表，且可能对已成功的编辑返回 400

锚点：`service/preview_service.py:582-623`：UPDATE 在 `:617` 已提交，`:619` 却调用**无过滤**的 `list_rename_previews(settings)` 再在 Python 里找该行（`:620-623`）；找不到就返回 400。

失败场景：10 万条预览时，改一个文件名要加载 10 万行；且若期间该预览被排除，`_previewReadFilters` 过滤掉它，调用方拿到 `400 命名预览不存在`，而编辑其实已经写入。最小修复：改用已存在的 `_preview_by_id`（`:730`），`exclude_rename_preview:656` 就是这么做的。

### M30. `metadata-match/all*` 批量匹配无上限且同步执行（当前无 UI 调用方，属遗留面）

锚点：`backend/app/api/rename_previews.py:272-301`、`backend/app/service/preview_service.py:1392-1404`

`match_all_unmatched_metadata` 的 id 选择 SQL **没有 LIMIT**（`preview_service.py:1393-1398`，条件只有 `metadata_match_status IS NULL`），随后对每个 id 调用 `match_rename_preview_metadata`，即**每个 id 一次真实 TMDB 搜索 + 候选详情 HTTP 调用**，全部在同一个同步请求内完成；`operations.batch_limit` 只约束**选择**端点与 dry-run，对这两个 `all` 端点无效（它们自己选 id 集合）。

重要限定（已核对，决定其定级）：**这两个端点当前没有 UI 调用方。** `frontend/src/api/client.ts:1734/1761` 与 `stores/preview.ts:147/226` 保留了 `matchAllUnmatchedMetadata*`，但**没有任何视图调用这些 store 方法**（已 grep 全 `frontend/src`，仅 store 自身定义与测试 mock 出现）。视图的"全部匹配"实际走 `readAllTargets` → `previewStore.matchMetadataBatch(...)` → `client.ts:1721` 的 `/rename-previews/metadata-match`，该端点接收显式 `rename_preview_ids`，受 `batch_limit` 约束并在超限时拒绝。因此这不是用户当前会踩到的缺陷，而是**遗留/未使用面**：只要有人（或未来的页面）改用它，或直接调 API，就会得到一个约 2.8 小时、客户端 15 秒必然超时、且服务端继续跑并消耗外发预算的请求。

次要影响（同一根因）：这类同步长任务占用 anyio 线程池线程（默认 40）且无服务端截止时间。

测试空白：`test_rename_previews.py:519` 只用极小 id 列表走 HTTP；`test_preview_pagination.py:64-87` 只覆盖选择端点的 `over_limit`。

最小修复：与其它已异步化的入口保持一致——改为队列式受理（202 + 任务 id，复用 operation_log 轮询），或删除这两个端点，或至少加 `LIMIT operations.batch_limit` + 超限 409。

## 4. 文档与代码的错位（可能只是文档滞后，不一定是缺陷）

这一节单独列出，因为**当文档与代码不一致时，需要先判定哪一边是旧的**。下列错位我都读了代码，给出结论；请按你的实际意图决定改代码还是改文档。

### 4.1  `POST /api/scan-jobs`（同步）已被文档"淘汰"，但路由仍然存在且有副作用

- 文档怎么说：`project-design.md:130`"用户发起扫描任务，受理接口先持久化任务、操作者及策略快照，**返回202和稳定任务ID**"；`M1-design-manual.md:147` 则把 `POST /api/scan-jobs` 列为 M1 的 API。
- 代码怎么做：`api/scan_jobs.py:30-47` 的 `POST /scan-jobs` **仍同步执行** `run_full_scan`；`api/scan_jobs.py:93` 另有 `POST /scan-jobs/async`（202）才是文档描述的那个。
- 谁在用：前端只用异步的 `acceptScanJob`（`stores/media.ts:99`）；`createScanJob`（`client.ts:1306`，指向同步端点）**仅被 `client.test.ts:631` 调用**，没有任何生产调用方。
- 结论：这是**遗留同步入口**。文档没有描述它，但代码保留它以兼容旧调用。我把它记为 H1 不是因为"文档没提"，而是因为它在无令牌的情况下执行真实扫描，能绕过执行槽并与排队任务并发（见 H1）。
- 建议：要么删除该路由，要么在文档里明确标注为"遗留同步入口、可能绕过执行槽、不推荐使用"。

### 4.2  其余三处同步入口同样是遗留面，且行为一致

- `POST /api/rename-operations/{id}/execute`（`api/rename_operations.py:48`）、`POST /api/rename-rollback-plans/{id}/execute`（`:116`）、`POST /api/pending-files/move`（`:116`）都各自有一个 202 异步兄弟（`:71`、`:139`、`api/pending_files.py:32`）。
- 前端**全部只用异步版**：`acceptRenameOperation`（`client.ts:1599`）、`acceptRenameRollbackPlan`（`:1652`）、`acceptPendingMove`（`:2184`）；对应的同步 helper `executeRenameOperation`（`:1590`）与 `movePendingFiles`（`:2165`）**仅被测试引用**。
- 结论：与 D1 同构。这解释了 H9 的成因——同步路径的认领不写执行令牌，而恢复工具只认令牌；异步路径（`rename_queue_service.py:63-71`）才是正确实现。**这不是文档滞后，而是遗留代码与新增恢复机制之间的语义不匹配。**

### 4.3  `must_change_password` 不在服务端强制——这是已确认政策，不是缺陷

`remediation-plan-2026-10-01.md:24` 的 P02 明确写"保持已确认需求：每次登录提醒改密，不新增操作限制"，所以"后端不拦、前端提醒"与文档一致。我最初把它列为待定项，核对政策后**已撤销**，不作为发现。

### 4.4  开发库落后于文档，而不是文档落后于代码

已只读核对：`data/mediaai.sqlite3` 的 `schema_version = 30`，而 `CURRENT_SCHEMA_VERSION = 34`。文档（`fnos-ghcr-docker.md:186-195`）已按 30→34 的完整迁移链描述到位，**文档是新的、这个开发库是旧的**。所以 L7 只是运维提示，不是文档问题。

### 4.5  如何避免"文档滞后"被误判为缺陷（本次方法）

本次审查对每条发现都做了两步：先读**代码**确认行为（而不是读文档推断行为），再核对文档里是否存在与之冲突的声明。凡属下列情形，我一律不计为缺陷：

- 文档描述了某个目标行为，而代码已按该行为实现（例如 202 异步入口、schema34 迁移链、一次性外发授权的单次消费）——即便同一文档别处还留着旧描述。
- 代码里的某个 `except Exception`、`print()`、硬编码 URL 带有明确注释或位于 CLI 工具中——属既有约定，不是新引入的问题。
- 行为与 `remediation-plan` 已确认政策一致（如 4.3），即使它与更早的设计手册措辞不同。

因此本报告的高危项都满足"有具体失败场景 + 与当前代码一致 + 不是已知政策的另一面"这三条。

## 5. 低危与工程卫生

### L1. 出站请求未纳入共享预算，且无内网地址限制

`ai.base_url`、`douban_proxy.base_url` 仅做 `^https?://[^\s]+$` 形态校验（`settings_service.py:1235-1241`），`POST /api/settings/ai/test` 会用配置的密钥向该地址发请求；WebDAV 的浏览/连接测试走 `build_opener`（`webdav.py:56-57`）完全不经 `ExternalRequestBudget`。`settings:write` 或 `source:write` 持有者可借此探测内网（重定向已被正确禁止，所以不是凭据转发问题）。建议拒绝回环/链路本地/私有网段或改为运营方白名单。

### L2. `run_tests.py`清理失败会覆盖runner退出语义

> **事实更正（三轮定稿）**：子进程stdout/stderr为继承输出，清理异常不会抹掉已经打印的测试行；真实缺口是`TemporaryDirectory.__exit__`异常会覆盖runner原本应返回的子进程退出码并留下目录。临时目录在创建阶段被环境策略拒绝是另一类环境限制。下方“吞掉全部输出”及`ignore_cleanup_errors=True`建议已作废。

`backend/run_tests.py:14-27` 用 `with tempfile.TemporaryDirectory(...)` 包住子进程调用。若退出时清理抛 `PermissionError`（受限沙箱、杀软、`data_dir` 内有未关闭句柄、Windows 句柄占用），`__exit__` 的异常会替换子进程输出——**测试结果一行都不打印，且即使套件全绿也以非 0 退出**。本次审查在本机复现了该现象（在收紧的文件策略下 `%TEMP%` 与工作区内 `tempfile.mkdtemp` 均被拒绝写入，`run_tests.py` 直接崩在清理阶段）。`test_test_runner.py` 只 mock 了 `subprocess.run` 成功返回 0 的情形，覆盖不到清理失败路径。

历史建议（已作废）：~~使用`ignore_cleanup_errors=True`。~~ 现行方案见修复计划X0：清理异常单独告警；子进程失败保留其非零码，子进程成功但清理失败返回1。

### L3. `.reasonix/` 与 `.codegraph/` 未加入 `.gitignore`

已核对：`.codex/` 已忽略（`.gitignore:35`），但 `.reasonix/`（3 个桌面主题 JSON）与 `.codegraph/`（其内部 `.gitignore` 只挡 `*.db`/`*.db-wal`/`*.db-shm`，`daemon.pid` 与 `.codegraph/.gitignore` 仍未被忽略）会随 `git add -A` 进入提交；`.codegraph/codegraph.db` 是 2.9MB 的索引库（本身就是被忽略的关键——它可能含源码片段）。建议补两条忽略规则。

### L4. `_request` 的 Destination 约束是大小写敏感的

`webdav.py:139-142` 用 `if "Destination" in headers` 判断。调用方传小写 `destination` 时不会走 `_confined_url`，而 urllib 仍会以 `Destination` 发出。当前生产只有 `move_file` 构造 `extra_headers`（大写），所以**不可从生产路径触达**；但该函数是模块共享 helper，且仓库自己的测试 helper 是大小写不敏感的，会给出虚假的安全感。建议统一为大小写不敏感比较。

### L5. 条件写入失败被映射成误导性的错误文案

`webdav.py:533-534` 的强 ETag 拒绝是 `ValueError`，被 `:543-544` 捕获后渲染成"地址或安全边界校验失败 / 请检查最终地址、根目录及路径编码"（`:229-230`）；HTTP 412 则变成"请检查 WebDAV 服务状态和反向代理配置"（`:226`）。两者都是失败关闭（无数据影响），但运维会被引向排查代理而非"源版本已变或目标已存在"。建议为 412 与 ETag 校验各给一条专用文案（"源版本已变化或目标已存在；请重新干跑"）。

### L6. 登录无速率限制，且默认管理员密码短于常规策略

`api/auth.py:153-179` 无任何限流/锁定；`config.example.toml:32` 出厂默认密码是 `123456`，`_validate_default_password` 只要求 6 位（`auth_service.py:263-266`），低于 `_validate_password` 的 8 位常规策略（`:257-260`）。注意 `default_admin_enabled` 默认为 `false`（`config.example.toml:29`），且"不改密只提醒"是 P02 已确认政策——所以这不是政策违规；但一旦有人开启该开关并保留默认密码，配合无限流就是可枚举的弱口令。建议给登录加退避/锁定，或让开启开关时拒绝出厂密码。

### L7. 开发库 schema 落后于代码

已核对（只读）：`data/mediaai.sqlite3` 的 `schema_version = 30`，而 `CURRENT_SCHEMA_VERSION = 34`。**下次在本机启动应用会触发 30→34 迁移并改写这个开发库**。若要保留当前开发数据用于比对，请先备份；测试请继续走 `backend/run_tests.py` 的临时配置入口。

## 6. 已验证为正确的部分（摘要）

- 认领/租约原语、槽位谓词、`finishInterruptedScan` 的先后顺序（见第 1 节）。
- 一次性外发许可的绑定与单次消费：`_checkActor` 重读用户行并要求启用 + 同名 + 权限；指纹覆盖来源、文件名/路径、标题、目标服务与解析载荷；状态在发网络请求**之前**置为 `consumed`；`override_unknown` 不可再次批准。
- `isStrongEtag` 严格实现 RFC 9110 `etagc`，拒绝弱 ETag/未加引号/`*`/CR-LF，因此 ETag 无法注入头部。
- WebDAV 请求形状符合 RFC（`If-Match` 作用于源 URI、`Destination` + `Overwrite: F`），无重试丢弃条件，除 412 外没有 4xx 被当作可重试。
- 目标覆盖 TOCTOU 在本地协议上确实封闭：Linux 用 `renameat2(RENAME_NOREPLACE)` 且**拒绝降级**（`file_operations.py:24-32`），Windows 用会抛 `FileExistsError` 的 `os.rename`，跨设备复制用 `O_CREAT|O_EXCL` 并在删除源之前持久化目标身份。
- 路径穿越在写入侧封闭：预览目标名拒绝任何路径分量，迁移目标取 `Path(file_path).name`，源受根目录 `is_relative_to` 约束，目标目录身份按 device/inode 复核。
- 监督者的 kill/释放顺序与进程回收正确，认领不会在子进程存活时被释放。
- 无 `async def` 路由，事件循环不被阻塞；`time.sleep` 只在线程池线程或隔离子进程中。
- 日志不记录密钥：审计/运行日志经 `sanitize_audit_detail` + `redactUrlCredentials`，worker 错误消息只含类型名。

## 7. 未完成与残余风险

- **测试基线已独立复现**：在放宽文件策略后运行
  `\.codex\test-artifacts\review-fixes\dependency-lock\venv311\Scripts\python.exe backend\run_tests.py discover backend/tests`
  得到 `Ran 747 tests in 232.695s`、`OK (skipped=10)`、退出码 0（日志 `.codex/baseline-20261008-190248.log`）。正确读法是 **747 总项、10 跳过、737 通过**（此前本文误写为“747 通过 + 10 跳过”，经 Codex 复核更正）。这与文档声称的 747 项一致，说明 `remediation-plan` 第 6.1 节的证据可信；10 项跳过为受控 WebDAV 集成，不计为通过。
- 本次报告的所有"已验证"均来自源码阅读与定点验证，并逐条核对了关键锚点；未对真实 NAS 做任何操作。
- 未验证：真实 NAS 上 `Overwrite: F` 是否被执行（H3 的关键前提）、`If-Match` 在线上报文中的实际形态；未做真实浏览器/组合缩放验收。
- 本报告未覆盖：设计文档与实现之间的一致性复核（`design-review` 的职责）、可观测性专项（`observability-review` 的职责）、以及 10 月的 F01 一次性授权链路的端到端验收。
- 本次为只读审查，未提交、未发布、未修改任何生产代码、未操作正式媒体或数据库（对 `data/mediaai.sqlite3` 只做了只读查询）。

## 8. 建议修复顺序

1. **H8**（无令牌 `running` 永久锁死队列）与 **H4**（已并发模拟确认，同属恢复安全）。
2. **H2 / H3**（WebDAV 写入门禁；H2 为适配器 fail-closed，与 R37 端点能力验证联合安排）。
3. **H9 / H10**（不可恢复的文件状态、假成功审计）；**H11** 需先决策容量政策。
4. **H1**（同步扫描绕过执行槽；修法见 4.1、4.2）。
5. **H5 / H6**（配置读取授权、跨用户状态残留）与 **M3（重写）**（覆盖路径不重算敏感词；**注意**：原"改名洗白"链已撤回，本项依据为 prepare 时点被吸收的敏感词变化）。
6. **M2 / M6 / M7 / M8**（状态机、维护可见性与多进程日志）与 **M30**（批量匹配；目前无 UI 调用方，可后置）。
7. **M4 / M19 / M20-M23 / M25 / M26 / M29**（契约、错误分类与身份窗口）。
8. **L3 / L4 / L5 / L7** 随手修；其中 **L2** 会直接影响后续验证的可信度，建议优先。
9. **决策项（先决策，不实施）**：H11、M17、M24、M27、M28、H5 的公开设置范围、M9/M10、L1、L6。
10. **已撤回、勿实施**：M1、M3 原链、M18。
11. **第 4 节的 5 条错位**：确定每条该改代码还是改文档，避免下次审查再把遗留面当成新缺陷。
