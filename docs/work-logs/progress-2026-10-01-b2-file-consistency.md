# B2 文件一致性与恢复修复记录

## 范围

承接 B1 全量 384 项（374 通过、10 NAS 跳过），继续 R04/R10/R19/R20/R21。用户授权持续推进，不逐节确认；不提交、不发布、不操作正式媒体文件。

第一节先修远程重命名：网络调用前不持有 SQLite 写事务；每项单独提交；远端成功用 remote_applied 表示，业务明细与 completed 同事务提交，提交后才释放租约。恢复入口识别 remote_applied。

后续仍须覆盖回滚、索引、媒体源生命周期、待处理迁移、本地中断恢复和远端文件身份。当前未完成，所有结果以追加证据为准。

## 短事务第一节

- 重命名/回滚每项成功或失败独立提交；远程意图创建失败也释放租约，且不调用 MOVE。
- MOVE 成功记录 remote_applied；业务与 completed 同事务提交，数据库故障回滚业务更新、记录 recovery_required。远程锁在提交后释放。
- 恢复识别 remote_applied，恢复业务记录与完成状态同事务。后续完成摘要仍单独更新；辅助日志故障处理及 completed 历史一致性检查仍待完善。
- API 列表允许 status=remote_applied，前端类型、中文标签与筛选同步为“已移动待同步”，沿用既有状态标签，不改 UI 布局。
- 原重命名系列 64 项通过（运行早于新专项）。最新重命名 20 项通过，含两条远程 MOVE 时独立连接可取得写锁、前项已提交、业务故障留下恢复状态、意图故障不执行 MOVE。
- 恢复专项 10 项通过，含 remote_applied 不再次 MOVE、局部数据库写入后故障回滚。首轮一个断言使用了另一测试的路径，已改为记录实际原路径并加强回滚验证，不能把首轮失败当成业务缺陷。
- Node 24.15.0 下 frontend:test 86 项通过，类型检查/生产构建通过；仍有原有大包警告，不冒充浏览器实机验收。
- 后端全量正在运行，证据目标 `.codex/run-logs/fix-b2-short-transactions-2026-10-01.log`，未结束前不填写通过数字。

下一节先修 R04 已移动条目随之后续失败回滚的问题。仅逐项提交不能解决本地进程中断、目标覆盖竞态及跨盘复制失败，仍需持久化意图和恢复验证；R04/R21 不提前关闭。

短事务最终全量：389 项，379 通过、10 NAS 跳过，114.896 秒，证据 `.codex/run-logs/fix-b2-short-transactions-2026-10-01.log`。测试进程已结束。

## 远端身份第二节

实施过程中先补 R09 的远端身份边界，R04 保持下一节待办，没有误记为完成：

- WebDAV.get_file_info 只接受准确文件资源，取得真实 ETag/大小/修改时间；缺失修改时间保持空值，不临时用当前时间充当文件版本。
- 干跑保存 remoteIdentity，执行前重检。强 ETag 才能执行；弱值、无引号、通配符及控制字符不作为 If-Match。
- SharedPathContext 增加可选 expected_source_version，不改变 move_file 参数；重命名传递已保存版本，MOVE 发送 If-Match 与 Overwrite:F；远程意图也保存 source_version。
- 重命名专项先行 22 项通过（不含后补无强 ETag 用例），WebDAV 边界专项 15 项通过，含实际请求头、准确资源读取及非法版本不发请求。后补无强 ETag 用例已纳入正在运行的全量。
- 全量目标 `.codex/run-logs/fix-b2-etag-2026-10-01.log` 尚未结束，不填通过数字。部署说明第 9 节记录能力限制、状态及旧计划需重新干跑。

恢复仍不能仅凭 target 存在认定身份；回滚快照/条件写入、移动后目标版本和索引同步尚待接续。真实 NAS 可能不遵守 If-Match，必须专项验证，不能把合成协议测试作为实机结论。

ETag 首轮全量 394 项有 2 个测试错误：新增测试把上下文管理器括号写成 tuple（两次 subTest），不是业务断言失败。已修正；原日志 `.codex/run-logs/fix-b2-etag-2026-10-01.log` 保留，不能作为通过证据。随后重命名系列 70 项通过，28.645 秒。

## 迁移部分失败与远端索引第三节

- 待处理迁移每项成功后提交，后续目标冲突不回滚前项状态；成功返回提交后的 moved/目标路径，不返回旧 pending 数据。部分失败说明此前已提交的数量。待处理专项 6 项通过，2.569 秒。
- 此窄修复未解决 shutil.move 的覆盖竞态、跨盘复制故障与文件动作后数据库提交故障；R04 仍开放，下一节需要本地持久化意图/恢复，不把每项提交说成跨资源事务。
- 新 remote_file_index_service 统一远程重命名、回滚、恢复后的索引更新：旧路径历史状态，目标 active、实际大小/修改时间/版本指纹、关联预览。与业务状态同事务，不伪造本地 stat。
- 移动后先读目标元数据，再开始业务写事务；目标版本保存到 remote_operation_items.target_version，网络调用不放进 SQLite 写事务。
- 恢复自动重试需有匹配的 source_version，并发送 If-Match；补齐需 target_version（缺时使用 source_version）匹配真实目标。旧记录缺版本或目标已换文件转 recovery_required，不自动 MOVE/补齐。恢复专项 11 项通过，4.701 秒。
- 回滚执行仍未保存干跑版本，条件写入需要继续补齐；历史 completed 仍需只读一致性检查，不能提前关闭 R21。
- 当前全量正在运行：`.codex/run-logs/fix-b2-index-recovery-2026-10-01.log`；终端 session 60356。恢复先检查此会话或日志终态，不另起重复全量。暂无最终通过数字。

下一顺序：确认第三节全量→记录审查状态→本地意图/禁止覆盖/中断恢复→源生命周期 R19。C/D/E 尚未实施；用户要求连续推进，无需每节重新确认，提交和发布仍未授权。

第三节最终全量 397 项，387 通过、10 NAS 跳过，152.240 秒：`.codex/run-logs/fix-b2-index-recovery-2026-10-01.log`。上述进程已结束，不能继续轮询旧 session。

## 执行期源保护第四节

为避免本地恢复实施期间源配置被删改，先补执行期保护：

- 源修改/启停/单删/批删在 BEGIN IMMEDIATE 中检查扫描、重命名、回滚 running、有效远程租约和未完成远程恢复；有占用拒绝，不删历史或真实文件。
- 回滚 checked→running 原子认领，重复执行拒绝；编辑/排除/重建/匹配回填也检查回滚占用。
- 媒体源专项 23 项通过，8.500 秒；回滚专项 5 项通过，2.465 秒，含阻塞期间拒绝第二次执行、源禁用与预览编辑。
- 最新全量 `.codex/run-logs/fix-b2-source-guard-2026-10-01.log` 运行中，不填通过数字。R19 的源退役/历史快照保留尚未实现；既有“清空历史”路径不属于最终 P05 目标，不能关闭 R19。

下一节本地动作意图方案：在文件动作前持久化 prepared，动作后 applied，业务与 completed 同事务；跨进程中断只凭持久化文件身份修复数据库，身份不明转人工。禁止覆盖必须使用平台真正拒绝已存在目标的操作，不只用 exists 预检查。恢复维护入口默认预览，显式停服确认才修复，不自动删除/移动真实文件。此段是实施约束，尚未建表或实现入口，恢复时先看最新代码。

第四节最终全量 399 项，389 通过、10 NAS 跳过，135.629 秒：`.codex/run-logs/fix-b2-source-guard-2026-10-01.log`。测试进程已结束。

## 本地禁止覆盖原语第五节

- file_operations.moveFileNoReplace：Windows os.rename 拒绝已有目标；Linux 使用 libc.renameat2 + RENAME_NOREPLACE，不支持或失败不降级覆盖。macOS 当前明确拒绝，后续平台门禁需补已验证能力，不能说已经支持全部平台。
- 官方/原始依据：[Linux rename 手册](https://man7.org/linux/man-pages/man2/rename.2.html)、[Linux UAPI fs.h](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/fs.h)。特别是 NFS 上错误并不保证远端动作没发生，因此仍需持久化意图，不按异常自动二次移动。
- 专项 4 项通过，0.021 秒：Windows 临时文件真实移动/目标保护、Linux 调用标志和禁止 fallback、未知平台拒绝。Docker 命令不可用，未跑真实 Linux，不扩大结论。
- 已接入本地重命名及回滚，尚需系列复验。待处理跨盘迁移仍是 shutil.move，不能把本节覆盖保护套用于它。
- 本地意图/恢复未实现，schema 仍 22；下一步先补本地 journal，不跳到 C。正式文件未操作，无提交发布。

第五节接入后重命名系列 71 项通过，24.336 秒；接续本地 journal 初版系列 71 项通过，26.190 秒。两次覆盖范围不同，不能合并为全量结论。

## 本地意图与核对第六节

- schema 22→23 新增 local_file_actions 和 source/status 索引。保存动作类型/业务条目/源与目标/规范化占用键/dev/inode/size/mtime_ns/阶段；不补造旧历史意图。迁移 SQL 使用固定结构，无 ORM 引入。
- 本地重命名及回滚准备意图后才移动；applied 独立落盘；业务、索引和 completed 同事务。索引故障不能标记虚假完成，保留 recovery_required 并明确提示停服核对。
- 源变更及预览修改也检查未解决的本地意图；即使原批次失败，也不能修改记录破坏恢复关联。普通文件需稳定 inode，不处理目录/符号链接；身份不足拒绝。
- 本机 CLI `python -m app.recover_local_files --config <实例配置>` 默认只读，最多 200 项，limit 可设 1-5000；apply 需 --confirm-service-stopped。工具不检测/执行停服、不导入 app.main、不初始化数据库，不移动/删除文件。
- 核对结果区分 wouldRepair/wouldRelease（预览）、repaired/notApplied（实际）、manual/failed/hasMore；无路径/凭据回显。身份匹配且源消失才能补齐，身份不符或两路径都存在转人工，不自动二次移动。
- 维护修复与成功审计同一数据库事务，失败回滚；若尚有其他 ready 项，批次标 interrupted，不能伪报整个批次完成。每项提交，部分失败计数明确。
- 专项最新 6 项通过，1.916 秒，覆盖意图失败不移动、文件成功后业务失败、索引失败、目标被改、只读预览、停服门禁、审计故障回滚、未移动意图释放。v22 迁移专项已纳入全量。
- 全量正在运行 `.codex/run-logs/fix-b2-local-journal-2026-10-01.log`，session 44319；不填写终态数字，先查看该会话再续做。

待办：真实进程杀死（当前为异常注入）、Linux/其他平台实机、待处理迁移接入意图/跨盘排他复制、回滚干跑文件身份、历史 completed 只读一致性检查、P05 历史保留。当前 journal 覆盖 rename/rollback，pending_move 只具备核对结构，迁移仍未接入；不关闭 R04/R19/R21。

第六节最终全量 410 项，400 通过、10 NAS 跳过，117.485 秒：`.codex/run-logs/fix-b2-local-journal-2026-10-01.log`。v21/v22 升级及重复初始化包含在内。进程已结束。

实际以隔离配置执行 recover_local_files 只读预览：apply=false，所有计划/实际计数为 0，items 为空，返回 0。未执行正式实例恢复。恢复命令示例：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.recover_local_files --config config/config.toml
# 操作者完成停服后才允许数据库修复；命令本身不负责停服。
.\.venv\Scripts\python.exe -m app.recover_local_files --config config/config.toml --apply --confirm-service-stopped
```

下一节接入 pending_move；跨盘复制使用排他创建、限量流式复制、flush/fsync 和源/目标身份重检，保留目标身份才能核对复制后 inode 变化。复制中断保留源文件、不自动删除部分目标，进入人工核对。不把 cross-volume 异常注入当作真实跨盘平台验收。

## 待处理迁移意图第七节

- schema 23→24 为 local_file_actions 添加 target_identity_json，允许复制后新 inode 的目标身份；原 journal 不丢失或补造版本。
- pending_move 准备意图与 pending→moving 同事务；同路径占用拒绝并发。成功后 pending moved/目标路径与 journal completed 同事务；单项失败不回滚此前已迁移项目。
- 同盘使用平台禁止覆盖移动；EXDEV 才使用排他创建目标、1 MB 流式复制、flush/fsync、权限/metadata 保留。源/目标身份重检与目标身份落库后才 unlink 源；不按其他 OSError 随意退化复制。
- Windows ACL 保留提取到通用 file_permissions，历史工具保留原 _copyAccessControl hook；内容写入前复制 ACL。18 项历史维护回归通过，13.782 秒，未放宽权限或弹窗。
- 未生效且源身份未变的失败恢复为 pending；部分目标/身份不明保留 moving + recovery_required，不自动删除部分目标。移除操作拒绝 moving/未完成意图。
- 待处理专项 9 项通过，5.650 秒，含合成 EXDEV 成功、复制完成后业务失败对账、复制中断保留两端；不是实际跨盘验收。
- 全量 `.codex/run-logs/fix-b2-pending-journal-2026-10-01.log` 正在运行，session 86063，包含新 v23→24 迁移及排他复制覆盖保护测试。未填写结果。

发现交互待办：list_pending_files 当前只返回 pending，moving/待恢复记录会隐藏，应接续显示并禁用不允许的操作；前端真实入口尚待定位，不虚构 PendingFilesView.vue（不存在）。源生命周期历史保留和回滚快照仍未实施。下一步先确认本节回归、补待恢复可见性，再继续 B2 余项，C/D/E 不提前关闭。

第七节最终全量 415 项，405 通过、10 NAS 跳过，121.761 秒：`.codex/run-logs/fix-b2-pending-journal-2026-10-01.log`，进程已结束。

## 待恢复记录可见性第八节

- API 返回 pending/moving，存在未解决迁移意图时展示 recovery_required；AI、迁移、移除拒绝非 pending，清空只清理 pending。
- RenamePreviewsView.vue 待处理标签页添加状态列；待恢复行不能选择、AI 解析或删除。清空及迁移按后端实际数量/返回条目更新，不伪报整批成功。失败刷新保留原始错误；refreshFailed 暂无页面提示，留待体验修复。
- 前端 89 项测试通过，构建通过（既有大 chunk 提示仍在）。后端待处理 9 项通过；最新全量 415 项，405 通过、10 NAS 跳过，125.000 秒：`.codex/run-logs/fix-b2-pending-ui-state-2026-10-01.log`。
- 隔离实例 9071/5175 使用 ui-config.toml 和两条合成媒体记录，未读取正式配置或文件。登录验证用隔离库初始化的默认管理员，不保存密码、不改密码。普通行选择启用批量迁移，恢复行复选框和两个操作按钮均 disabled；没有调用外部 AI 或真实迁移。
- 1536×864：documentWidth=1521、bodyWidth=1505；1366×768：documentWidth=1351、bodyWidth=1335，无页面横向溢出。1366 恢复行操作按钮 right=1214/1258，均可见。控制台无 error/warn。截图 `.codex/test-artifacts/review-fixes/b2/pending-ui-1536.jpg`、`pending-ui-1366.jpg`。
- 以上为 CSS 视口模拟，不能替代 Windows 200%＋浏览器 125% 实机验收；笔记本尺寸仅参考。视口覆盖已重置，隔离服务会话 9442/40422 已停止，测试会话 71154 已结束。
- 下一节回滚干跑身份快照及条件 MOVE，之后继续历史 completed 核对、P05 历史保留和真实进程中断测试；不关闭尚有平台/恢复门禁的 R04/R19/R21。

## 回滚快照与恢复终态第九节

- schema 24→25 增加回滚条目 nullable snapshot_json；不补造旧快照。回滚干跑保存关联记录/源状态/文件身份；执行重新核对，本地意图验证预期身份，WebDAV MOVE 携带强 ETag 条件，远程意图保留 source_version。
- 旧 checked 回滚缺快照拒绝移动；文件/ETag/业务状态变化拒绝；running 回滚不能重新干跑。回滚与迁移专项 29 项通过，8.410 秒：`fix-b2-rollback-snapshot-2026-10-01.log`。
- 远程恢复若仍有 ready 条目，批次保持 interrupted；completed 历史只读核对业务及索引，差异或后续操作只报冲突，不重放 MOVE 或覆盖当前记录。未核对远端实时状态，不把本地一致性当作远端验收。
- 远程恢复及回滚专项 21 项通过，8.079 秒：`fix-b2-recovery-terminal-2026-10-01.log`。全量 421 项，411 通过、10 NAS 跳过，131.448 秒：`fix-b2-rollback-recovery-2026-10-01.log`。
- 真实 Windows 子进程在文件移动后、applied 前，以及 applied 后、业务提交前分别被终止；prepared/applied 持久存在，业务仍旧路径，停服核对可补齐，文件内容保持。8 项本地恢复专项通过，5.705 秒：`fix-b2-process-termination-2026-10-01.log`。仅终止测试自己创建的子进程；这不是 NAS/Linux/NFS/整机断电或真实跨盘验收。
- 下一步 P05 源生命周期历史保留及计划失效；任务清理目前只有归档、不物理删除任务，日志清理的恢复关联仍需 C2 检查。真实平台门禁仍不关闭。

## 源生命周期第十节

- schema 25→26：media_sources 增加 deleted_at/retired_root，scan_jobs 增加 source_invalidated_at，新增 media_source_history 保存不含认证信息的源根/名称/类型快照，不补造旧变更事件。
- 改根路径保留任务/媒体/预览/操作明细；旧扫描被标记失效，旧待处理、干跑与未执行回滚计划失效，旧索引清理。已完成动作历史不改为失败，不删除真实媒体。
- 删除源改为退役：保留源 ID 和关联，活动 API 不返回退役源，禁用并清除活动认证字段；原根保存在 retired_root 与历史快照，path 使用 reserved retired://ID 释放既有 UNIQUE 路径，原路径可建不同 ID 的新源。不重建源表或复用历史 ID，系统保留路径禁止用户配置。
- 历史预览不能编辑/重新生成、旧计划不能执行、已改根历史不能再生成新回滚；扫描创建在短事务内重新核对 enabled/path/updated_at，阻止就绪检查后源变化的竞争。历史查询仍保留，可执行入口与历史分开。
- 对外接口/参数名 clear_history_on_path_change 为兼容保留，但 true 的行为改为确认失效与保留历史，不再删除历史。cleanup_summary 旧删除计数为 0，附加 pending_files_invalidated/rename_plans_invalidated/scan_index_removed；前端提示和汇总显示实际失效/索引计数。
- 初版 75 项专项通过，21.930 秒。加强专项首轮 62 项出现 1 个错误测试断言（将 PreviewGenerationSummary 当作列表）；已改为 generated_count=0，不隐藏失败证据：`fix-b2-source-lifecycle-final-2026-10-01.log`。
- 前端 89 项通过，类型检查/构建通过，既有大 chunk 提示。全量 `fix-b2-lifecycle-full-2026-10-01.log` 会话 13823 运行中，包含真实进程中断及新增源竞争/迁移断言；未填写最终数字。
- 正式库未升级/退役/清理，无提交发布。P05 数据保留增长/归档保留策略留待 C2；完整实时日志/审计原子性继续 C1，平台及实机门禁保留。

第十节最终全量 426 项，416 通过、10 NAS 跳过，127.147 秒：`fix-b2-lifecycle-full-2026-10-01.log`。包含源就绪后变更竞争与历史不能创建新回滚的断言，进程已结束。B2 核心实施转入 C1；真实 NAS/Linux/NFS/实际跨盘与断电、正式升级和实机缩放门禁仍待，不视为整体发布验收完成。
