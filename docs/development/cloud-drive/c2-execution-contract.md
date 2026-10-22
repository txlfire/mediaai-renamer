# C2 受控执行接口契约

类型：接口设计。范围：已确认的受控目录改名模式；不是发布说明。

## 前提与限制

- 用户直接拥有 `cloud-directory:plan` 和 `cloud-directory:execute` 权限，并为固定计划所有者。普通查询/取消只要求计划权限；执行与本地同步重新校验执行权限。
- 管理员通过系统设置配置固定 CD2 HTTPS 地址、可信公开 CA、批准根、映射修订，并显式启用“受控目录改名”；配置仅保存于数据库，见[配置设计](../../design/cd2-database-settings.md)。产品默认关闭；客户端不能传入替代地址、预算、源路径或目标名称。
- 固定计划最多五项、同父目录、完整观察、名称与 ID 快照。支持的 CD2 构建组合以连接器版本校验为准，不推断其他版本兼容。
- 维护窗口内其他客户端不修改父目录；前后身份核对不等同服务端 CAS，仍存在外部并发替换风险。
- 执行不读取正文，不递归、不移动、不删除、不自动反向补偿。执行未知不重试，任何新真实试验需要对应范围授权。
- HTTPS 限制在服务端检查请求 scheme；反向代理部署必须另行验证可信代理和 TLS 链。不能把客户端传入的任意转发头当作可信证明。

## 接口

前缀 `/api/cloud-drive`，认证沿用应用登录会话。

| 方法与路径 | 请求 | 副作用 |
| --- | --- | --- |
| GET `/directory-policy` | 无 | 只读取本地准入策略，无云请求 |
| POST `/directory-plans/{planId}/execute` | 下列执行请求 | 受理独立预算并启动一次指定 worker；202 不代表远端改名完成 |
| GET `/directory-execution-requests/{requestId}` | 原执行 UUID | 只查当前所有者的本地执行，不新建、领取或重放 |
| GET `/directory-plans/{planId}/execution` | 固定计划 UUID | 只查该计划本地状态 |
| POST `/directory-plans/{planId}/execution/cancel` | `expectedRevision` | 记录取消意图；不撤销已发请求，不承诺远端未执行 |
| POST `/directory-plans/{planId}/commit-local` | `expectedRevision`、`confirmLocalOnly=true` | 仅提交全部已核验项的本地路径修订，不发送 CD2 请求 |

执行请求字段均必需，拒绝额外字段与布尔/整数隐式转换：

```json
{
  "requestId": "12345678-1234-1234-1234-123456789abc",
  "expectedRevision": 0,
  "policyHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "confirmMaintenance": true,
  "confirmGlobalFence": true,
  "apiToken": "SYNTHETIC_LIMITED_TOKEN"
}
```

`apiToken` 仅使用精确绑定固定父目录、短期的最小权限 Token：列目录、改名、查看运行信息；拒绝更多或未知权限。Token 通过 HTTPS 请求体及匿名 stdin 交接，不进入 URL、参数、环境变量、数据库或普通日志。客户端不保存 Token 到浏览器存储；应用无法保证浏览器/HTTP 库已分配内存即时物理擦除。

调用示例使用现有前端封装。以下是可放在前端模块中的完整辅助函数；调用方必须先展示两项确认，失败后让用户手动调用查询函数，不自动重试：

```ts
import { fetchDirectoryPolicy, startDirectoryExecution, findDirectoryExecution } from "../api/cloudDirectory";
import type { DirectoryPlan } from "../api/cloudDirectory";
import type { Router } from "vue-router";

export async function executeConfirmedDirectory(router: Router, plan: DirectoryPlan,
  apiToken: string, confirmMaintenance: boolean, confirmGlobalFence: boolean) {
  let requestId = "";
  try {
    if (location.protocol !== "https:" || !confirmMaintenance || !confirmGlobalFence || !plan.valid) {
      throw new Error("execution_not_confirmed");
    }
    const policy = await fetchDirectoryPolicy();
    if (!policy.available || policy.policyHash !== plan.policyHash) throw new Error("policy_changed");
    requestId = crypto.randomUUID();
    const failure = await router.replace({ query: { ...router.currentRoute.value.query,
      directory_execution_request_id: requestId } });
    if (failure) throw new Error("request_marker_not_saved");
    const execution = await startDirectoryExecution(plan.id, {
      requestId, expectedRevision: plan.revision, policyHash: policy.policyHash,
      confirmMaintenance, confirmGlobalFence, apiToken,
    });
    return { requestId, execution };
  } catch {
    return { requestId, execution: null };
  } finally {
    apiToken = "";
  }
}

export const queryOriginalDirectoryExecution = findDirectoryExecution;
```

产品窗口还实现响应绑定检查、秘密清理与会话隔离，完整实现为 `DirectoryExecutionDialog.vue`。只有真实用户确认两个边界后才可传入 `true`，不能在后台自动推定同意。

## 状态与预算

返回字段包括 `planId`、`requestId`、`revision`、`status`、`reason`、`cancelRequested`、`workerSlotHeld`、`globalSlotHeld`、`pollingRecommended`、`logId`、`logAvailable`、预算、最多五项逐项状态及最多 38 条 RPC 事件。查询响应的 `networkAccess=false` 仅表示本次 GET 没有访问云盘，不表示历史动作没有写入。

| 状态 | 含义 |
| --- | --- |
| accepted / running | 已受理 / 正在执行，尚未业务完成 |
| review / awaiting_path_sync | 远端逐项核验通过，等待本地路径事务；worker 退出后才可本地同步 |
| review / 其他原因 | 需核对；可能已部分改名或存在未知请求，冻结不自动释放 |
| cancelled | 无已发改名及未决 RPC 的情况下已停止；不能泛化为所有取消都保证未执行 |
| completed | 全部逐项核验通过，路径修订和旧计划水位已提交，槽已释放 |
| review / positions_reconciled | 新只读预算已核对当前位置并显式同步；原未知因果不回填为成功，槽已释放 |

预算独立于 C1：`3+7×项数` 次应用 RPC、180 秒、1 MiB 接收元数据；等待计入截止。超过响应大小上限也保留实际接收量，不伪造为未收到。费用/流量不是此计数的承诺口径。共享间隔、暂停和冷却继续生效。

批准前以计划实际剩余有效期和180秒的较小值作为可用寿命；所需最小时间含首个请求距上次发送的剩余间隔、后续固定RPC间隔及15秒余量。已知不足返回 `batch_exceeds_time_budget`，不占槽、不发送RPC。此预估不是远端耗时保证；执行中超时仍停止并保留未知事实，不补发。

发送前落盘意图并计费；未知保留原始意图，禁止超时后自动清除或原样重发。页面最多每 1500ms 串行读一次本地状态，隐藏或关闭停止轮询；到期仅令 `pollingRecommended=false`，不据此认定 worker/远端已经停止。

本地路径同步只保存少量目录修订及全实例旧扫描/文件计划水位，不遍历替换所有文件路径、不自动重新扫描。旧观察保留作历史展示，不得作为新计划依据。

## 错误

- 400：HTTP 提交凭据、缺少确认等；422：字段形状不合法。错误不能包含 Token 或原始请求体。
- 403：权限不足；404：当前用户未查到请求/计划，不证明远端从未受理。
- 409：修订/策略变化、已受理、槽占用等；业务错误包含安全的 `code`、`message`，不回传 gRPC 原始详情。
- 断连、非预期响应与服务崩溃：保存执行 UUID，仅查询。请求超时不证明远端未执行。

普通操作日志清理不应删除安全审计、动作意图、身份前后证据与路径修订。独立只读恢复、部分批次核对及反向新计划使用自己的明确契约，不通过本接口增加重试或任意目标参数。

`latestRecovery`返回最近一次独立位置核对摘要，其UUID用于恢复窗口查询，不自动重新受理。接口及反向计划详见[恢复契约](c2-recovery-contract.md)，页面操作见[操作说明](../../manuals/cd2-directory-rename.md)。

## 运行日志

运行日志使用稳定整数 `logId` 关联，类型 `cloud_directory_execution`。沿用 `GET /api/operation-logs?task_type=cloud_directory_execution&task_id={logId}&after_id=0&limit=100` 及 `/export`。除 `audit:read` 外，还必须为计划所有者并具备当前计划权限；不能用其他用户的 `logId` 读取或导出。

阶段摘要仅记录受理、前后检、未知、取消、回收、本地提交及条目序号，不记录名称、路径、对象 ID、Token 或原始 gRPC 错误。普通日志沿用系统保留时长、行数/大小与清理政策；日志写入失败不丢弃安全状态、不触发远端重发。清理后 `logAvailable=false`，页面禁用静止任务的日志按钮并提供提示；独立执行与身份事件仍可查看。

窗口复用现有 `OperationLogDrawer`，串行增量查询、最多渲染1000条、后台降低刷新频率，不新增高频逐RPC推送。关闭窗口停止查询，导出仍受公共行数/字节上限约束。
