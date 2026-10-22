# C3 本地广告复核接口与数据契约

文档类型：接口设计。权限为 `scan:run`，均只访问本人本地记录，不发云RPC。安全约束见[安全设计](../../design/cd2-ad-review-safety.md)。

## 1. 接口

| 方法 | 路径（前缀 `/api/cloud-drive`） | 行为 |
| --- | --- | --- |
| POST | `/ad-reviews` | 固定1至100个明确文件，写关键审计，成功201；相同UUID相同内容返回原记录 |
| GET | `/ad-review-requests/{requestId}` | 按原UUID查询，不自动保存或重发 |
| GET | `/ad-reviews/{reviewId}` | 本人证据详情，重新计算本地来源是否当前 |
| GET | `/ad-reviews?page=1&pageSize=20` | 本人摘要，pageSize不超过50 |

请求示例（令牌仅为当前应用会话，不需要CD2令牌）：

```json
{
  "requestId": "12345678-1234-1234-1234-123456789abc",
  "jobId": "22345678-1234-1234-1234-123456789abc",
  "expectedRevision": 7,
  "items": [{"entryId": 12, "decision": "review", "reason": "需人工进一步确认"}],
  "confirmLocalOnly": true
}
```

前端使用现有客户端（`frontend/src/api/cloudAdReview.ts`）：

```typescript
import { saveAdReview, findAdReview } from "./cloudAdReview";

export async function saveExample(jobId: string, revision: number, entryId: number,
  persistRequestId: (id: string) => Promise<void>) {
  const requestId = crypto.randomUUID();
  await persistRequestId(requestId);
  return saveAdReview({ requestId, jobId, expectedRevision: revision,
    items: [{ entryId, decision: "review", reason: "需人工进一步确认" }], confirmLocalOnly: true });
}

export async function lookupExample(requestId: string) {
  return findAdReview(requestId);
}
```

调用失败不自动调用 `saveExample`，先使用已持久化UUID查询。

## 2. 返回及错误

详情包含 `id/requestId/jobId/inventoryRevision/pathEpoch/ruleVersion/items/createdAt/snapshotHash/sourceCurrent/staleReason`，固定 `networkAccess=false/contentReadBytes=0/writeAvailable=false/executionAuthorized=false`。

逐项包含 `entryId/name/path/size/remoteId/generation/protection/evidence/evidenceLevel/decision/reason`。`evidenceLevel` 为 none/low/medium，不是广告概率。`sourceCurrent` 不证明远端身份当前，不授予动作执行。

| HTTP | 条件 |
| --- | --- |
| 400 / 422 | 规范UUID、类型、超限、重复ID、非法理由或未确认 |
| 401 / 403 | 未登录、用户禁用或权限撤销 |
| 404 | 原记录不存在或不属于本人 |
| 409 | 请求冲突、来源变化、不完整文件、保护项、证据不足或容量已满 |
| 503 | 本地存储不可用，不能把存储失败当原请求不存在 |

409中的稳定代码包括 `request_conflict/observation_changed/path_revision_changed/scope_changed/protected_entry/review_evidence_required/review_file_incomplete/review_capacity`；沿用本地观察异常包装，前端只映射白名单，不展示远端原文或Axios对象。

## 3. 数据

schema51新增 `cloud_ad_reviews`。表不外键依赖可清理观察，关键字段有主键、UUID唯一约束、逻辑字节与项数检查，所有者索引支持分页。完整SQL见 [schema定义](../../../backend/app/core/cloud_ad_review_schema.py)。

```sql
SELECT id, request_id, item_count, created_at
FROM cloud_ad_reviews
WHERE actor_id = ?
ORDER BY created_at DESC, id DESC
LIMIT ? OFFSET ?;
```

数据访问使用参数化 sqlite3；无 ORM 映射。写入使用 `BEGIN IMMEDIATE`，同事务检查全库数量/字节配额、观察修订和审计。只迁移隔离测试库，不自动迁移正式实例。

不提供更新/删除人工证据接口、云端执行接口、自动保留期清理或下载/隔离开关；达到配额时由未来独立归档政策处理。
