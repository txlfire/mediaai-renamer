# 后台探测与匹配接口及维护

类型：接口与维护手册。后台任务接口自 v1.2.0 交付；下文新增任务类型和通用设置适用于当前未发布的开发分支，沿用既有表结构。当前状态见[项目状态](../project-status.md)。

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
| tmdb_batch | rename_preview_ids、metadata_match_source | metadata:submit；单源 TMDB 匹配并保留候选，不自动回填或改名 |
| tmdb_ai_batch | rename_preview_ids、metadata_match_source | metadata:submit；仅明确符合条件的条目进入 AI 后备，保留两阶段结果 |
| preview_generation | scan_job_id 必填，media_source_id 可选且必须与扫描任务一致 | rename:execute；绑定已完成、仍有效的扫描。保留人工编辑，仅写预览记录，不改名 |

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

上述适配层先保存 UUID、受理后查询状态，完成后只取一次完整结果。原页面的确认、动作权限和敏感词保护不变。旧同步接口保留兼容；网页已将生成预览、选中及筛选范围的 TMDB 批量和 TMDB + AI 后备接入队列。单条外部匹配、连接测试等短操作仍同步。

预览任务受理只读取索引和现存预览，按 128 条一组记录快照。每组本地解析和路径探测在写锁外完成，发布前核对扫描、媒体源、输入、权限和租约。预览与本组进度同事务提交。任务中断时，已提交组保留，后续组不自动执行。超过队列容量时不截断扫描范围。

TMDB 后台模式仅在 V4 明确认证拒绝且配置了 V3 时降级。超时、网络中断、无法确认的响应不降级，不对该条发起 AI 后备。TMDB 写入与后续 AI 绑定同一份预览快照，人工修改会阻止发送或发布旧结果。同步入口沿用原协议。

## 状态与恢复

- queued→running→completed/partial_completed。监督中断、超时、撤权、配置或输入失效进入interrupted；不自动重新排队。failed为查询兼容终态，不能仅凭状态名称判断是否已外发。
- 逐项queued/running尚无持久化发出记录，dispatched表示已持久化准入意图（不保证供应商实际收到），completed/failed保留已知结果，unknown需核查，not_sent表示监督终止时未持久化外发意图。摘要已处理数不等于成功数，也不等于实际文件动作数。
- 每次实际HTTP开始前、排队后以及数据库发布前检查执行租约、当前用户权限和配置。AI返回后另查原输入；多源线程继承守卫。配置/来源改变不能让排队任务继续使用新参数或覆盖新预览。
- 本机同数据库只有一个后台探测/外发任务执行槽，与实际文件写FIFO独立；外发继续受既有origin级预算控制。停止/kill请求不等于进程已退出；PID未知、复用或不能证明退出时保留槽，不直接删除或重排任务。
- 刷新页面后从本浏览器sessionStorage或后台最近任务恢复观察；不是浏览器自动重复提交。跨账号记录隔离，旧会话迟到成功/失败不回填新会话。新标签/关闭浏览器后可从服务器任务中心找回，普通用户近期列表只有20条，需保存重要请求编号。
- 受理响应不明确时只查询原UUID，不产生新UUID猜测结果。明确拒绝才允许纠正参数后另行提交。未知结果先到供应商侧核查是否执行/计费；“确认已核查”仅解除本浏览器重复提交保护，不改变服务器结果、不执行原任务。
- 关闭抽屉停止其额外观察；已受理任务及仍由业务页面等待的观察继续。重开抽屉会重新拉取近期列表；开启自动刷新时，切回浏览器标签页会立即更新。没有选中编号时优先恢复本浏览器未核对编号，其次是近期排队或运行中的任务。退出登录不自动取消后台业务；禁用用户/撤销权限会使执行守卫失效。结果观察前台1.5秒、隐藏页6秒、同ID去重，只查小摘要。

## 从原工作页面查看

| 操作 | 返回页面后如何查看 |
| --- | --- |
| 扫描 | 扫描任务页会按当前媒体源恢复活动扫描；也可从指定任务打开进度和日志。 |
| 实际重命名、回滚 | 从任务历史打开对应操作或回滚计划；同一会话仍有活动标识时，重命名页会继续观察。 |
| 待处理文件移动 | 进入带原移动任务或请求编号的地址会回到待处理文件标签页，运行中显示处理数和进度条，结束后保留最终状态、计数和详情。 |
| 多源批量、纯 AI 批量、重命名/回滚试运行 | 用顶部「后台任务」找回进度、结果和日志；结果已清理时不重新请求供应商。 |
| CD2 盘点、诊断、目录执行与恢复 | 盘点任务按选中的原编号读取摘要。明确重开诊断、执行或恢复弹窗时，有原请求编号才自动读取一次本地状态；仅已有状态表明可继续观察才轮询。隐藏后按页面保护规则停查；需要时再点「核对」，不会重新提交 Token 或发起云操作。 |
| 生成预览、TMDB 批量、TMDB 与 AI 组合后备 | 从「后台任务」按原编号恢复观察。生成预览的结果可返回原扫描范围；已完成部分可查看，不会重新执行。 |

## 容量与清理

- **活跃任务上限**在「系统设置 → 通用设置 → 后台任务」维护，需 `settings:write`。`PUT /api/settings` 提交 `{"values":{"background_jobs.max_queued":45}}`，后端只接受 1～1000 的整数。值保存到 `system_settings` 并用于下一次受理；缺失或非法时回退到 `config.toml` 的 `max_queued`（缺省 100）。调低后不删除、暂停或取消旧任务，新任务返回 409 和 `background_job_queue_full`（附当前上限）；按原编号查询或识别同一请求不受影响。
- 上限按实例数据库计算，包含排队、运行和仍占执行槽的记录。同一任务只计一次。该限制不代替扫描、实际文件写入和 CD2 各自的队列、预算及保护；也不改变该队列的单 worker 执行规则。
- 总时间、结果大小及保留配置仍由 `config.toml` 的 `background_jobs` 控制，修改后重启。默认任务总时间 3600 秒、单任务结果 4 MiB、总逻辑容量 64 MiB、保留 7 天；范围分别为 30～86400、1～64、1～4096、1～365。总容量不得小于单任务容量。每个活跃任务仍预留容量，因此实际可受理数量可能小于通用设置的上限。
- 降级旧程序前先核对新增任务类型没有排队或仍持有执行槽。旧程序不识别新增任务类型，不直接对未完成的数据库回退；避免通过手工改状态来释放槽位。

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
