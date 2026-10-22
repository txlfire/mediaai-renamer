# C3 受控隔离契约

文档类型：接口与数据设计。目标状态已确认，实施进度与验证边界见[实施计划](c3-quarantine-implementation-plan.md)。适用于开发schema55（本契约的隔离表结构仍为schema54阶段所定，新增配置表见[配置设计](../../design/cd2-database-settings.md)），不属于已发布v1.3.0能力。

## 1. 固定计划

独立cloud_quarantine_plans保存本人reviewId、来源观察修订、共同父根、目标既有目录ID、策略与路径世代、选定文件ID/大小/原名/源目标虚拟路径及快照摘要。正向只接收reviewId、1至5个entryIds、targetEntryId和requestId，不接受路径、凭据或规则证据。所有文件须同父，隔离目录位于该父根直接下一层。

复核必须当前且决策为suggest_quarantine，选定项仍存在于完整观察；服务端重新应用保留/保护与广告证据，固定目录代次和对象ID。目标名称必须符合服务端专用名称，不能借本功能整理任意媒体目录。计划不可改写；新输入使用新UUID。相同UUID同内容返回原计划，异内容拒绝。

本人直接权限cloud-quarantine:plan与scan:run控制计划；cloud-quarantine:execute另控制实际动作及恢复。禁用/撤权每事务重新核对。没有角色、没有借用C2权限或复核结论授予写权限。

## 2. 执行与恢复

cloud_quarantine_runs区分move与observe；独立worker令牌、PID/本机归属、RPC/字节/时间预算、取消、状态/修订及保护槽。cloud_quarantine_rpcs保存规范方法、固定项/路径、发送状态、已校验身份、固定结果及接收计数；无Token、正文、原始远端消息。snapshot和逐项证据有界存储，不依赖可清理缓存。

每动作只受理一次，POST不提供重放语义。连接自检后逐项核对源、目标不存在及两父身份，发送一条MoveFile，再查源/目标与两父。Skip显式存在，moveAcrossClouds=false、handleConflictRecursively=false。低层拒绝其他文件动作和未在固定集合内的路径。

每次Move发送前，须在限流等待结束后重新验证固定4条前检、对象身份及30秒时效。每轮最多`3+9×项数`应用RPC、180秒、1MiB元数据，首次连接和连续响应后均等待完整间隔，所有等待计入截止；无法容纳上限请求时拒绝受理。预算不是115上游请求或传输流量保证。

未知、身份变化、冲突或取消后只停止后续动作。任何发送过移动或仍有未决RPC的执行，进程退出不释放业务保护。全部明确后等worker回收，再由用户明确本地同步，推进全实例路径水位；关键审计失败回滚。原未知账本不改为成功。

新observe预算只查连接和精确源/目标/父身份，不列目录或读正文；最多五次独立批准。已进入恢复的计划不允许旧普通提交绕过恢复。位置歧义继续冻结；只接受最新、完整、有限时效且worker退出的只读证据。手动暂停只能走明确的本地管理解除分支，不能被未知回调覆盖。

observe独立上限`6+2×项数`应用RPC、90秒、1MiB，精确根最小Token只含列目录与运行信息。原move及更早observe的未知事实不改为responded；同步时仅增加核对关联。位置证据最长300秒，创建/提交都绑定原run修订和全局路径世代。动作开关关闭不阻止已存在未知的显式只读核对，但不允许新移动。普通日志写入只在原事务仍有效时降级；SQLITE_FULL等导致整笔关键事务失效时必须失败返回，不得继续隐式开启新事务。

反向计划仅从当前已同步位置生成，交换隔离与原位置，保持文件名和固定ID。它需要新UUID、权限、Token、维护确认和预算；目标已存在时不覆盖。反向准备本身没有云动作。

## 3. 接口目标

路径前缀`/api/cloud-drive`。GET均仅查本地；POST明确成功受理不等于远端完成。执行Token仅可信HTTPS和worker匿名stdin，失败后只查询原UUID，不自动重发。

| 接口 | 输入/语义 |
| --- | --- |
| GET `/quarantine/policy` | 当前权限、执行默认关闭、固定预算与专用目录名；无秘密 |
| GET `/quarantine-targets/{reviewId}?entryId=...` | 本人当前复核、同父/代次/配置专用名称的本地精确索引查询，不列云目录 |
| POST `/quarantine-plans` | requestId、reviewId、entryIds、targetEntryId；201本地固定计划 |
| GET `/quarantine-plan-requests/{requestId}` | 原UUID查询本人计划 |
| GET `/quarantine-plans`、`/quarantine-plans/{id}` | 本人分页摘要/固定详情，禁用或权限撤销拒绝 |
| POST `/quarantine-plans/{id}/cancel` | expectedRevision；仅未执行计划 |
| POST `/quarantine-plans/{id}/execute` | requestId、expectedRevision、policyHash、confirmMaintenance、confirmGlobalFence、apiToken；202一次worker |
| GET `/quarantine-runs/{id}`、`/quarantine-run-requests/{requestId}` | 原执行本地查询 |
| POST `/quarantine-runs/{id}/cancel` | expectedRevision；不撤销已发RPC |
| POST `/quarantine-runs/{id}/commit-local` | expectedRevision、confirmLocalOnly；仅本地同步 |
| POST `/quarantine-runs/{id}/observe` | 新requestId、expectedRevision、confirmMaintenance、confirmRemoteIdle、只读apiToken |
| POST `/quarantine-runs/{id}/review-worker` | 本人记录，当前scan:run、cloud-quarantine:plan、cloud-quarantine:execute及settings:write均满足；expectedRevision、confirmClientsStopped、confirmRemoteIdle；本机旧worker与owner确实退出后核对，不释原保护、不发RPC |
| POST `/quarantine-plans/{id}/reverse` | 新requestId、expectedRevision；只生成反向计划 |

结构与类型错误422，未登录401、无权限403、非本人404、未确认或HTTP传Token400，版本/策略/容量/进程/冷却/冲突409，存储/关键审计失败503。错误不回显输入Token、目录绝对位置或远端details。

产品使用参数化sqlite3，无ORM，不另引入ORM。实际DDL和调用示例见下。运行日志沿用operation_logs全局/单任务容量、保留天数和增量分页；清理运行日志不删除固定计划、动作/RPC/位置证据或关键审计。查询动态返回logAvailable，页面无日志时禁用入口并提示。

## 4. 固定计划数据结构

schema54固定计划最终结构如下。表不依赖观察缓存外键，不随清缓存删除固定证据。`logical_bytes`是UTF-8快照JSON大小，不是SQLite物理文件上限。1000计划/64MiB累计上限在同一个BEGIN IMMEDIATE写事务内检查；无定时删除或在线VACUUM。迁移验证记录独立保存在验收报告，不在本契约修改正式库。

```sql
CREATE TABLE IF NOT EXISTS cloud_quarantine_plans (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    review_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    inventory_revision INTEGER NOT NULL,
    path_epoch INTEGER NOT NULL,
    policy_hash TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 5),
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    logical_bytes INTEGER NOT NULL CHECK(logical_bytes BETWEEN 1 AND 65536),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'forward',
    origin_run_id TEXT,
    basis_epoch INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cloud_quarantine_plan_owner
    ON cloud_quarantine_plans(actor_id,created_at DESC,id);
```

ORM写法不适用本项目；对应服务使用参数化`connection.execute(..., parameters)`，不拼接用户输入。本人查询实际SQL如下，原UUID查询使用固定列名`request_id`，不是外部可指定的SQL标识符。

```sql
SELECT * FROM cloud_quarantine_plans WHERE id=? AND actor_id=?;
SELECT id,request_id,review_id,job_id,status,revision,item_count,created_at,expires_at
FROM cloud_quarantine_plans WHERE actor_id=?
ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?;
```

快照含复核内容摘要、观察修订/路径世代、共同父路径、专用目标目录ID/名称/世代及逐项源ID/大小/原名/目标路径/人工理由/规则证据。计划GET每次重新核对当前观察；失效仍返回原固定证据及invalidReason。分页只取摘要，不读取全量snapshot_json，也不把摘要status=prepared当作当前仍可执行。

## 5. 本地计划接口调用

policy返回planAvailable、executionEnabled、executionImplemented、canExecute、executionAvailable及固定限制。executionImplemented=true表示源码含执行链；executionEnabled默认false，executionAvailable还检查直接权限、策略与全实例保护。networkAccess=false/writeAvailable=false仅描述该GET或本地计划调用本身没有云操作，不是执行路由不存在。计划不接收apiToken、sourcePath或targetPath。

以下PowerShell示例只创建/查询本地计划；reviewId和entryIds必须替换为当前用户实际本地复核与观察中的ID。示例UUID、数字均为合成占位，不发送云请求。SESSION_TOKEN来自当前本机登录，不在文档保存。

```powershell
$base = 'http://127.0.0.1:8970/api/cloud-drive'
$headers = @{ Authorization = "Bearer $env:SESSION_TOKEN" }
$body = @{
    requestId = [guid]::NewGuid().ToString()
    reviewId = '00000000-0000-4000-8000-000000000001'
    entryIds = @(101, 102)
    targetEntryId = 103
}
$plan = Invoke-RestMethod -Method Post -Uri "$base/quarantine-plans" -Headers $headers `
    -ContentType 'application/json' -Body ($body | ConvertTo-Json)
Invoke-RestMethod -Uri "$base/quarantine-plan-requests/$($body.requestId)" -Headers $headers
Invoke-RestMethod -Uri "$base/quarantine-plans/$($plan.id)" -Headers $headers
Invoke-RestMethod -Uri "$base/quarantine-plans?page=1&pageSize=20" -Headers $headers
Invoke-RestMethod -Method Post -Uri "$base/quarantine-plans/$($plan.id)/cancel" -Headers $headers `
    -ContentType 'application/json' -Body (@{ expectedRevision = $plan.revision } | ConvertTo-Json)
```

201响应包含id/requestId、prepared/revision=0、valid/invalidReason、固定来源与目标/逐项证据、policyHash/snapshotHash、createdAt/expiresAt和networkAccess=false/writeAvailable=false。保存响应丢失时只按原requestId GET；不使用新UUID假装恢复原动作。

结构/类型/额外字段422，未登录401、缺任一计划或scan权限403、非本人404；UUID格式语义及当前共享保护槽拒绝为400（沿用现有本地目录服务），幂等输入冲突/容量/取消修订409；SQLite或关键审计存储失败503，不回显路径、Token或底层异常。

## 6. 执行与核对数据结构

以下DDL为最终字段和索引，与schema54源码对照。外键仅关联长期固定证据，不关联可清理的观察。

```sql
CREATE TABLE IF NOT EXISTS cloud_quarantine_runs (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL REFERENCES cloud_quarantine_plans(id), actor_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('move','observe')),
    original_run_id TEXT REFERENCES cloud_quarantine_runs(id), attempt INTEGER NOT NULL DEFAULT 0,
    policy_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('accepted','running','review','cancelled','completed')),
    reason TEXT, revision INTEGER NOT NULL DEFAULT 0,
    slot_held INTEGER NOT NULL DEFAULT 1 CHECK(slot_held IN (0,1)), worker_token TEXT NOT NULL,
    owner_pid INTEGER NOT NULL, owner_host TEXT NOT NULL, worker_pid INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    max_rpc INTEGER NOT NULL, used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc>=0 AND used_rpc<=max_rpc),
    max_bytes INTEGER NOT NULL, received_bytes INTEGER NOT NULL DEFAULT 0, log_id INTEGER NOT NULL UNIQUE,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL,
    basis_revision INTEGER, basis_epoch INTEGER, positions_json TEXT, committed_epoch INTEGER, committed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_move_once
    ON cloud_quarantine_runs(plan_id) WHERE kind='move';
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_kind_slot
    ON cloud_quarantine_runs(kind) WHERE slot_held=1;
CREATE INDEX IF NOT EXISTS idx_cloud_quarantine_run_owner
    ON cloud_quarantine_runs(actor_id,created_at DESC,id);
CREATE TABLE IF NOT EXISTS cloud_quarantine_rpcs (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES cloud_quarantine_runs(id), ordinal INTEGER NOT NULL,
    method TEXT NOT NULL, request_path TEXT, item_ordinal INTEGER,
    phase TEXT NOT NULL CHECK(phase IN ('sending','responded','unknown')), response_code TEXT,
    received_bytes INTEGER NOT NULL DEFAULT 0, identity_json TEXT,
    reviewed_by_run TEXT REFERENCES cloud_quarantine_runs(id), sent_at REAL NOT NULL, responded_at REAL,
    UNIQUE(run_id,ordinal)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_rpc_inflight
    ON cloud_quarantine_rpcs(run_id) WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL;
CREATE TABLE IF NOT EXISTS cloud_quarantine_outcomes (
    run_id TEXT NOT NULL REFERENCES cloud_quarantine_runs(id), ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 5),
    state TEXT NOT NULL CHECK(state IN ('prepared','sending','responded','verified','not_applied','review')),
    precheck_json TEXT NOT NULL, postcheck_json TEXT, move_rpc_id TEXT, remote_reply INTEGER, reason TEXT,
    source_parent_id TEXT NOT NULL, target_parent_id TEXT NOT NULL, created_at REAL NOT NULL, verified_at REAL,
    PRIMARY KEY(run_id,ordinal)
);
```

每种kind最多一个持有槽；move保护下只有明确批准的关联observe可发新的只读请求。每run最多一个未决RPC，原未知不因新核对而改成成功。idx_cloud_quarantine_target定义为本地观察表`(job_id,directory_id,generation,name)`，只用于精确定位配置隔离目录。

## 7. 执行调用示例

### 查询投影与状态

`GET`运行查询返回本地投影，不触发云请求。`networkAccess=false`和`replayed=false`限定此次查询，不否认历史RPC。`workerSlotHeld`只表示PID仍绑定；false不能单独证明从未派发。`globalSlotHeld`为业务保护，不能因worker退出或轮询停止而自动释放；`pollingRecommended=false`也不是远端已停止的证据。

| 字段组合 | 语义与允许的后续 |
| --- | --- |
| `status=accepted/running` | 本轮已受理/执行；只查询或请求停止后续，不重放POST |
| `kind=move`、`review`，逐项为`verified/not_applied`，原槽持有且无后续observe | 仅满足本地同步的候选条件；提交还核当前权限、revision、无未决RPC及完整身份/位置证据300秒内有效 |
| `kind=observe`、`review`、`reason=positions_observed`，本轮槽/PID释放 | 须最新轮、原动作修订/路径世代一致、位置完整且无歧义和未过期，才能提交原动作本地修订 |
| `completed`、`committedEpoch`非空 | 普通本地提交完成；恢复需反向新计划与新授权 |
| 原动作`review/positions_reconciled`、`committedEpoch`非空 | 新观察已提交当前位置，但原未知事实保留，不改成原RPC成功 |
| `review`与其他异常/未知、或`cancelled` | 不推断未发送/未应用；按原动作及保护状态核对，再决定独立只读观察 |

`latestObservationRequestId`用于定位后续观察；存在后续观察后，普通原动作提交不得绕过恢复链。`logAvailable=false`只说明普通日志不可用，固定RPC与审计不随之删除。页面操作说明见[状态与下一步](../../manuals/cd2-quarantine.md#7-查询状态与下一步)。

以下TypeScript调用使用项目封装；变量来自本人已读取的固定记录和页面明确输入，不提供自动重试循环。Token不写入URL、存储或日志。

```typescript
import { startQuarantinePlan, findQuarantineRun, commitQuarantineRun } from "./api/cloudQuarantine";

async function executeOnce(plan: { id:string; revision:number; policyHash:string }, token:string) {
  const requestId=crypto.randomUUID();
  const url=new URL(location.href);
  url.searchParams.set("quarantine_run_request_id",requestId);
  history.replaceState(null,"",url);
  try {
    return await startQuarantinePlan(plan.id,{requestId,expectedRevision:plan.revision,policyHash:plan.policyHash,
      confirmMaintenance:true,confirmGlobalFence:true,apiToken:token});
  } finally {
    token="";
  }
}

async function inspectOriginal(requestId:string) {
  return findQuarantineRun(requestId);
}

async function syncConfirmedPosition(run:{id:string;revision:number}) {
  return commitQuarantineRun(run.id,run.revision);
}
```

调用executeOnce之前必须已满足维护窗口、单写者、最小Token、可信HTTPS及两项明确确认；示例不会代替这些确认。受理只返回202记录，后续只GET观察；只有位置明确且worker确实退出，用户再次确认后才可本地同步。页面实际通过Vue Router保存计划与执行两个UUID；导航失败即不POST。
