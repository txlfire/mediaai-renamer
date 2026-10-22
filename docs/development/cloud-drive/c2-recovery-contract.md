# C2 只读位置核对与反向计划契约

类型：接口与数据设计。适用受控维护窗口模式，开发源码schema55；不是发布或实机验收结论。执行前提见[执行契约](c2-execution-contract.md)。

## 1. 只读位置核对

原执行必须仍持有保护槽，由当前用户所有，当前直接拥有`cloud-directory:plan`和`cloud-directory:execute`权限。只允许同一主机核对：原worker PID尚存记录时，worker与原监督进程均须确认退出；尚未绑定worker的accepted/running执行也不能跳过监督进程存活检查。正常回收且worker PID已清空的review状态可以在原API进程仍运行时核对。

用户确认维护窗口、旧进程退出和远端静止后，使用新的请求UUID、新的只读Token及独立预算。Token精确绑定原父目录，权限只能为列目录和运行信息，期限1至600秒；不含改名、删除、正文读取或管理权限。凭据仅HTTPS和匿名stdin交接，不保存到配置、数据库、URL、浏览器存储或日志。

每轮最多`4+2×项数`次RPC、90秒、1MiB接收元数据，每个原计划最多五轮。查询只使用GetApiTokenInfo、GetRuntimeInfo及固定父/源/目标的FindFileByPath，不GetSubFiles、不读正文、不重发原改名。原生连接器、预算及Token三层拒绝写操作。等待计入截止；共享间隔和冷却继续生效。仅明确确认且冷却结束后可对request_incomplete暂停申请新只读预算，不能绕过手动暂停或时钟倒退保护。

若管理员在未知保护期间显式暂停，须先通过独立[管理恢复分支](c0-budget-implementation.md)撤销手动暂停意图。该分支要求额外确认旧目录worker退出及远端静止，仅转为paused/request_incomplete，原保护和未知账本不变；之后仍由原计划所有者单独批准新只读核对，不自动发送请求。

关闭目录写开关不阻止只读核对；固定端点、CA、根、映射修订、规则及间隔等身份摘要仍须匹配。不能趁恢复更换连接地址或扩大范围。

| 方法及路径，前缀`/api/cloud-drive` | 请求 | 行为 |
| --- | --- | --- |
| POST `/directory-plans/{planId}/recoveries` | 下列请求 | HTTPS受理并启动一次独立只读worker，202不代表核对完成 |
| GET `/directory-recovery-requests/{requestId}` | 原核对UUID | 仅查询当前所有者本地记录，不新建、不重放 |
| GET `/directory-recoveries/{recoveryId}` | 核对ID | 只查本地状态和位置证据 |
| POST `/directory-recoveries/{recoveryId}/cancel` | `expectedRevision` | 停止后续读取，保留已发事实及原执行保护 |
| POST `/directory-recoveries/{recoveryId}/commit-local` | `expectedRevision`、`confirmCurrentPositions=true` | 显式提交已核对的位置，不发送云请求 |
| POST `/directory-recoveries/{recoveryId}/review-worker` | `expectedRevision`、`confirmClientsStopped=true`、`confirmRemoteIdle=true` | 本人且具计划/执行/设置权限；核对本机owner及worker退出后只归还孤儿只读恢复槽 |

```json
{
  "requestId": "12345678-1234-1234-1234-123456789abc",
  "expectedRevision": 12,
  "confirmMaintenance": true,
  "confirmRemoteIdle": true,
  "apiToken": "SYNTHETIC_READ_ONLY_TOKEN"
}
```

拒绝额外路径、名称、预算或端点字段，不接受布尔与整数隐式转换。400表示HTTP或确认不满足；422表示字段形状错误；403权限不足；404当前用户没有对应记录；409表示修订、旧进程、共享槽、策略、冷却或容量等门禁拒绝。404和请求超时均不能证明原写入未发生。

## 2. 位置及提交语义

| 位置 | 判据 |
| --- | --- |
| not_observed | 尚未形成完整位置证据 |
| at_source | 源处为固定ID、目标不存在 |
| at_target | 目标处为固定ID、源不存在 |
| ambiguous | 其他ID、双占、双缺或证据不足，不能放行 |

核对前后父身份必须相同；原计划发过RenameFile时，还必须匹配原先父身份基线。所有项位置确定才进入ready；任何歧义进入review并保留保护。worker退出只释放本轮只读槽，原目录保护槽不自动释放。恢复槽本身也阻止普通文件操作绕过保护。

本地提交必须使用最新一轮ready证据，worker及其槽均已回收、未取消、原执行修订/策略/路径世代未变。以最早精确身份响应为起点，所有证据最多300秒有效；不是以进程回收时间重新续期。确认只证明当前观察位置，不证明原未知RPC的执行因果。

提交在同一事务中推进路径世代及旧扫描/文件计划失效水位，只为at_target子集保存目录路径修订；全部at_source也推进失效水位。不递归改写历史文件路径，不自动补扫。关键审计失败整笔回滚，原保护保持；普通运行日志失败不触发远端重发。

原执行变为`review / positions_reconciled`并释放保护，不伪装为completed。原RPC的unknown保持unknown，单独引用`reviewed_by_recovery`；未完成sending归为unknown后再关联本轮证据，不伪造成功响应。原逐项后检与预算不改写。旧worker/监督进程在交接后不得继续原执行或迟到回收改写槽位。

计划一旦存在恢复记录，原普通路径提交入口即拒绝，包括原执行显示completed时的普通幂等返回。不能用原先verified结果覆盖后来的歧义；只允许符合上述条件的恢复专用提交同步路径并释放保护。

恢复状态为accepted、running、ready、review、committed。返回独立预算、revision、workerSlotHeld、recoverySlotHeld、最多五项位置及最多14条RPC事件；`networkAccess=false`仅描述该状态查询不访问云端。恢复本身不明时停止，再次核对须用户明确新预算，不自动循环五次。

### 孤儿恢复槽管理核对

`review-worker` 不接收Token、PID、路径或替代所有者。严格确认及最新revision必须满足；本机原监督者和已绑定worker都须确认退出，存活、状态不明或跨主机时拒绝。未绑定PID仍须原监督者退出。查询与修改在同一写事务完成，关键审计失败回滚。

该入口不证明跨主机或PID复用后的进程身份。旧PID仍被探测为存活、容器/主机身份变化或不能确认退出时继续保持保护；不允许以人工确认字段覆盖这些检查，也不承诺任意重启后的自动恢复。

仅将本次只读槽归还、worker PID清空；原目录执行保护、消费和原未知RPC不变。恢复中的sending改unknown，暂停共享预算并保留/延长冷却，手动暂停意图不覆盖。已完整核对且无未知的ready证据可保留；其他状态进入review，不据进程退出推断远端请求未发送或成功。之后若需重查，仍须新的明确预算与Token，不自动启动。

请求结构如下，响应为上文完整DirectoryRecovery；400确认不满足，401未登录，403缺当前权限，404非本人或记录不存在，409修订/主机/进程门禁，503本地存储或关键审计失败。响应不明只查询原核对编号。

```json
{"expectedRevision": 3, "confirmClientsStopped": true, "confirmRemoteIdle": true}
```

项目调用示例，必须在界面收集上述两个确认后调用，不自动重试：

```ts
import { reviewDirectoryRecoveryWorker } from "../api/cloudDirectory";
import type { DirectoryRecovery } from "../api/cloudDirectory";

export async function reviewConfirmedReadWorker(value: DirectoryRecovery, clientsStopped: boolean, remoteIdle: boolean) {
  if (!clientsStopped || !remoteIdle) throw new Error("not_confirmed");
  return reviewDirectoryRecoveryWorker(value.id, { expectedRevision: value.revision,
    confirmClientsStopped: clientsStopped, confirmRemoteIdle: remoteIdle });
}
```

## 3. 反向新计划

POST `/directory-plans/{planId}/reverse`，请求只有`requestId`和原执行`expectedRevision`，返回201固定计划。创建本身不写云端，要求当前计划权限；实际执行仍要求独立执行权限、写开关、新Token、维护确认及新动作预算。

只允许从当前全实例路径世代已提交的位置修订生成。原执行须completed或positions_reconciled，且没有worker/保护槽。恢复后只反向at_target子集；全部位于原名或已有其他计划推进全局世代时拒绝，不猜测仍可撤销。新计划原/目标互换，沿用固定对象ID，仍校验保护结构、名称、目标重合及配置身份；不接受任意新名称。反向依据不依赖可清理的旧观察缓存。

新计划类型reverse，保存origin_plan_id、origin_revision、basis_epoch；有效期30分钟，同父目录最多五项。创建和取消的计划也计入全实例1000个计划容量；到限明确停止，没有自动删除批准、消费或审计的政策。

GET `/directory-plans?page=1&pageSize=20`只返回本人有界计划摘要及执行/最新核对请求编号，每页最多50项，不逐行访问云盘或自动执行。页面从记录恢复编号后仍需明确核对。

前端调用示例，须由已完成界面确认的调用方执行；失败保留UUID，仅调用findDirectoryRecovery查询，不自动再POST：

```ts
import { startDirectoryRecovery, findDirectoryRecovery, prepareReverseDirectoryPlan } from "../api/cloudDirectory";
import type { DirectoryExecution } from "../api/cloudDirectory";

export async function startConfirmedPositionCheck(execution: DirectoryExecution, apiToken: string,
  confirmMaintenance: boolean, confirmRemoteIdle: boolean, persistId: (id: string) => Promise<void>) {
  const requestId = crypto.randomUUID();
  try {
    if (location.protocol !== "https:" || !confirmMaintenance || !confirmRemoteIdle) throw new Error("not_confirmed");
    await persistId(requestId);
    return await startDirectoryRecovery(execution.planId, { requestId, expectedRevision: execution.revision,
      confirmMaintenance, confirmRemoteIdle, apiToken });
  } finally { apiToken = ""; }
}

export const queryOriginalPositionCheck = findDirectoryRecovery;
export const createConfirmedReversePlan = prepareReverseDirectoryPlan;
```

## 4. 持久化

cloud_directory_recoveries保存独立核对预算及工作槽，cloud_directory_recovery_rpcs保存核对RPC事实，cloud_directory_recovery_items保存当前位置证据；原RPC使用reviewed_by_recovery单独关联位置核对，反向计划保存来源与世代字段。未复核的sending/unknown仍阻断普通发送。证据独立于观察和普通日志，清理日志不删除未知意图或路径修订。

项目使用参数化sqlite3，无ORM，不为文档引入第二套持久层。位置核对的只读SQL示例：

```sql
SELECT ordinal, position, source_id, target_id, parent_id, observed_at
FROM cloud_directory_recovery_items
WHERE recovery_id = ?
ORDER BY ordinal;
```

对应运行实现位于`cloud_directory_recovery_service.py`及`cloud_directory_reverse_service.py`。迁移及环境差异见实施计划，不在此契约中宣称既有实例已升级。

孤儿恢复槽归还的参数化更新由`cloud_directory_recovery_worker_service.py`执行，权限/归属/修订/主机/进程检查和关键审计在同一事务中；下面仅用于解释持久化语义，不是手工解锁命令。项目无ORM，不提供虚构ORM写法：

```sql
UPDATE cloud_directory_recoveries
SET worker_pid = NULL, slot_held = 0, status = ?, reason = ?,
    revision = revision + 1, updated_at = ?
WHERE id = ?;
```
