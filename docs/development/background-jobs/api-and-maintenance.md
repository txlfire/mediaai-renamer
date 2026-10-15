# 后台探测与匹配接口及维护

类型：接口与维护手册。适用版本1.3.0、schema39；该后台任务接口自v1.2.0交付，v1.3.0保持兼容。当前状态及剩余边界见[项目状态](../project-status.md)。

## 接口

请求通过后端代理执行，不由浏览器直连供应商。携带`Authorization: Bearer <应用会话令牌>`，不要把供应商密钥放进请求。客户端先保存安全随机UUID；同一ID、同一用户和规范化参数返回原任务，不重新执行；同ID不同参数返回409。

| 方法与路径 | 参数/结果 | 限制 |
| --- | --- | --- |
| POST /api/background-jobs | request_id、kind及下表参数；202小型任务摘要 | 仅做数据库预检与持久化，不探测媒体、不外发HTTP |
| GET /api/background-jobs | 最近20条当前用户任务摘要 | 不返回输入快照、租约、候选或供应商原始响应 |
| GET /api/background-jobs/{UUID} | 状态、总数、已处理数、成功/失败/未知数、result_available、清理时间和错误信息 | 原用户或task:manage；另需该任务原权限 |
| GET /api/background-jobs/{UUID}/result | 摘要、result及items逐项结果 | 结果已清理则404，不重建或重发任务 |

| kind | 参数 | 权限/结果 |
| --- | --- | --- |
| rename_dry_run | rename_preview_ids：正整数数组 | rename:execute；result.operation_id，需另外确认执行，不改名 |
| rollback_dry_run | plan_id：正整数 | rollback:execute；result.plan_id及operation_id，不恢复文件 |
| metadata_batch | rename_preview_ids、metadata_match_source、mode | metadata:submit；与原多源批量摘要兼容，缓存候选、不自动改名 |
| ai_batch | rename_preview_ids、metadata_match_source | metadata:submit；结构化候选/已知用量，不自动应用候选 |

metadata_match_source可选parsed_title（默认）、original_file_name、parent_folder_title；mode可选fallback（默认）、primary_only、parallel。批量最多min(operations.batch_limit,1000)，去重后整批超限拒绝，不截断执行前N项。UUID/类型/字段参数错误返回422；业务预检400，冲突/容量/重叠任务409，查询无权限403或404，内部异常500并由公共关联ID机制记录。400也可能表示排队前当前用户已失效，不自动更换身份重发。

调用示例（在已初始化Pinia的页面内，沿用现有API客户端）：

```typescript
import { useBackgroundJobsStore } from "../stores/backgroundJobs";
import type { BatchAiParseResult } from "../api/client";

export async function submitConfirmedAiBatch(previewIds: number[]) {
  return useBackgroundJobsStore().run<BatchAiParseResult>("ai_batch", {
    rename_preview_ids: previewIds,
    metadata_match_source: "parsed_title",
  });
}
```

上述适配层先保存UUID、短请求受理，再查询状态，完成后只取一次完整结果。业务调用前仍须原页面的用户确认、权限及敏感词保护。原同步dry-run、多源和AI批量API保留兼容，遗留TMDB批量/AI后备组合并未全部后台化。

## 状态与恢复

- queued→running→completed/partial_completed。监督中断、超时、撤权、配置或输入失效进入interrupted；不自动重新排队。failed为查询兼容终态，不能仅凭状态名称判断是否已外发。
- 逐项queued/running尚无持久化发出记录，dispatched表示已持久化准入意图（不保证供应商实际收到），completed/failed保留已知结果，unknown需核查，not_sent表示监督终止时未持久化外发意图。摘要已处理数不等于成功数，也不等于实际文件动作数。
- 每次实际HTTP开始前、排队后以及数据库发布前检查执行租约、当前用户权限和配置。AI返回后另查原输入；多源线程继承守卫。配置/来源改变不能让排队任务继续使用新参数或覆盖新预览。
- 本机同数据库只有一个后台探测/外发任务执行槽，与实际文件写FIFO独立；外发继续受既有origin级预算控制。停止/kill请求不等于进程已退出；PID未知、复用或不能证明退出时保留槽，不直接删除或重排任务。
- 刷新页面后从本浏览器sessionStorage或后台最近任务恢复观察；不是浏览器自动重复提交。跨账号记录隔离，旧会话迟到成功/失败不回填新会话。新标签/关闭浏览器后可从服务器任务中心找回，普通用户近期列表只有20条，需保存重要请求编号。
- 受理响应不明确时只查询原UUID，不产生新UUID猜测结果。明确拒绝才允许纠正参数后另行提交。未知结果先到供应商侧核查是否执行/计费；“确认已核查”仅解除本浏览器重复提交保护，不改变服务器结果、不执行原任务。
- 关闭抽屉停止其额外观察；已受理任务及仍由业务页面等待的观察继续。退出登录不自动取消后台业务；禁用用户/撤销权限会使执行守卫失效。结果观察前台1.5秒、隐藏页6秒、同ID去重，只查小摘要。

## 容量与清理

配置位置：config.toml的background_jobs，修改后重启；示例见[配置示例](../../../config/config.example.toml)。默认任务总时间3600秒、队列上限100、单任务结果4MiB、总逻辑容量64MiB、保留7天。有效范围分别30～86400、1～1000、1～64、1～4096、1～365；总容量不得小于单任务容量。活动任务按单任务上限预留空间，因此实际可受理数量可能小于队列上限。

结果丢弃raw_data/raw_response/prompt；单项超限记录明确失败，不重新请求；聚合用量等超限时压缩汇总、保留有界逐项已知结果，抽屉默认展示逐项。原预览的业务候选缓存由原业务维护，不等于后台结果容量。运行日志继续服从系统日志设置，安全审计不套用普通结果保留期。

每次维护最多清理200条终态且已确认不占槽的记录，受理时也做容量检查。只清输入/快照/逐项结果/汇总；保留UUID、幂等摘要、用户、状态、计数及审计。清理后查看结果禁用，直接查询返回已清理提示；不会因新POST相同UUID重新执行。小型幂等记录长期保留，64MiB不是SQLite/WAL物理文件上限，不自动VACUUM；物理维护与审计保留政策另行确认。

数据库使用参数化sqlite3，没有ORM映射。维护只读SQL示例：

```sql
SELECT id, request_id, kind, status, total_count, completed_count,
       unknown_count, result_bytes, result_cleared_at,
       execution_pid, execution_owner_pid, execution_expires_at
FROM background_jobs
ORDER BY id DESC LIMIT 20;
```

结果统计计入输入摘要和结构化JSON的UTF-8逻辑字节，不承诺删除后物理文件收缩。不要手工清租约、改状态或删幂等行释放容量。来源/权限变化和超时后检查逐项已知结果与供应商记录，不将未知任务当未执行。
