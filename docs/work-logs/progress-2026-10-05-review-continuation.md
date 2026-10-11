# 2026-10-05 审查修复续做记录

类型：实施与验证过程记录。权威审查范围仍以主修复计划及代码、设计、可观察性审查清单为准。

## 1. 恢复与启动

- 承接C3第十五节，应用1.0.1、schema29；不提交、升级版本、发布或维护正式实例。
- 用户要求先启动服务再继续；已核对5173/8970均无监听、b2/ui-config.toml隔离路径及启动/停止脚本。
- 停止脚本按PID文件及端口强制结束进程，未直接执行，避免误杀旧PID复用的其他进程。
- 隐藏后台启动被执行策略拒绝，命令未启动成功；不换执行路径绕过。无本轮健康200、浏览器异步扫描截图或真实组合缩放证据。
- 测试使用a2/config.toml及临时数据库/合成媒体，Node24.15.0。不记录真实账号凭据。

## 2. C3执行槽终态边界

- 发现claimScan只检查running/interrupted带令牌任务。子进程已提交completed但尚未退出时，另一worker可认领下一任务，违反单槽约束。
- 新回归首次因测试重复媒体路径触发唯一约束；修正数据后明确复现提前认领及终态槽不在维护预览中的问题。保留slot-red及slot-red-corrected两份日志，不将测试数据错误归为生产故障。
- claimScan改为任何非NULL execution_token都保留槽位，仍沿用BEGIN IMMEDIATE条件认领；监督者确认退出前不放行。
- 本机维护预览/释放支持interrupted/completed/partial_completed/failed。仍要求停服确认及PID明确不存在，运行中任务拒绝释放。完成、部分完成、失败的状态、计数和摘要保持不变；中断任务仍不自动重试。
- 原Python函数名及CLI参数保持兼容，无迁移、无ORM依赖。数据访问是sqlite3参数化SQL；认领检查为`SELECT 1 FROM scan_jobs WHERE execution_token IS NOT NULL LIMIT 1`。
- 20项扫描队列回归4.758秒通过：`fix-c3-slot-target-2026-10-05.log`，包含确认退出前不认领、三类终态摘要保留、运行中维护拒绝。
- 修复前恢复全量521项（511通过、10NAS跳过）115.364秒；修复后524项（514通过、10NAS跳过）112.835秒，分别见resume-full和slot-full日志。不将前者作为新增断言证据。

## 3. C1元数据Provider维护审计

- 核对到PUT /api/settings/metadata-providers/{provider}先保存配置，再在API写成功审计；后者失败时配置已提交但调用报错。
- update_metadata_provider_config新增可选auditContext关键字参数，配置与成功审计共用连接和事务。API传真实用户/IP/User-Agent并删除重复成功事件；失败验证审计原逻辑保留。
- 敏感字段沿用record_audit_event统一脱敏；服务调用未带用户时记system，不能猜测为admin。
- 接口、请求体和响应结构不变。调用示例：`apiClient.put('/settings/metadata-providers/bangumi', { enabled: true, timeout_seconds: 12 })`，需settings:write权限，响应仍是脱敏Provider配置。
- 无ORM层，不为本次审计引入ORM。原参数化INSERT ON CONFLICT与审计INSERT共用一次commit；审计异常关闭连接回滚配置及密钥字段。
- 新增服务故障回滚/脱敏上下文和API单事件真实操作者断言。首轮34项1个失败：预期admin而隔离客户端实际api-review，已修测试；复验及最新全量结果待下面终态补记。

专项复验34项10.363秒通过，见`fix-c1-provider-target-retest-2026-10-05.log`。最新标准全量`fix-c1-provider-full-2026-10-05.log`包含Provider补丁，终态待补记；524项全量早于Provider补丁，不混用。测试客户端弃用告警保留，不擅自替换测试依赖。

最新终态：标准`npm.cmd run backend:test`共527项（517通过、10NAS跳过）109.936秒通过，覆盖本轮槽位与Provider补丁；对应会话已结束。`npm.cmd run check:encoding`及`git diff --check`通过。端口复查仍无5173/8970监听，不声称服务已启动或浏览器通过。

### 下一修复单元代码核对

- AI配置档案的新增、切换、删除实际通过系统设置接口保存ai.provider_profiles/ai.active_profile_id，已进入update_setting_values同事务审计；不是独立的未审计Provider服务。尚需针对嵌套秘密与删除/切换添加显式证据，不能直接标为缺失实现。
- 回滚计划创建和干跑：rename_rollback_service的commit早于API成功审计，仍有已提交但请求失败边界；干跑冲突计数非零时API仍记success，需按业务校验结果记录。接续修改应保留旧接口并补事务失败回滚、真实操作者及冲突摘要回归。
- 远端恢复：remote_operations API在recover_remote_operation返回后才写成功审计，远端动作不能用简单数据库回滚解决；需复用持久化审计意图与补写，不把最终审计失败提示成需要重复恢复文件。
- 默认管理员：ensure_default_admin目前COUNT后INSERT并commit，没有同事务初始化审计；需核对多进程空库竞争及不保存默认密码的审计记录。这是本机初始化，不推断为已登录用户动作。

## 4. 前端与剩余门禁

- 本轮前端105项通过，类型检查及build1.42秒通过，证据resume-ui-tests/build日志；约1.42MB包告警保留。本轮未修改页面样式。
- 浏览器实际异步任务/日志、真实NAS、Linux不可中断NFS、多进程日志轮转及真实200%系统+125%浏览器缩放未验证，不关闭C3/F02/R26。
- 继续核对C1的AI配置档案、回滚计划创建/干跑、远端恢复及默认管理员初始化审计；D授权/重试/分页/易用性与E文档验收仍待。
- 恢复时先读本日志终态及主计划，核对最新会话、端口与代码，不重复已实现的异步扫描核心，不沿用旧服务健康。

## 5. C1回滚计划与默认管理员审计

- 回滚计划创建、条目保存、创建审计共用BEGIN IMMEDIATE事务；干跑状态/快照/计数与审计同事务。API只传真实用户、IP、User-Agent，不重复成功事件；原URL、响应结构及服务位置参数兼容，新增可选auditContext关键字参数。
- 干跑无冲突记success，部分可执行且有冲突记partial_success，全部冲突记failed；HTTP200表示校验请求完成，不表示全部条目可回滚。
- 默认管理员空库创建采用BEGIN IMMEDIATE串行核对，创建用户与初始化审计同事务；操作者为local_initialization，不冒充登录用户，不保存密码。关闭默认创建或已有用户时仍不重复创建。
- sqlite3参数化SQL沿用现有实现，无ORM层、无新迁移。关键SQL为BEGIN IMMEDIATE、原计划/条目INSERT及同连接audit_events INSERT、commit；任何审计异常关闭连接回滚。
- 专项36项11.280秒通过，见`fix-c1-rollback-bootstrap-target-2026-10-05.log`，包含计划创建/干跑审计故障回滚、冲突结果、API单事件真实操作者、默认管理员故障及并发单用户单事件。

## 6. C1远端恢复可靠审计

- POST /api/remote-operations/{itemId}/recover仍按原操作类型要求rename:execute或rollback:execute；业务响应不变。调用示例`apiClient.post('/remote-operations/123/recover')`。
- 复用file_action_audit_intents，task_type为remote_operation_recovery，不新建审计表；初始意图/pending审计失败拒绝调用协议。完成请求结果先固定outcome_json，再写最终事件；最终审计失败不伪报恢复未执行，HTTP200保持实际恢复结果并增加X-MediaAI-Audit-State: pending。前端已有通用拦截提示，未改页面样式。
- 恢复冲突仍HTTP409，参数/业务拒绝仍HTTP400；已创建意图的拒绝有失败结果快照，不再额外重复失败事件。权限不足在意图之前拒绝，不调用文件协议。
- 维护补写读取保存的动作/结果，不重新调用恢复或MOVE。最初方案允许未保存结果时从completed业务核对推断；二次审查发现它可能抢在当前请求结束前写成功，已收紧为缺少本次结果一律pending，不从任何业务状态猜测当前请求结果。
- 共用有界游标维护命令与周期调度。未保存结果且业务尚未完成的意图仍需人工核对，不宣称自动补齐全部进程崩溃窗口；审计意图长期容量/保留策略仍待C2核对。
- 跨恢复、原重命名/回滚、维护CLI、权限专项72项27.224秒通过，见`fix-c1-remote-audit-target-2026-10-05.log`，覆盖初始失败无协议调用、最终失败返回真实完成与pending头、补写幂等且不MOVE、未保存结果不猜旧失败、完成历史只读核对。随后追加锁冲突审计断言，需全量覆盖。

## 7. AI档案显式证据及验证检查点

- AI档案无需新增维护接口；新增测试确认多档案保存、切换生效项及删除旧档案与settings审计同事务，嵌套api_key不进入审计原文，审计失败保留原列表和生效项。
- 首轮46专项因合成密钥含非法字符在配置校验阶段失败，保留`fix-c1-ai-profile-audit-target-2026-10-05.log`；修正为合法合成密钥，复验运行中。标准全量`fix-c1-audit-tail-full-2026-10-05.log`启动早于该数据修正，终态另记，不能误报通过。

AI档案复验46项19.539秒通过。首轮全量537项160.580秒，9个错误（8个权限矩阵子场景仍mock已移走的API审计函数，1个非法测试密钥），10NAS跳过；日志保留，不记为通过。权限矩阵改为隔离runAuditedFileAction调用，仍断言403时既不创建意图也不执行恢复；新恢复集成测试负责真实审计边界。安全/恢复/维护专项27项10.928秒通过。第二轮全量运行时再次核对并收紧未保存远端结果的补写语义，之后需最新专项和全量，不混用早于该补丁的结果。
- 本轮业务改动全在后端；应用1.0.1/schema29不变，不提交发布，不启动或维护正式实例。上一节前端105/类型/build证据可说明前端未改，不替代本轮后端验证。

第二轮全量537项（527通过、10NAS跳过）159.608秒，早于缺结果不推断补丁；最终安全/恢复/CLI/维护专项30项10.902秒通过，见`fix-c1-audit-tail-final-target-2026-10-05.log`。最终全量`fix-c1-audit-tail-final-full-2026-10-05.log`运行中，业务代码已冻结，待终态后同步主计划和记忆。前端本轮105项复验通过，见`fix-c1-audit-tail-ui-tests-2026-10-05.log`。

最终终态：标准`npm.cmd run backend:test`537项（527通过、10NAS跳过）155.371秒通过，覆盖收紧后的结果补写语义及锁冲突审计断言。所有测试会话已结束；编码/diff通过。上一轮527全量不作为本轮补丁证据。前端本轮105项通过，未改页面；服务未启动，浏览器验收仍未完成。

## 8. 下一阶段入口与未关闭风险

- C1本节计划内维护审计尾项已实现，仍不关闭R28：外部提交决定API在配置提交后单独写审计，下一D/F01改造时一并纳入同事务；原重命名/回滚审计的缺结果补写仍依赖任务状态，需要增加维护与请求并发的故障注入，防止旧终态抢先归属于新请求。
- D/F01只读核对：设置页的继续提交调用PATCH拦截记录，guard仅保存override_submitted，没有匹配调用；需绑定操作者、目标服务、源条目、有效载荷和有效期的一次授权，由实际请求消费，不允许永久绕过敏感词。已有ai_parse_service及preview_service两个guard消费入口，须覆盖AI与元数据，不能只改页面状态。
- 后续先处理上述原文件动作意图的并发补写证据，再进入D/F01一次性授权与真实外发；F03重试预算、F05分页、F06固定TMDB语义及UI实机继续。授权方案已确认，不重复需求访谈。
- 服务仍受执行策略限制，无本轮浏览器或NAS实测；不绕过限制、不删除正式日志，不把自动化通过当实机验收。审计意图容量与多进程轮转仍未闭。

## 9. C1原文件动作审计并发归属

- 两项隔离红灯明确复现：completed旧重命名任务在新请求结束前被维护写成功；新请求被拒绝时若业务任务running，只有pending而没有请求失败记录。证据`fix-c1-audit-race-red-2026-10-05.log`，不是验收通过。
- 所有文件动作意图统一只补写已保存outcome_json；缺本次请求结果一律pending，不从当前业务终态猜结果。进程崩溃且结果未落库时保留初始意图，人工核对门禁明确，不增加自动重放。
- 请求异常一律记录本次请求failed，即使业务当前是completed/executed/running；详情状态与计数保留为current_business_snapshot，摘要不把旧成功数量描述成这次执行成功。不更改媒体或原业务状态。
- 原并发补写测试改为先固定请求结果、注入最终审计故障，再双线程flush，仍验证最终事件只写一次；不会用无结果任务状态推断来让测试通过。新增重命名及回滚旧终态并发维护拒绝回归。
- 原67项专项20.068秒通过；补回滚场景及下一节决定事务后60项21.920秒通过，见`fix-c1-audit-race-target-2026-10-05.log`与`fix-c1-decision-audit-target-2026-10-05.log`。最新标准全量`fix-c1-audit-race-full-2026-10-05.log`运行中，最终统计待补记。

## 10. D/F01前置：外部提交决定审计

- PATCH /api/external-submission-blocks/{id}保留原接口与请求字段；仍需metadata:submit，保存决定服务新增可选auditContext，不改原位置参数。
- 决定状态、决定文本、操作者、时间与成功审计共用BEGIN IMMEDIATE事务。API只传真实用户/IP/User-Agent，不重复事件；审计失败回滚记录，不发生外部调用。
- 审计摘要明确是保存决定，detail.submission_executed=false；这次补丁没有把override_submitted变成真实匹配，不关闭F01，也不宣称已完成一次性授权。
- 数据访问沿用sqlite3参数化UPDATE与audit_events INSERT，无ORM层或新迁移。前端调用示例`apiClient.patch('/external-submission-blocks/12', { status: 'ignored', user_decision: '不进行匹配' })`，响应仍为拦截记录。
- API回归覆盖真实操作者单事件、审计失败状态/时间整体回滚及缺记录/无确认原因；与权限/重命名/回滚/维护60项专项通过。
- 下一D/F01交付要覆盖实际有效载荷及目标范围：AI发送文件名、路径、ParsedMediaName与生效Provider/模型；_parsed_from_preview实际支持原文件名、父目录标题、解析标题三种来源，不含edited模式。多源入口目前guard统一记tmdb，不能让一次TMDB授权自动扩大为所有外部服务。确认后真实调用、一次消费、内容/源/Provider变更失效、失败/未知不重放与审计状态需要整体验证。
- 现阶段不引入未消费的token模块或未接入页面的新接口，避免无效实现；继续在已授权方案内核对最小调用链再接实际外发。应用1.0.1/schema29，无提交发布/正式维护。

第9-10节最终检查点：标准`npm.cmd run backend:test`541项（531通过、10NAS跳过）147.420秒通过，见`fix-c1-audit-race-full-2026-10-05.log`；编码与diff通过，所有测试会话结束。本轮未修改前端，最近105项证据见第7节，不冒称本轮浏览器通过。主计划第3.1节已同步D/F01调用链、授权范围与验收顺序；下一步是实际授权/消费/调用接口与页面接入，不重复实现决定审计或继续做已验证的C1单点。未启动服务、未操作正式媒体/数据库/日志、未提交发布。
