# C2 固定计划实现契约

类型：实现与迁移说明。日期：2026-10-17。对应C2-A，未发布，不是实际云端改名操作手册。

## 范围

固定明确选中的一至五个同父目录观察条目，服务端根据字面净化词重算建议。只接受完整且已结束的本地观察；必须有非空远端ID。计划不执行云端请求、不保存Token、不沿用只读RPC余额。执行、位置核对和反向计划分别使用独立契约，不能把计划创建当作云写成功。

需要单独授予`cloud-directory:plan`权限，现存用户不会自动获得。创建新管理员使用项目既有全权限逻辑；已有管理员权限不因迁移改写。本文只定义固定计划阶段，不以其`writeAvailable=false`断言全系统没有执行接口；后续独立权限`cloud-directory:execute`及HTTPS执行契约见[C2执行契约](c2-execution-contract.md)，当前交付状态见[实施计划](c2-implementation-plan.md)。

## 数据与迁移

固定计划增量为schema42升级到43；当前源码目标为schema50，稳定v1.3.0/schema39不变。43增量只创建两张计划表及索引，不修改既有观察、预算或用户权限。49增加只读位置核对证据，50增加反向计划来源和世代，见[恢复契约](c2-recovery-contract.md)。正式升级仍须备份数据库、配置及匹配密钥，停止相关进程后使用标准启动流程；不要在既有开发/验收实例上直接加载新源码触发迁移。

SQL由`backend/app/core/cloud_directory_schema.py`维护，业务采用项目现行参数化sqlite3；没有ORM等价实现，不另引入ORM。查询快照的实际写法：

```sql
SELECT * FROM cloud_directory_plans WHERE id = ? AND actor_id = ?;
SELECT * FROM cloud_directory_plan_items WHERE plan_id = ? ORDER BY ordinal;
```

```python
row = connection.execute(
    "SELECT * FROM cloud_directory_plans WHERE id=? AND actor_id=?",
    (planId, actorId),
).fetchone()
```

计划表保存请求UUID/摘要、所有者、观察任务及修订、配置/规则摘要、字面词、批准根、父路径、固定快照摘要、状态及时间。条目表保存选定条目ID、代次、远端ID、原路径/名称及目标路径/名称。没有指向盘点缓存的外键；清理观察后仍可查询计划证据，但不能继续把计划当有效观察。

边界：30分钟有效、最多1000个计划、每计划最多五项、同一父目录最多核对1000条本代观察。达到容量明确拒绝，不自动删除历史证据。名称使用NFC校验、NFKC加casefold冲突键；拒绝控制/格式字符、路径分隔符、设备保留名及疑似名称等价冲突；并非远端命名规则的完整推断。

## 接口

| 接口 | 输入 | 结果与边界 |
| --- | --- | --- |
| POST /api/cloud-drive/directory-plans | requestId、jobId、expectedRevision、entryIds、folderTerms | 201固定快照；本地幂等不等于远端幂等 |
| GET /api/cloud-drive/directory-plan-requests/{requestId} | 原请求UUID | 200原计划；只读，不重新生成或执行 |
| GET /api/cloud-drive/directory-plans/{planId} | 计划UUID | 200固定证据及valid/invalidReason，失效不丢弃原快照 |
| POST /api/cloud-drive/directory-plans/{planId}/cancel | expectedRevision | 200取消，仅prepared及当前revision有效 |

输入模型拒绝多余字段、布尔型ID/修订、超长列表及Token。未认证401，缺少权限403，非本人计划404，无效值400/422，冲突或不满足安全条件409，存储不可用503；错误不返回数据库路径或凭据。创建和取消与关键审计使用同一事务。

成功响应包括id、requestId、inventoryJobId、inventoryRevision、revision、status、valid、invalidReason、policyHash、rulesHash、ruleVersion、parentPath、items、createdAt、expiresAt、networkAccess=false、writeAvailable=false。items是固定选中集合，不随翻页、新盘点自动增加；remoteId仅是本次观察身份，不保证跨重启/重映射稳定。

前端实际包装位于`frontend/src/api/cloudDirectory.ts`，Vue调用示例：

```ts
import { prepareDirectoryPlan } from "./api/cloudDirectory";

export async function saveSelectedDirectoryPlan(jobId: string, revision: number, entryIds: number[]) {
  return prepareDirectoryPlan({
    requestId: crypto.randomUUID(),
    jobId,
    expectedRevision: revision,
    entryIds,
    folderTerms: ["论坛推广"],
  });
}
```

真实页面先将UUID可靠保存在`directory_request_id`查询参数，再发创建请求。响应不明、刷新或重开后仅显式查询原请求，不自动重发。结束核对只移除浏览器标记，不删除计划。应用会话由公共apiClient处理，不把CD2 Token当应用登录令牌。

## 验证记录

本阶段精确结果见[实施日志](../../work-logs/progress-2026-10-17-cd2-c2-implementation.md)。本地测试和模拟CSS视口不替代正式部署、物理系统缩放或真实产品写验收。
