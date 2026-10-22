# CD2 持久化准入实施

类型：实施与演进。日期：2026-10-16。范围承接[C0基础实现](c0-implementation.md)，本批不开放真实扫描或写操作。

本文保留 schema40 预算批次的实现与测试快照。后续 schema41 独立 worker、盘点队列和流式入库进度见[C1 实施](c1-inventory-implementation.md)；下文“下一批”描述当时边界，不代表后续源码尚未实施。

2026-10-22诊断补强：终端SQLite/OS存储错误仍安全停止，接口错误码保持 `storage_unavailable`；应用日志只记录connect/begin/prepare_read/body/commit阶段、读写模式、异常类别及SQLite数值码，不输出SQL、数据库路径、异常正文或Token。准入允许的短锁等待不在每次50ms尝试时写日志，避免正常等待刷屏。复现与回归见[审查修复记录](../../work-logs/progress-2026-10-22-review-fixes.md)。

2026-10-18续作修正：显式暂停始终保存manual_pause，不再沿用下文历史版本“保留已有错误原因”的行为。自动失败、目录动作/恢复的未知响应及worker回收不能覆盖管理员暂停意图；冷却只延长，原RPC失败原因仍保留在账本和审计，不因原因展示优先级改变而清除未决事实。见[本次复核](../../work-logs/review-2026-10-18-c3-c4-followup.md)。

## 本批决策

- 尚未验证真实云账号映射，先使用全实例单一CD2预算池（比单账号更严格），不允许按分类、端点或别名创建并行池。跨主机共享同一个SQLite实例不在支持范围。
- schema40增加独立预算池、只读批准会话、单个在途租约；不复用AI/元数据HTTP预算表。无端点明文、密码或API令牌入库。
- 每次实际RPC发起前计数，默认最小间隔2秒、并发1；会话批准总RPC数1至1000、有效期30至900秒，续用不重置计数或期限。
- 默认失败冷却300秒，仍须人工恢复，不因冷却到期自动放行。配置只能取不低于2秒间隔、不低于60秒冷却；多进程持久化采用较严格值，不靠旧进程放宽。
- 租约分reserved/sending/unknown：未发送失败可回收；已发送未明确完整成功则隔离租约并暂停池。正常截断也是部分观察，取消传输不等于云端停止。
- PID存活、超时、租约年龄均不能自动清除unknown；本机维护须确认相关客户端已停止、远端静止，并核对原进程已退出及租约身份。清理只释放槽，不重试原请求、不重置会话预算，随后仍须显式恢复池。
- 管理接口只查看/暂停/恢复预算，不提供远端RPC入口；会话批准和连接器工厂保持内部调用。暂停和恢复写入同事务审计；原始gRPC错误不入预算日志。

## 必测与检查点

- [x] schema39升级、新库、重复初始化及旧业务数据保留。
- [x] 跨进程单槽、共享间隔、未发送不计数、发送总预算、到期、时钟回退。
- [x] 异常/截断保留租约、跨重启暂停、活进程拒绝释放、显式核对与恢复、不重放。
- [x] 连接器与真实准入在合成TLS端点联测，网络期间无数据库事务。
- [x] 权限、审计失败回滚、容量边界、完整后端回归、文档与编码检查。

## 数据与状态

迁移SQL见[完整DDL](../../../backend/app/core/cloud_drive_schema.py)，参数化sqlite3调用见[预算服务](../../../backend/app/service/cloud_drive_budget_service.py)。项目没有ORM映射，不新增ORM或提供不存在的ORM示例。

| 表 | 持久化内容与边界 |
| --- | --- |
| cloud_drive_budget_pool | 固定id=1，ready/paused、原因、间隔、冷却、墙钟检查点及revision；状态不会在启动时重置 |
| cloud_drive_read_sessions | 规范UUID和幂等request_id、请求摘要、HTTPS端点摘要、批准根、操作者、批准RPC总量/已用量、期限及active/paused/exhausted/expired/closed状态；不保存远端令牌 |
| cloud_drive_rpc_lease | 固定pool_id=1，在途UUID、会话、PID/进程实例/本机标识、reserved/sending/unknown、固定RPC名及错误码；无自动过期回收 |

`ready`只表示池未暂停，不代表CD2已连通或当前就能发送：在途槽、间隔、用户权限、范围、会话状态及截止时间仍须通过。每次实际发起前增加used_rpc并更新last_started_at；SQL事务完成后才发出RPC。完成或取消网络流后进入独立结束事务，整个网络等待期间不持有数据库事务。

完整响应也必须先通过本地权限/根/版本/元数据校验才能标记成功。新别名、新分类、新端点及不同进程共用一个槽；令牌自检和版本RPC也计数。会话用尽或到期不自动补额，UUID幂等重放返回原会话，不刷新计数或期限。同一个request_id改参数得到request_conflict。

会话总数硬上限1000，达到后拒绝新增，不自动删除历史批准或幂等标识。C1需补充长期保留/压缩策略，不能通过清理普通日志重置请求预算。本批没有逐文件日志，也不为每次等待轮询写审计；批准、暂停、恢复及维护核对写状态审计。

会话exhausted/expired仅描述批准预算，不等于扫描成功或目录完整。任务完成和目录观察状态必须由后续独立队列管理，不能把额度耗尽标成盘点完成。

配置修改需规范重启所有相关工作进程。数据库中的较严格间隔/冷却不会因旧进程或较宽新配置自动降低；本批没有降低既有持久化政策的管理入口。准入发现墙钟回退则持久暂停，时钟未恢复到已有检查点前不能恢复；不通过重启抹掉检查点。进程唯一标识与租约UUID用于防止旧执行者删除新槽，本机核对还须确认部署仍为同机同PID命名空间。

预留槽后政策仍可能被其他进程提高；markSending会再次核对最新间隔，不符合时按未发送处理，释放预留但不计数。客户端排队只重试数据库忙/等待条件，不重试任何已经发出的RPC。

## 管理API

三个接口均需应用登录和`settings:write`；仅管理本地预算，没有远端网络调用、文件操作或扫描任务创建。已有`/api/cloud-drive/status`与`/scan-advice`仍使用`scan:run`，不改变其语义。

### GET /api/cloud-drive/budget/status

返回字段：

| 字段 | 类型与含义 |
| --- | --- |
| scope | 固定instance，不是已验证的云账号ID |
| state / pauseReason | ready或paused；固定原因码或null，不返回原始服务端说明 |
| cooldownUntil | Unix秒，可为0；到期仍不自动恢复 |
| revision | 非负整数，恢复时必须携带刚读取的值 |
| minIntervalSeconds / failureCooldownSeconds | 当前配置与持久化政策中的较严格值 |
| inFlight / leasePhase | 是否占槽；reserved/sending/unknown或null，不暴露PID、租约UUID或批准路径 |
| executionAvailable / writeAvailable | 本批均固定false，不能把池ready当成真实扫描入口已经开放 |

该查询只读，不创建默认池行、不回收租约、不更新观察时间。未配置规划根时仍可查看预算，但不能批准新的只读会话。

### POST /api/cloud-drive/budget/pause

无请求体。暂停后返回同上状态。重复暂停也增加revision，使另一个页面此前读到的恢复版本失效。不会强行终止已经发送的请求；已预留但尚未发起的请求须在发起前再次检查暂停状态。已有错误原因和冷却不被一次手动暂停覆盖。

### POST /api/cloud-drive/budget/resume

请求：

```json
{"expectedRevision": 1, "confirmRemoteIdle": true}
```

两个字段必填，revision为严格整数，确认值为严格布尔值，拒绝额外字段。只有无未核对租约、冷却已结束、版本未变化并明确确认远端静止时才恢复池，返回同上状态。不恢复原会话、不续期、不补额、不重放请求。确认不能代替本机租约核对。

错误：未登录401、缺权限403；拒绝确认400；版本冲突、未决槽、冷却未结束或时钟回退409；结构/类型错误422；表/文件不可用、损坏状态及审计写入失败503。响应不回显数据库位置、端点或远端原始错误。

PowerShell调用示例，仅操作本机MediaAI，不访问CD2：

```powershell
$headers = @{ Authorization = "Bearer $env:MEDIAAI_SESSION_TOKEN" }
$uri = 'http://127.0.0.1:8970/api/cloud-drive/budget'
$paused = Invoke-RestMethod -Method Post -Uri "$uri/pause" -Headers $headers
$paused
# 仅在已核对相关客户端和远端状态后执行；如存在在途槽，先按下节维护。
$body = @{ expectedRevision = $paused.revision; confirmRemoteIdle = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$uri/resume" -Headers $headers `
  -ContentType 'application/json' -Body $body
```

预期：无在途请求的普通手动暂停可按版本恢复；有未决槽或未过冷却则409，不能反复点击强制跳过。会话令牌由已有登录流程提供，不写入脚本或文档。

## 本机未决槽核对

### 目录维护期间撤销手动暂停意图

新增 `POST /api/cloud-drive/budget/release-directory-manual-pause`，只需当前 `settings:write` 管理权限，沿用应用登录；原 `/budget/resume` 仍拒绝目录保护，不改变旧请求的含义。

请求三个字段均必填，严格类型，拒绝额外字段：

```json
{"expectedRevision": 12, "confirmClientsStopped": true, "confirmRemoteIdle": true}
```

服务端在同一写事务中核对：池为paused/manual_pause、修订一致、时钟正常且冷却结束、无C1租约；恰好一个本机目录原执行持有保护且处于review、worker PID已清空；没有恢复活槽/worker PID或任何sending目录RPC。只清空PID不能代替正常worker回收。

成功仅将pauseReason改为request_incomplete，state仍paused，增加预算修订并写同事务审计。原目录槽、unknown及预算消费不改变，没有远端调用或自动核对。随后由原计划所有者另行批准新只读恢复预算，仍检查权限、策略、维护确认和冷却。该管理接口不替其他用户创建恢复。

运行SQL由参数化sqlite3执行，无ORM。核心更新为：

```sql
UPDATE cloud_drive_budget_pool
SET pause_reason = 'request_incomplete', revision = revision + 1, updated_at = ?
WHERE id = 1;
```

上面的更新只在前述事务门禁通过后执行，不能作为人工修改数据库的操作步骤。审计失败整笔回滚。未登录401、无权限403、确认不满足400、结构错误422、修订/冷却/租约/进程门禁409、存储或审计失败503。

PowerShell调用示例，管理员必须先实际核对目录worker退出与远端静止：

```powershell
$headers = @{ Authorization = "Bearer $env:MEDIAAI_SESSION_TOKEN" }
$uri = 'http://127.0.0.1:8970/api/cloud-drive/budget'
$status = Invoke-RestMethod -Uri "$uri/status" -Headers $headers
$body = @{ expectedRevision = $status.revision; confirmClientsStopped = $true; confirmRemoteIdle = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$uri/release-directory-manual-pause" -Headers $headers `
  -ContentType 'application/json' -Body $body
```

预期：paused/request_incomplete且directoryExecutionHeld仍为true；不是ready、不是目录恢复已完成。若拒绝则保留原状态，不循环请求、不删除保护槽。

默认只读检查，不导入app.main、不初始化数据库、不停服、不访问CD2：

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m app.recover_cloud_drive --config config/config.toml
```

返回预算及租约UUID、原PID、本机标识摘要和阶段。若数据库不存在或尚未升级则失败，不回退创建默认实例。先核对是否为同一台服务机器、是否同一PID命名空间，以及原客户端和CD2/115上游是否确已停止；工具不能代替远端人工确认。

仅在上述条件成立后执行，租约UUID取自刚才的实际输出：

```powershell
.\.venv\Scripts\python.exe -m app.recover_cloud_drive --config config/config.toml `
  --apply --lease-id $env:MEDIAAI_REVIEWED_LEASE_ID `
  --confirm-clients-stopped --confirm-remote-idle
```

预期：原进程已退出、同机标识及租约快照未变化时释放槽，池与原会话仍保持paused，并保留已用量、期限和审计。冷却之后还须调用恢复池API；继续原会话是另一项内部显式确认，不能替代对未知请求的结果核对。活PID、PID状态未知、异机标识、租约变化或缺确认均拒绝释放。

**禁止**按租约年龄、进程退出、数据库重启或传输cancel自行删除槽。更换容器导致主机/PID命名空间变化时，本工具可能拒绝核对；此时保持关闭，另行设计离线迁移核对，不伪造PID或修改数据库绕过。

## 后续接入门禁

内部`createCloudReadSession`负责批准总预算记录，`createBudgetedCd2Client`绑定会话、端点摘要及批准根。连接器的准入上下文必须返回permit，发起前调用markSending，完整校验后markSucceeded，截断则markIncomplete；不提供空准入兜底。

真实入口还缺独立工作进程和盘点队列。不能把生产探测直接放在长期FastAPI进程里：若请求未决，其原PID仍活着，维护必须拒绝释放。下一批应接入独立进程、固定批准范围与任务状态，再做目录流式入库/断点和容量治理；不把本批小目录收集器的10000条前缀当成大目录完整结果。

真实fnOS/CD2/115访问仍需批准小目录、HTTPS/CA和受限令牌。当前仅有临时库、合成TLS服务及本机进程证据，无真实云端流量或风控测量；C2/C3不开放。稳定版仍为v1.3.0/schema39，本批未发布源码的迁移目标为schema40，回退须用匹配版本备份，不提供反向删除表的脚本。

测试证据与未完成项记录在[工作日志](../../work-logs/progress-2026-10-16-cd2-budget.md)。

本批最终源码的Windows Python3.11完整911项通过（899通过、12项受控集成跳过，298.717秒），没有真实CD2/115验收；普通开发服务未启动，也未迁移正式数据库。
