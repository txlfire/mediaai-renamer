# 审查修复工作指令（供 Codex 执行）

文档类型：实施指令与门禁。配套发现记录见[独立代码审查报告](code-review-2026-10-09-independent.md)（下称"报告"）。本文只讲**怎么改、改哪、怎么验证、什么算完成**。

基线：`develop` / `35e3db9` + 未提交改动，应用 1.0.1、`CURRENT_SCHEMA_VERSION = 38`（**已更正**：原文写 34，属于本计划成文时的值；后续批次已推进至 35–38）。**不提交、不发布、不升级版本、不动正式实例。**

> **状态说明（2026-10-11 核对）**：本计划的 X0 与 B1、B2、B3、B4 已由 Codex 执行完毕，实际进度与最新证据见文末第 13 节状态表；剩余为 B5（契约/错误分类）、B6（前端一致性）、B7（卫生与文档同步）。本节以下正文为该批次的原始设计与验收断言，保留作为已完成工作的追溯依据，不再代表当前基线。

## 0. 每次开工前必读

1. 先读本文第 1 节（环境），确认你用的是正确解释器和 Node。
2. 再读要执行的那一批的"文件清单"和"验收断言"。
3. 只改该批文件；相邻批次不要顺手改，避免回归范围失控。
4. 每批结束后按第 10 节的变更范围门禁执行，并把输出路径记到 batch 记录里。

## 1. 执行环境（重要：默认 PATH 是错的）

本机实测：

| 工具 | `PATH` 上的默认值 | **本任务必须使用** |
| --- | --- | --- |
| Python | `.venv\Scripts\python.exe` = **3.14.6** | `.codex\test-artifacts\review-fixes\dependency-lock\venv311\Scripts\python.exe` = **3.11.15** |
| Node | `D:\nodejs\node.exe` = **v16.20.2**（过旧） | `D:/nvm/v24.15.0/node.exe` = **v24.15.0** |

后果：直接跑 `npm run backend:test`（脚本内写死 `.\.venv\Scripts\python.exe`）或 `npm run frontend:test`（用 PATH 上的 node 16）**都会失败**，而且失败原因与本次修复无关，不要据此判断代码有问题。

### 门禁命令（按第10节的变更范围选择）

```powershell
# 1. 后端全量（工作目录 = 仓库根）
.\.codex\test-artifacts\review-fixes\dependency-lock\venv311\Scripts\python.exe backend\run_tests.py discover backend/tests

# 2. 前端单测 + 类型检查（把 Node 24 放到 PATH 前面）
$env:PATH = "D:\nvm\v24.15.0;" + $env:PATH
npm.cmd run frontend:test
npm.cmd run frontend:build

# 3. 编码与空白检查
npm.cmd run check:encoding
git diff --check
```

基线（独立审核方已复现）：`Ran 747 tests in 232.695s`、`OK (skipped=10)`、退出码 0，日志 `.codex/baseline-20261008-190248.log`。**正确读法是 747 总项 = 737 通过 + 10 跳过**（此前误写为“747 通过 + 10 跳过”，已更正）。验收应表述为「发现总数 = 通过 + 跳过 + 失败 + 错误」，新增回归后总数随之增长；**不得把跳过记为通过**。

### 先修 X0：让验证本身可信

`backend/run_tests.py:14-27` 用 `with tempfile.TemporaryDirectory(...)` 包住子进程调用。退出时若清理抛 `PermissionError`，`__exit__` 的异常会覆盖runner原本应返回的子进程退出码，并可能留下临时目录；子进程已经继承输出的stdout/stderr不会因此消失。`test_test_runner.py` 目前没有覆盖“子进程结束后清理失败”。

改法（**已按复核意见修订**）：不要简单用 `ignore_cleanup_errors=True`——那会掩盖残留目录问题。应：

1. 把清理放入 `try/except` **单独**处理，捕获后打印告警（含残留目录路径），**保留子进程的真实返回码**；
2. **不得**让清理告警把测试失败的退出码变成0；子进程成功但清理失败时返回1，明确表示本次runner未完整收尾；
3. 子进程的 stdout/stderr 本已继承输出，不要改为捕获后丢弃。

验收断言：
- 新增回归：mock `subprocess.run` 返回 **非 0**、同时让临时目录清理抛 `PermissionError`，断言 `main()` 仍返回该非 0 值，且**清理告警与残留路径已打印**。
- 再断言成功路径：子进程返回0 + 清理异常 → 告警及残留路径可见，runner返回1。
- 现有两条 `test_test_runner.py` 断言必须继续通过。

**先做 X0，再做其它批次**。X0 修的是“清理异常可能覆盖子进程返回码”；它**不会**吞掉已继承输出的子进程 stdout/stderr（复核方隔离 mock 已证实 `cleanupFailureObserved=PermissionError` 且 `childOutputRetained=true`）。另需把“**临时目录创建被策略拒绝**”与“清理阶段失败”分开记录：前者是环境限制，后者才是 runner 缺陷。

## 2. 批次划分与依赖

| 批次 | 对应发现 | 目标 | 依赖 |
| --- | --- | --- | --- |
| X0 | L2 | 验证入口可信 | 无，最先做 |
| B1 | **H8** / H1 / H2 / H3 / H4 / M4 / M19 | 执行槽、远端意图与文件写入门禁 | X0 |
| B2 | H9 / H10 | 不可恢复状态与假审计（H11另列B8） | B1 |
| B3 | H5 / H6 / **H7** / **M3（重写）** | 设置授权、跨用户状态、权威连接结果与外发校验 | 无（可与 B2 并行） |
| B4 | M2 / M5 / M6 / M7 / M8 | 状态机、审计饥饿与多进程日志 | B1 |
| B5 | M20 / M21 / M22 / M23 / M25 / M26 / **M29** / **M30**（**M27/M28 移入决策项**） | 批量语义、错误分类与契约 | B3 |
| B6 | M11-M16 | 前端一致性与请求代次 | 无 |
| B8 | H11 | 未知审计意图容量与人工关闭 | B2；采用第4.3节已确认设计 |
| B7 | **仅 L3 / L4 / L5 / L7** + 第 4 节错位 | 卫生与文档同步 | 全部之后 |
| 决策项（**不进执行范围**） | M17 / M24 / M27 / M28 / M9 / M10 / L1 / L6 | 需产品/部署决策 | 见第 11 节 |

说明：

- **M3 已重写并立项**（见5.4）。原“改名洗白”链不成立、已撤回。准确时序是：block创建后、prepare前的策略变化成为新指纹基线，但当前命中不会展示或留痕；prepare后、submit前变化会被指纹拒绝。B3只处理每授权知情确认与审计证据，不废掉F01已批准的人为覆盖例外。
- **已撤回、不安排实施**：M1（`_publishScanStage` 自身事务边界使竞态不成立）、M3 原链（权限与指纹前提不支持）、M18（`no_rename` + `READY_STATUSES` 已阻断）。报告保留原文并就地标注，供追溯。
- **决策项不进入开发批次**：`M17`（批量单项失败是否继续，与 `project-design.md:186` 已批准设计冲突）、`M24`（部署浏览根政策）、`M27`/`M28`（契约与遗留 API 治理）、`M9`/`M10`（历史密文与密钥位置，按 P04 安排）、`L1`（私网白名单会破坏 188 NAS 与本地 AI/代理）、`L6`（出厂密码策略与 P02 冲突）。这些先出决策，**不要**按发现初稿直接改。H5、H7和H11已在本轮计划定稿中选定最小权限方案，不再属于开放决策。

每批独立提交验证结果（在 batch 记录里），不要跨批混跑。新增回归会增加测试总数，**不要固定套件数量**；验收表述为「发现总数 = 通过 + 跳过 + 失败 + 错误」。

## 3. B1：执行槽与文件写入门禁

### 3.1 H8 无令牌 `running` 文件任务锁死队列（最高优先）

**改哪里**：`backend/app/service/file_task_queue_service.py`、`backend/app/main.py`；参考已有的扫描侧实现 `backend/app/service/scan_service.py:184-200` 与 `main.py:67`。

**根因**（已逐行核对）：`file_task_queue_service.py:64` 判定占槽时对每张表跑 `execution_token IS NOT NULL` **和** `status IN ('running')` 两组查询，即只看状态不看令牌；而 `:110`、`:177`、`:189` 三个恢复入口都要求令牌非空。`rename_operation_service.py:431-440` 与 `rename_rollback_service.py:549-552` 在同步路径下写 `status='running'` 却不写令牌，于是崩溃后留下一行"无令牌 running"，`claimFileTask` 对三种任务永远返回 None，且无 CLI 可解。

**怎么改**（**已按复核意见修订，原方案不可采用**）：

- ❌ **不可采用**："启动时把 `status='running' AND execution_token IS NULL` 的行直接标 `interrupted` 并写'未执行'"。启动一个实例**不能证明**另一进程已退出，更**不能证明**未发生文件动作；写"未执行"会用假结论掩盖未知意图。
- ✅ **应采用**：
  1. **统一同步认领**：让同步执行路径也持久化执行令牌与 PID（对齐 `pending_move_service.py:112` 的同步迁移做法——它已写 token/PID，不属同类产生方），从源头消除"无令牌 running"。
  2. **可只读核对**：让 `inspectInterruptedFileTasks` 之类入口能**只读列出**"`running` + 无令牌 / PID 未知"的行，供人工判断，不自动改状态。
  3. **明确停服核对后释放**：沿用现有的停服确认语义（`releaseInterruptedFileTask` 要求 `confirmStopped`），由运维在确认全部相关进程已退出后释放，并**保留未知意图**不移除证据。
  4. 补一条**归属证据**：记录该行由哪个进程/实例认领（实例标识 + 启动时间），使"另一进程是否可能仍在运行"可判断。

**不要做**：不要放宽 `assertFileTaskSlotFree` 的判定（那会让两个任务真的并发写文件）；不要自动重试或重放任何文件动作；不要删除无令牌行。

**验收断言**：
- 构造 `status='running'`、`execution_token IS NULL` 的三类行各一条 → 断言它们能被**只读列出**（含实例/归属信息），且**未被自动改写**、未产生任何文件动作。
- 断言同步执行路径执行后，该行**必有**令牌与 PID。
- 断言停服确认后释放能成功，且释放日志明确写"未知文件动作仍需独立核对"。
- 令牌非空的行不受影响；`assertFileTaskSlotFree` 仍拒绝并发（现有 `test_file_task_queue_scale.py` 断言继续通过）。

### 3.2 H1 同步扫描端点绕过执行槽

**改哪里**：`backend/app/api/scan_jobs.py:30-47`。

**二选一，并在 batch 记录里写明选了哪个及理由**：
- **A（推荐）**：把 `POST /scan-jobs` 改为受理语义，直接复用 `acceptScan`（返回 202 + `queued` 任务），与 `/scan-jobs/async` 行为一致。
- **B**：保留同步语义，但先走同一槽位认领（插入 `queued` → `claimScan`），执行期间持令牌。

**不要做**：不要让同步路径继续以"无令牌 + `status='running'`"写入。

**验收断言**：
- 新回归：先 `POST /scan-jobs/async` 使同源任务处于 `queued`，再 `POST /scan-jobs` → 必须被拒绝（400）或返回 queued，**不得**出现第二个 `running` 行。
- 断言任何 `running` 扫描行都满足 `execution_token IS NOT NULL`。
- 若选 A，`frontend/src/api/client.ts:1306` 的 `createScanJob` 语义变化需同步注释/文档；`client.test.ts:631` 必须更新为断言 202。

### 3.3 H2/H3 WebDAV 条件写入失败关闭 + 204 视为冲突

**改哪里**：`backend/app/service/shared_protocols/webdav.py:514-548`（`move_file`）、`check_rename_ready`（`:479-512`）；调用方 `rename_operation_service.py:506-516`、`rename_rollback_service.py:643-652`。

**改动点**：
1. `move_file` 在 `context.expected_source_version` 缺失或非强 ETag 时**拒绝**，返回明确失败结果（"服务端条件写入能力未验证，拒绝真实移动"），不再发出无 `If-Match` 的 MOVE。
2. `status` 取值改为**不要默认值**（`getattr(response, "status", 201)` 的默认 201 会掩盖异常响应）。
3. 把 **204 从"确定成功"中移除**，但**不要**当作"确定未执行"的普通冲突：按 RFC 4918 §9.9.4，204 表示"移动到已存在的目标"，在带 `Overwrite: F` 时合规服务器应返回 412；**该响应本身无法证明远端是否已修改**。因此 204 应落为**"请求已发出、结果待核查"**的权威状态并**禁止重放**，覆盖适配器、调用方、审计与恢复四条路径；PROPFIND 复核**可用于核查**，但**不能**消除"服务器不遵循条件请求"造成的原子性风险（与 **R37** 联合安排）。
4. 可选（更强）：MOVE 成功后用 PROPFIND 复核（源消失、目标存在且身份与移动前一致）才算成功。

**不要做**：不要移除 `Overwrite: F`；不要为了"让测试过"而放宽成功判定；不要把 204 继续算成功。

**验收断言**（扩充 `backend/tests/test_webdav_security.py`）：
- `expected_source_version=None` → `move_file` 返回失败且 **transport 未被调用**（`urlopen` 不得被调用）。
- `expected_source_version='W/"weak"'`、`"*"`、未加引号 → 失败且 transport 未被调用（现有断言保留）。
- 强 ETag + 服务器返回 201 → 成功（若已实现 PROPFIND 复核，则以复核结果为准）；返回 201 但复核发现目标身份不符 → 落"待核查"，不报成功。
- 返回 **204** → **不得报成功**，落"请求已发出、结果待核查"，且**禁止重放**；断言调用方/审计/恢复均按该状态处理（不是普通冲突、也不是确定未执行）。
- 返回 **412** → 失败且消息可读（源版本已变化或目标已存在）。
- 断言**不**以"动作后路径核对"冒充"动作前原子禁止覆盖"；该原子性缺口归 R37 单独验证。
- 断言请求仍带 `If-Match` 与 `Overwrite: F`。

### 3.4 H4 远端恢复加锁前读状态 + 租约不续期

**改哪里**：`backend/app/service/remote_operation_recovery_service.py:282-333`、`backend/app/service/remote_operation_service.py:188-189, 202, 361-405`。

**改动点**（约束已按二轮 02 收紧，**下列写法不可作为充分修法**）：

1. **原子化**：获取租约、锁内重读条目状态、执行认领必须在**同一事务**内完成（不能先读后锁、也不能只做其中一步）。
2. **全路径令牌/代次校验**：状态转换、业务同步与结果写入**都必须**验证当前令牌/执行代次。只保护"释放锁"不够——现有释放实现**已经**校验令牌，不要把它当新缺口重复实现。
3. **可恢复集合不等于安全**：`recovering` 属于可恢复集合，因此"锁内重读后仍属可恢复集合"**本身不能**阻止重放另一个尚未返回的请求；**租约过期不代表原请求没有执行**。必须显式区分四种情形：**原动作在途 / 结果未知 / 确证未执行 / 允许恢复**。
4. **续租覆盖完整慢调用**：续租必须覆盖整个慢调用，并明确续租失败后的停止与未知处理；**动作前单次心跳或简单提高 TTL 不证明全过程互斥**。数据库令牌**不能撤回**已发出的远端请求，**不能**把"接管成功"当作再次 MOVE 的充分条件。

**已确认的状态与事务设计**：

1. `remote_operation_items` 增加 `recovery_generation INTEGER NOT NULL DEFAULT 0`、`recovery_token TEXT`、`recovery_phase TEXT NOT NULL DEFAULT 'idle'`、`recovery_heartbeat_at TEXT`。阶段仅允许 `idle / probing / move_issued / remote_applied / reconciling / review_required / completed`。
2. 获取媒体源租约、锁内读取条目、递增generation、写入token并从允许状态转换到`probing`，必须在同一个`BEGIN IMMEDIATE`事务中完成。后续每次状态写入都带`WHERE id=? AND recovery_generation=? AND recovery_token=?`；影响行数不是1即停止，迟到请求不得修改新归属。
3. MOVE发送前先以CAS把阶段改为`move_issued`并提交。同步阻塞调用期间启动独立心跳执行单元，按固定间隔刷新锁及条目心跳；调用返回、异常或取消时可靠停止。续租失败后不再做业务同步，结果落`review_required`；如果请求已经发出，不自动重试。
4. 新持有者只能自动接管`idle/probing`且能够证明旧执行者已终止的条目。旧阶段为`move_issued`、心跳失效但执行者生死不明，或历史记录无法证明请求未发送时，一律进入`review_required`。仅“源存在、目标不存在”不能证明旧MOVE未在服务端排队。
5. `move_issued`后的恢复只允许两类自动收敛：源消失、目标存在且强身份匹配时转`remote_applied`并补业务记录；协议在发送前给出可证明的本地拒绝时转回可重试。超时、断连、204、条件能力不明及其他已发出异常均保持未知，不重发。
6. 完成业务同步前再次校验generation/token；业务记录、远端条目`completed`和恢复摘要在同一事务提交。旧generation的迟到成功或失败只写诊断日志，不覆盖条目和业务终态。

数据库变更同步升级schema并补旧库迁移测试；不把token、路径凭据或原始响应正文写入普通日志。

**不要做**：不要以 `recovering` 仍在可恢复集合、调用前心跳、增大 TTL 作为充分修法；不要因接管而自动重发 MOVE。

**验收断言**（新增并发回归，放 `test_remote_operation_recovery.py`；二轮已给出可复用的模拟证据）：
- 用 `ThreadPoolExecutor` + barrier 让两线程同持"加锁前读到的 `failed` 快照" → 断言**只有一个**执行到 `move_file`。
- **在途调用跨租约边界**：模拟 A 的租约过期后 B 接管 → 断言 B **不得**基于"接管成功"再次发出 MOVE，而应落"结果未知/需人工核查"。
- **迟到成功/失败不得覆盖新归属**：模拟 A 阻断后在 B 接管后再返回 → 断言 A 的迟到写入**不能**把已归属 B 的结果改回（二轮复现的 `finalRemoteItemStatus=failed` 而业务仍 `renamed` 正是此缺陷）。
- 断言租约在长操作期间被续期，且续租失败时停止并落未知（不静默继续）。
- 断言 MOVE 结果未知时不自动重放（保留现有语义）。
- **不能**只测"两线程中一个获取有效锁"。

### 3.5 M4 远端操作幂等创建并发

`remote_operation_service.py:265-295`改为原子幂等插入：`INSERT ... ON CONFLICT(idempotency_key) DO NOTHING`后返读，并在同一写事务校验同键的媒体源、操作类型、源/目标路径及源版本一致。`target_version`和恢复摘要会随执行演进，不属于初始幂等载荷；不同不可变载荷复用同键返回冲突，而不是既有成功记录。

验收：两线程同键同载荷并发只产生一行且返回同一条记录，无`IntegrityError`；同键不同载荷被拒绝且原记录不变。

### 3.6 M19 本地文件身份窗口

`rename_operation_service.py:589-595`与`rename_rollback_service.py:726-732`在`prepareLocalFileAction`和`moveFileNoReplace`之间复检`localFileIdentity(source) == identity`，动作后核对目标身份。复检只能缩短窗口，不能宣称原子消除外部换源；动作已发出后无法确证时记录未知，不误报成功或自动重试。

验收：注入准备后换源，文件动作被拒绝且记录身份冲突；注入动作后身份不明，记录未知并进入人工核查。

## 4. B2：不可恢复状态、假审计、审计容量

### 4.1 H9 仅大小写不同的重命名无法恢复

**改哪里**：`backend/app/service/local_file_recovery_service.py:30-37`（`_outcome`）；可选 `:177-180`。

**改动点**（**已按复核意见修订**）：❌ **不可**仅用 `os.path.normcase(source) == os.path.normcase(target)` 或 dev+ino 相等来判"已生效"。复核方已在 Windows 合成样本实测：大小写改名**移动前与移动后 normcase 均相等**，该判据会把"尚未移动"误判为"已生效"；同理会把硬链接误判为同一文件。

✅ 应采用"记录并核对**实际目录项**与**动作阶段**"的方式：
1. 在动作前记录**目录项的真实拼写**（`os.scandir` 得到的 entry name，而非仅存储的字符串）；
2. 记录动作阶段（意图已写 / 已发出 / 已确认落地），使"移动前"与"移动后"可区分；
3. 判定"已生效"要求：目录项拼写等于目标、源目录项已不存在，且**排除**硬链接、符号链接与目标被第三方替换的情形；
4. 无法确证时保持 `"manual"`，并提供**人工核对后关闭**的明确入口（带证据与原因），不要自动关闭。

**验收断言**：
- 新增回归（Windows 合成文件，`TemporaryDirectory` 内，不触碰真实媒体）：源 `Movie.2024.mkv` → 目标 `movie.2024.mkv`。
  - **移动前**：断言判定**不是**"已生效"（这正是原最小改法会失败的点）。
  - **移动后**：断言判定为"已生效"。
- 硬链接用例：源与目标为同一 inode 的两个目录项时，**不得**判为"已生效"。
- 断言 `"manual"` 状态仍可通过显式人工核对入口关闭，且关闭动作写入审计。

### 4.2 H10 重复 `POST /execute` 写入假成功审计

**改哪里**：`backend/app/service/file_action_audit_service.py:190-200` 与 `:101-135`；`backend/app/service/rename_operation_service.py:402-403`；`backend/app/api/rename_operations.py:57-65`。

**改动点**：让审计意图归属于**赢得认领**的那次请求（在 `dry_run → running` 事务内创建，参考 `rename_queue_service.py:63-71` 的 `acceptRename` 模式）；或让未执行任何文件动作的请求向 `runAuditedFileAction` 报告"未执行"，从而不落成功事件。

**验收断言**：A 执行完成并落 success 审计后，B 对同一 id 再次 `POST /execute` → 断言**不新增** `result=success` 的 `audit_events` 行（允许新增 `pending`/`not_performed` 或直接返回既有结果且不写成功）。

### 4.3 H11 未知审计意图容量与人工关闭（已确认设计，归B8）

**依赖**：先完成H10，使审计意图能够绑定实际赢得认领的执行请求。禁止按当前业务快照猜测旧请求结果；未知不推断仍是硬约束。

**权限决定**：不新增角色或新的权限枚举。查看未知详情要求`audit:read`，执行人工关闭同时要求`audit:read`与`task:manage`；前端仅在两项权限都具备时显示关闭操作。

**状态与数据设计**：

1. `file_action_audit_intents.status`明确使用`pending / review_required / completed / manually_closed`。增加`request_phase`、`closed_at`、`closed_by`、`close_reason`、`close_evidence`字段；原因和证据均必填且有长度上限，不保存凭据、令牌或未脱敏响应正文。
2. 只有能从本次执行上下文证明“请求被拒绝、未进入副作用”或“确定未执行”的意图可自动写入确定结果并转`completed`。已发出但无`outcome_json`、进程中断或归属不明的意图转`review_required`，不得自动改成成功或失败。
3. 人工关闭只把容量治理状态转为`manually_closed`，`outcome_json`记录`action_outcome='unknown'`及核查摘要，绝不伪造业务成功或失败。关闭动作单独写`audit_events`，包含操作者、原因、证据摘要和原意图ID。
4. 现有`audit_intents.max_records`与`audit_intents.max_total_mb`继续作为硬上限；达到80%在任务治理页告警，达到100%继续在任何文件副作用前拒绝新请求。`pending/review_required`不自动删除；`manually_closed/completed`按现有保留期与批次预算清理。
5. 新增只读分页查询和人工关闭API，放在任务治理或独立审计意图路由；关闭使用条件更新，重复提交幂等返回现有结果，不允许覆盖第一次关闭证据。

**验收断言**：

- 构造已发出但无结果的意图，维护任务只能转`review_required`，不能从当前任务状态推断成功或失败。
- 可证未发送的拒绝能自动完成并释放容量；真实未知在未人工关闭前不进入删除集合。
- 只有同时具备`audit:read`与`task:manage`的用户可关闭；原因/证据缺失返回400，权限不足返回403。
- 人工关闭后业务任务状态不变，意图保留`action_outcome='unknown'`，新增且仅新增一条关闭审计；重复请求不产生第二条关闭审计。
- 80%告警、100%副作用前拒绝、保留期清理及记录数/估算字节两种上限均有回归。

## 5. B3：授权、跨用户状态、敏感词

### 5.1 H5 `GET /api/settings` 缺权限

**已确认权限边界**：系统设置属于管理面，完整页面和配置明细只对`settings:write`开放。普通业务用户不读取配置列表、敏感词、路径、Provider地址或详细连接测试快照；业务页确需的状态通过独立最小接口提供。

**改动点**：

1. `GET /api/settings`、`GET /api/settings/metadata-providers`、三类详细`test-result`及命名配置导出统一要求`settings:write`；写接口维持同权限。
2. 前端设置菜单仅对`settings:write`显示，路由元数据和守卫同步校验；无权限直接访问`/settings`返回到首个可访问业务页并提示权限不足。
3. 新增认证用户可读的`GET /api/settings/ai/readiness`，仅返回`ready`、`status`、`tested_at`和通用提示，不返回配置快照、base URL、模型、敏感词或其他设置。重命名页改用该接口判断AI是否可用。
4. 不引入角色、资源所有权或多租户模型；权限继续直接分配给用户。

**验收断言**：

- 无`settings:write`用户访问完整设置、Provider配置、详细测试历史和导出均403，且前端菜单不可见、直达路由被拦截。
- `settings:write`用户维持现有读写、测试和用户维护能力。
- 仅有`metadata:submit`的用户可读取AI readiness并正常使用已配置AI匹配，但响应不含配置明细；普通用户不能通过其他设置读接口还原敏感词和服务地址。

### 5.2 H6 登出后业务 store 残留

**改哪里**：`frontend/src/stores/auth.ts:41-45`、`frontend/src/App.vue:75-78`；各业务 store。

**改动点**：新增 `resetWorkspaceState()`，在 `logout()`、`clearSession()`、`handleAuthExpired()` 中调用，清空 preview/media/pendingFiles/renameOperation/pagination/tableSort，并停掉轮询（`stopMovePolling()` / `stopExecutionPolling()`）。同时让 `RenamePreviewsView.vue:1671-1686` 在路由**不带** `scan_job_id` 时主动清空 `previews` 与 `filters`。

**验收断言**（新增测试，当前无任何覆盖）：
- 登出→重新登录后，`previewStore.previews` 为空、`filters.scan_job_id` 为空、"查询"按钮禁用。
- 断言轮询定时器已停止（用假定时器推进，断言不再发请求）。

### 5.3 H7 连接测试结果必须由服务端签发

**已确认方案**：TMDB、IMDb、AI测试接口负责实际连接并在服务端原子保存结果；客户端不再提交成功/失败结果。采用现有单次测试接口，不引入客户端可伪造的结果令牌。

**改动点**：

1. `POST /settings/tmdb/test`继续在服务端顺序测试V4和V3并保存完整结果，响应明确返回两个通道各自成功/失败及最终生效通道；前端不再调用两个通道后自行拼接保存。
2. `test_imdb_connection`调整为测试完成后由服务层调用`save_imdb_connection_test_result`，成功和已知失败均保存；AI现有服务端测试并保存行为保持。
3. 前端移除`saveTmdbTestResult`、`saveImdbTestResult`及相关客户端类型/调用。原`POST /tmdb/test-result`和`POST /imdb/test-result`在一个兼容版本内返回410与明确迁移提示，后续主版本删除；不得继续接受客户端结果。
4. `/tmdb/test/{channel}`可保留为仅测试单通道的管理诊断接口，但它不更新“当前配置已验证”状态；只有完整`/tmdb/test`结果可使`matches_current=true`。
5. 服务端保存使用测试开始时捕获的配置快照；测试过程中配置被修改时，结果可保留但`matches_current=false`，不得把旧配置结果写成当前已验证。

**验收断言**：

- 伪造两个旧`test-result`写接口均返回410，数据库不变。
- 完整TMDB测试分别展示V4/V3结果并只由服务端写一条权威记录；IMDb与AI同样由实际测试调用保存。
- 测试期间修改配置后，历史结果与测试时快照绑定，读取时`matches_current=false`。
- 前端不再发结果保存请求；连接失败仍显示明确通道、错误类型和失败提示。

### 5.4 M3（重写）覆盖路径不重算敏感词，策略升级不可见

**原 M3 已撤回**：我原写的"改名洗白"链经复核不成立（`PUT /rename-previews/{id}` 要 `rename:execute` 非 `metadata:submit`；且拦截记录只在敏感词**已命中**时产生，`_snapshot` 要求解析标题与 `match_title` 一致，改 `target_name` 动不了这些字段）。

**我在首轮回应中提的"策略漂移必然被拒绝"同样已更正**（Codex 二轮指出，我已复核）：`_snapshot` 只在 `prepare`（`external_submission_authorization_service.py:139`）与 `submit`（`:194`）被调用，**block 创建路径不计算指纹**。因此指纹基线是在 **`prepare` 时**按当时策略生成的：

| 策略变化时点 | 实际结果 |
| --- | --- |
| block 创建后、`prepare` 之前 | 变化被**吸收**进 prepare 的基线 → `submit` 通过 → **零告警、命中词不重算**（← 本项核心风险窗口） |
| `prepare` 之后、`submit` 之前 | 指纹不一致 → `submit` 拒绝并要求重新确认（门禁有效） |

验收场景因此**必须显式区分 prepare 前后两个时点**，不能用"新增词后直接 submit"掩盖签发时点。

**本项的真实缺口**（立项依据，已取证）：

- `external_submission_guard.py:248-251`：带 `authorization` 时 `consume()` 指纹后直接 `return ..., allowed=True, matched_words=[]`，**不调用** `_match_sensitive_words`；
- `matched_value_masked` 只在 `_save_block_record`（`:159-170`）写入，授权/提交路径从不更新；
- 授权快照 `public`（`external_submission_authorization_service.py:116-118`）不含当前匹配词。

**风险窗口（上位依据）**：`prepare` 用**签发当时**的策略生成指纹基线，因此 **block 创建后、`prepare` 之前**新增的敏感词会被基线**吸收**，`submit` 比对通过 → **零告警、命中词不重算**。这是本项核心。

**失败场景**：block 因敏感词 A 创建 → 运营方**新增**命中同一文件的敏感词 B → 用户点"确认覆盖"（`prepare`）→ 授权快照用的是含 B 的新策略，但**从不计算 B 是否命中该载荷**，`submit` 通过 → **提交成功且无任何提示**，`matched_words` 返回空，`matched_value_masked` 仍是创建时的 A。结果是：**用户决定"覆盖 A"时，实际执行的是"覆盖 A 与 B"**，却从未看见 B；审计也留不下"本次命中了哪些当前生效的词"。

> **原失败场景（提交被拒但提示笼统）已作废**——那只适用于 `prepare` 之后的变化，而那条路径的指纹门禁本身是有效的。请勿按已作废表述实施。

**改哪里**：`external_submission_authorization_service.py`（`_snapshot`、`prepareExternalSubmission`、`submitExternalSubmission`）、`external_submission_guard.py`（许可路径）、`frontend/src/views/SettingsView.vue`（确认弹窗）、`frontend/src/api/client.ts`（类型）。

**改动点**（**不废掉 F01 已批准的人为覆盖例外**，只补知情与留痕；下列约束已按二轮复核收紧）：

1. `prepareExternalSubmission` 按**同一份有效策略快照**计算文件名、路径、解析标题的当前命中，返回**脱敏词 + 命中数量 + 策略版本/摘要**；
2. 该确认信息**归属于该次授权**（每次授权一份不可变记录），**不得**用当前命中覆盖历史证据：`block.matched_value_masked` 代表**原始拦截原因，必须保留**；若要展示最近核查结果，**另定义字段/记录及其语义**；
3. 因 `block.updated_at` 已参与指纹，**不得**把"修改历史字段"当作无副作用的展示变更；若确需更新，必须明确事务顺序及其对其他 `prepared` 授权的影响；
4. **脱敏字符串可能碰撞，只用于展示**：不得靠脱敏集合相等来证明策略/载荷一致——一致性继续依赖原有私密配置摘要与完整载荷绑定；普通服务配置变化**不得**一律提示成"命中新敏感词"；
5. 前端必须同步：API 类型、确认弹窗展示当前命中（现弹窗仅显示文件/路径/解析结果/服务/有效期）、国际化文案与展示测试；**仅改后端两个文件不算完成**；
6. **不要**因命中当前策略就拒绝人为覆盖；**不要**把未脱敏词写入快照/审计/日志；**不要**改 `privacy.*` 的指纹覆盖（它是正确的）。

**验收断言**（必须**显式区分 prepare 前后两个时点**）：
- **场景一（prepare 之前变化）**：block 创建命中 A → 新增命中同一文件的 B → `prepare` → 断言返回的当前命中**含 B**（脱敏）；确认后可通过；断言用户可见提示包含"当前策略命中 N 词"而**不是**通用"配置已变化"。
- **场景二（prepare 之后变化）**：`prepare` 后新增命中词 → `submit` → 断言**拒绝**且**零外部调用**（TMDB/AI transport 未被调用），授权仍为 `prepared`，重新 `prepare` 后可完成。
- 断言每次授权有**独立、不可变**的确认信息；`block.matched_value_masked` 作为原始拦截原因**未被覆盖**。
- 断言**脱敏碰撞**用例：两个不同词脱敏后相同，仍不得据此判定策略/载荷一致。
- 断言未命中任何当前策略时行为与今日一致（覆盖仍可完成，不新增拒绝）。
- 断言历史拦截证据保留、拒绝/未知不自动重放。

## 6. B4：状态机与失败可见性

- ~~**M1**~~ **已撤回，不实施**：`_publishScanStage`（`scan_service.py:453-454`）自身先 `commit()` 再 `BEGIN IMMEDIATE`，而 `:832` 的 `requireScanLease` 与最终状态写入都在**同一写事务**内，`recoverExpiredScans` 的写无法插入其中。原竞态场景不成立。**特别注意**：不要采用"进程存活就不标中断"——那会让授权过期失效。
- **M2** `scan_service.py:898-904`：保留**原始异常诊断**（原错误日志 + 关联 ID），不得被二次校验失败覆盖。**不要**采用"先写终态再复核"（Codex 复核意见）：状态更新需 CAS，且**不能越过已失效租约去覆盖新执行**。断言：硬失败 + 租约同时失效时，任务仍带**真实** `error_message`，且不影响后续新执行。
- **M5** `log_maintenance_service.py:49-57`：失败意图不独占整页，补公平重访/重试预算。修订说明：末页会归零，**不是永久跳过**；风险是持续新增下的长时间饥饿。断言：构造持续新增，断言坏项最终被重访而不是被永久越过。
- **M6** `scan_service.py:184-200` / `scan_recovery_service.py:53-55`：让"`running` + PID 未知"的交接窗口**可只读列出**，供人工核对；**不自动清槽**。修订说明：`inspectInterruptedScans` 已能列出已中断/完成的持令牌槽，缺的只是这一窗口；不能称所有 worker 崩溃都无 CLI。断言：构造该类行后能只读列出，且状态未被自动改写。
- **M7** `remote_operation_service.py:349-352`：修订为——明确分页**不是跨请求快照**；需要时改为稳定 ID 排序或在同读快照内返回。**不要**承诺可变排序键下 keyset 能零漏行。断言：翻页期间另一行状态变化时不漏、不重复（或在文档与响应中明确该限制）。
- **M8** `core/logger.py:23,211`：先补 **Windows 多进程**轮转/清理复现，再在"单写进程"与可靠的跨进程方案之间选。**注意** `delay=True` 只延迟打开文件，**不解决**跨进程竞争。断言：多进程并发写 + 清理时无记录丢失、无清理假死。

## 7. B5：批量语义与错误分类

- **M20** 把控制器内的 `record_audit_event` 移入对应 service 并传 `connection=` 同事务（`api/tasks.py:104`、`api/auth.py:123,137,166`、`api/settings.py:145,309`、`api/rename_rollback_plans.py:46,100`）。断言：审计写入失败时归档整体回滚并返回 5xx（不是 500 假失败）；成功路径只落一条审计。**注意**现有测试 patch 的是 `app.service.audit_service.record_audit_event`，因 import 绑定拦不住控制器副本——修完要补能拦住的测试。
- **M21** `api/pending_files.py:116-129`：部分失败改为返回权威任务结果（200 或 207/409 带已提交计数），400 只用于预检拒绝；**不要**把 `sqlite3.Error`/`OSError` 包装成业务 `ValueError`。断言：第 1 项已提交、第 2 项目标存在时，HTTP 状态与 body 计数一致。
- **M22** `rename_operation_service.py:611`：引入系统性失败标记，环境级错误（`sqlite3.Error`、凭据/密钥错误、`ExternalBudgetUnavailable`、协议不支持）在提交已写入的逐项行后抛出循环。断言：凭据密钥缺失时整批中止，且已写入的逐项结果保留。
- **M23** `pending_file_service.py:276,280`：`detail` 只回文件名，完整路径留在服务端日志。
- **M25** `main.py`：注册 `RequestValidationError` / `HTTPException` / `Exception` 处理器，统一 `{"detail": "<字符串>"}` 形状并用 `app.core.logger` 记关联 id。断言：未预期异常产生 5xx 且 `logs/error.log` 有记录。
- **M26** 统一 422/400：让 `RequestValidationError` 处理器输出字符串 `detail`（或让 `apiErrorMessage` 同时处理数组）。断言：`page_size=500` 在 scan-jobs 与 rename-previews 上返回**同一**状态码与同形 body。
- **M30** `preview_service.py:1392-1404`：删除 `/metadata-match/all*` 两个端点，或加 `LIMIT` **并显式检出超限**（取 `batch_limit + 1` 或先 `COUNT`，超限即 409），**不得静默只处理前 N 项**；或改为 202 受理。**注意**：无当前 UI 调用方**不等于**无其他客户端，删除或改语义前须确认兼容性与回退，本批不擅自变更公开接口。

### 7.1 M29 单记录返读

`preview_service.py:582-623`提交后用`_preview_by_id`（`:730`）单记录返读，替代无过滤`list_rename_previews`。验收：10万条规模不加载全表；并发排除时不得对已提交的编辑返回400。

## 8. B6：前端一致性与请求代次

统一采用项目里已存在且已测试的模式（`TaskGovernanceView.vue:76-100` 的 `taskRequestVersion` + `viewActive`）作为标准：

- **M11** `TaskGovernanceView.vue:107-126`：`loadRemoteOperations` 加版本守卫。断言：慢 R1 落地时不得覆盖快 R2 的行。
- **M12** 同文件：回写 `paginationStore.setPage("webdav-recovery", result.page)`；`items.length === 0 && currentPage > 1` 时用钳制后的页重取一次。后端 `remote_operation_service.py:322-357` 与 `audit_service.py:190-232` 补页上限钳制（对齐其余 6 个端点的做法）。
- **M13** `SettingsView.vue:499-517, 1080-1091, 537-548`：加版本守卫，且**过期响应不得回写 `setPage`**。
- **M14** `MediaSourcesView.vue:433-550`：四个写路径补 `catch` + `ElMessage.error`，失败时关闭清理对话框。（更正：原文"失败前预置 90%"有误——`cleanupDialog.percentage = 90` 在 `:422`，位于 `await work()` **之后**，失败时不会执行。真实影响是异常使清理对话框残留、无任何提示，且删除失败也可能留下进度框。）
- **M15** `client.ts:1465-1597, 2123-2128`：全部经由 `apiErrorMessage`；或给拦截器加 403 分支输出本地化文案。
- **M16** `RenamePreviewsView.vue` / `ScanResultsView.vue` / `ScanJobsView.vue`：`watch` 相关 `route.query` 键并重新应用/清空筛选。**不要**用 `<RouterView :key>` 一把梭（`RenamePreviewsView.vue:1465-1466` 依赖自身 `router.replace` 不重建组件）。

- **会话代次（二轮 05）**：除清 store 与停 poll 外，必须增加**会话代次/请求失效**——用递增 session generation 标记请求，登出或切换用户后使在途响应作废。断言：A 发慢请求 → 登出 → B 登录 → **A 的响应不得回填数据或分页**。（说明：本项目是共享工作区，不是用户私有数据模型；此处只做隔离，不引入用户所有权/多租户语义。）

前端每批都要跑：`frontend:test` + `frontend:build`（Node 24）。

## 9. B7：卫生与文档同步（**范围已收窄至 L3/L4/L5/L7，不要跳**）

1. **L3** `.gitignore` 补 `.reasonix/` 与 `.codegraph/`；确认 `git status --short` 不再出现这两个目录下的文件。（仅添加忽略规则，**不要删除**另一工具的资料目录。）
2. **L4** `webdav.py:139-142` 改为大小写不敏感地识别 `Destination` 并统一规范化；补"重复键/大小写变体"回归。说明：当前生产 `move_file` 用大写、不可从外部直接控制 `extra_headers`，故属**防御性加固**，不是现成凭据外泄链。
3. **L5** `webdav.py:226,229-230` 为 412 与 ETag 校验失败各给一条专用文案（"源版本已变化或目标已存在；请重新干跑"）；**不要**把全部 412 断言为仅源变化。
4. **L7** 仅记录：`data/mediaai.sqlite3` 仍是 `schema_version=30`（**属我这台实例的事实，不代表其他实例**）。**未经用户明确同意不要启动会触发迁移的进程。**
5. **文档同步（关键）**：把报告第 4 节的 5 条错位逐条落到文档或代码，二选一：
   - 4.1/4.2：若同步入口保留 → 在 `project-design.md` 与 `M1-design-manual.md` 明确标注"遗留同步入口，可能绕过执行槽，不推荐使用"；若删除 → 从文档中移除。**注意**：仅写"不推荐使用"**不能代替** H1/H8 的安全修复或明确批准的兼容决策。
   - 4.3：在 `project-design.md` 第 11 节补一句"默认密码未改时仅登录提醒，服务端不禁止高风险操作"，与 P02 政策对齐（避免下次审查再次误判）。
   - 4.4：无需改文档，`fnos-ghcr-docker.md:186-195` 已正确。
   - `remediation-plan-2026-10-01.md` 第 6 节补上本批的修复编号与证据路径。

> **已移出本批**：**L1**（私网/白名单——禁止私网会破坏 188 NAS 与本地 AI/代理，需部署边界决策）与 **L6**（登录限速与出厂密码——"只提醒不改密"是 P02 已确认政策）。两者见第 11 节决策项，**本批不实施**。

## 10. 完成定义（DoD）

一批算完成，必须同时满足：

- 该批"验收断言"全部有新增或更新的自动化测试，且**不是**把现有错误行为写进断言（报告已指出 `test_pending_files.py:95`、`test_pending_move_queue.py:159` 正是这种反例，改动它们时要说明）。
- **门禁按实际变更范围选择**：
  - 触及后端 → 跑后端套件，并报告「**发现总数 = 通过 + 跳过 + 失败 + 错误**」四项，新增回归会使总数增长（**不要固定套件数量**，基线 747 只是修改前的对照）；**跳过不得记为通过**。
  - 触及前端 → 跑 `frontend:test` 与 `frontend:build`（Node 24）。
  - 两者都触及或进入集成阶段 → 两者都跑。
  - 每批都跑：`npm.cmd run check:encoding` 与 `git diff --check`。
  - 纯后端小批**不要求**重复无关的前端构建。
- 行为变更涉及的文档已同步（`project-design.md`、对应阶段设计手册、必要时用户手册）；若是决策项，则先记录决定再动代码。
- batch 记录写出：改了哪些文件、验收断言、测试命令与日志路径、剩余限制。
- **未提交、未发布、未升级版本、未动正式实例。**

## 11. 决策项（先决策，不要实施）

以下条目经复核后确认**不能按发现初稿直接改**：它们要么与已确认政策/目标设计冲突，要么需要产品与部署边界决策。请项目维护者先给出决定，再决定是否纳入批次。

| 项 | 冲突或缺口 | 需要决定什么 |
| --- | --- | --- |
| **M17** | `project-design.md:186` 已明确"失败或未知停止后续项"，实现与该设计一致；但与 `agent.md:95` 的通用规则不一致 | 三类失败（确定未执行 / 系统级 / 结果未知）是否采用不同策略？改实现还是改设计？ |
| **M24** | "添加任意合法本地源"可能是有意能力；`MEDIAAI_MEDIA_ROOT` 只是默认起点 | 是否需要部署浏览根/白名单？会不会破坏现有用法？ |
| **M27 / M28** | 统一信封会改公开 JSON；旧列表接口兼容性未确认 | 是否引入 `response_model`/统一分页命名？旧接口删除还是封顶？ |
| **M9 / M10** | M10 的默认密钥位置正是 `project-design.md:270` 已确认基线 | 按 P04 安排历史副本治理与备份策略，不搬移现有密钥 |
| **L1** | 禁止私网会破坏 188 NAS 与本地 AI/代理 | 私网/白名单的部署边界政策 |
| **L6** | "只提醒不改密"是 P02 已确认政策 | 是否加登录退避/限速；是否拒绝出厂密码（属政策变更） |

另：H5、H7、H11及M3重写范围已在本计划定稿：H5采用管理面最小权限，H7采用服务端实际测试并保存，H11采用现有两项直接用户权限控制人工关闭，M3按prepare前后时序补每授权知情展示与审计。它们不再属于第11节开放决策。

## 12. 明确不要做的事

- 不要为让测试通过而放宽安全判定（尤其 H2/H3 的成功条件、H8 的槽位判定）。
- 不要自动重放或重试任何结果未知的文件动作。
- 不要把无令牌 `running` 行直接删掉（那会让未知的文件动作失去证据）。
- 不要引入 ORM、不要换 UI 组件库、不要新增前端依赖。
- 不要在本机默认数据库上跑会触发 schema 30→34 迁移的进程。
- 不要"顺手"修不在当前批次范围内的文件。

## 13. 执行检查点

实际进度只在此处和对应工作日志追加，不用历史测试数替代当前证据。

| 项 | 状态 | 当前证据 |
| --- | --- | --- |
| 计划定稿 | 完成 | 第三轮终检8处冲突已修订；H4/H5/H7/H11设计已确定 |
| X0 / L2 | 完成 | 定点4项通过；后端全量749项=739通过+10跳过，0失败/错误 |
| B1 / M4 | 代码与定点测试完成 | 远端幂等服务测试12项通过；待B1整批全量回归 |
| B1 / H4 | 完成 | schema 35、原子认领、全路径CAS、后台心跳及跨租约迟到响应回归已完成；定点组56项、后端全量753项通过（10项受控WebDAV集成测试跳过） |
| B1 / H8 | 代码与定点测试完成 | schema 36、同步执行归属、历史未知槽只读核对与停服释放完成；定向组144项通过 |
| B1 / H1 | 代码与定点测试完成 | 遗留扫描入口改为202后台受理；后端M1 API 4项、前端客户端27项通过 |
| B1 / H2-H3 | 完成 | 缺失强ETag和端点能力未验证均在传输前拒绝；仅201成功，204/异常2xx保留待核查意图；组合130项、后端全量764项通过（10项受控集成跳过） |
| B1 / R37实机 | 安全收口完成 | 绿联端点已确认不支持可用的条件MOVE，保持只读；真实安全子集8通过+1有意跳过并完成独立清理 |
| B1 / M19 | 完成 | 本地重命名和回滚增加移动前源身份复核、移动后目标身份确认；故障注入4项、组合回归65项及后端全量768项通过（全量10项受控集成跳过） |
| B1 / WebDAV能力验证入口 | 完成 | 随机隔离目录验证错误ETag、正确ETag及`Overwrite: F`语义；结果绑定端点和凭据版本，清理失败不解锁写入；前端188项及构建通过，浏览器双视口无横向溢出 |
| B1整批 | 完成 | 后端全量774项=764通过+10跳过+0失败+0错误；下一批进入B2不可恢复状态与假审计 |
| B2 / H9 | 完成 | schema 37记录实际目录项与动作阶段；大小写改名前后、硬链接和人工证据结案已覆盖；组合171项及后端全量779项通过（10项受控集成跳过） |
| B2 / H10 | 完成 | 同步重命名和回滚仅在可执行前态进入审计链，重复终态请求直接返回权威结果且不新增成功审计；同批全量通过 |
| B2整批 | 完成 | H9/H10验收及文档已同步；下一批按依赖进入B8/H11未知审计意图容量与人工关闭 |
| B8 / H11 | 完成 | schema 38、80%容量告警、未知意图分页、双权限幂等人工关闭及任务治理页已实现；后端全量784项（774通过、10跳过）、前端189项及构建通过；正式实例未迁移 |
| B3 / H5-H7-M3 | 完成 | 设置明细权限、AI readiness、会话工作区重置、服务端权威连接测试与每授权当前策略证据已实现；后端全量790项（780通过、10跳过）、前端190项及构建通过 |
| B4 / M2-M8 | 完成 | 扫描首因保留、维护游标周期回访、PID未知运行槽只读核对、远程操作稳定ID分页及Windows跨进程日志轮转已实现；组合110项、后端全量795项通过（10项受控集成跳过） |

详细命令、日志与限制见[实施工作日志](../../work-logs/progress-2026-10-08-independent-review-remediation.md)。
