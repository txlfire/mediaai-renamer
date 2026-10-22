# CD2 只读盘点队列实施

类型：实施与演进。更新：2026-10-17。承接[C0预算](c0-budget-implementation.md)。本批实现内部执行基础、只读观察工作台及合成验证；精确根本层真实 worker 验收已通过，不注册自动扫描循环。[C1-2 受控网页入口](c1-2-web-entry.md)的默认关闭接口及页面已完成，合成浏览器和一次专项真实HTTPS接口验收通过；正式部署、真实系统缩放仍需独立核对。

## 执行边界

- schema41增加独立任务、目录队列和元数据观察表；schema42增加本次保留例外和观察清理状态，保留schema40预算及原业务数据。
- 一个批准会话最多一个盘点任务；固定分类/排除/模式、总条目/元数据/目录/深度预算，幂等UUID不能重置已用量或期限。
- 全实例只允许一个盘点worker。先持久化PID再通过匿名stdin管道交接端点、受限令牌和CA；秘密不进命令行、表或日志。子进程不导入app.main、不初始化数据库。
- 目录流式处理，解析批次最多128条；消息接收先记消费，完成一层目录才放行其子目录。父目录直接文件保留，排除目录不落条目或递归。
- 只有完整读完且本地保存成功的目录才能标完整。部分观察可查看但不能推断missing；超限、取消、断流、进程退出都不能伪报完成。
- 量子只在已完成目录边界暂停，显式继续不重置批准总预算。中断或不完整目录本批不自动重读，未知消费须另行核对/批准，不能靠重启免费重扫。
- 队列/缓存按任务和全实例双重限量，触顶即暂停，不淘汰旧数据触发自动重扫；支持手动分批清理观察和当前视图导出，不自动按天删除。
- API只本地查询/分页、取消和观察维护，按用户直接权限及任务所有者隔离；不暴露worker令牌或云凭据，不产生远端写入。

## 检查点

- [x] schema40升级、幂等任务与固定范围、预算和容量。
- [x] 有界流式入库、部分结果、分类串行与目录完成边界。
- [x] 独立进程交接、监督与取消、崩溃后不重放。
- [x] 所有者隔离、分页、迁移及合成TLS/进程测试。
- [x] 当前条目保留、当前视图导出与手动分批观察清理。
- [x] 本批内部实现完整回归、文档和交接；不含真实CD2接入验收。

进度见[工作日志](../../work-logs/progress-2026-10-16-cd2-inventory.md)及[真实验收续作](../../work-logs/progress-2026-10-17-cd2-acceptance.md)。真实CD2/CLOUDAPI1.1.2、fnOS原生安装、TLS/CA/IP/指纹、HTTP2及精确根受限令牌已核对；修复 Token 虚拟根映射后正常独立 worker 保存 72 个直接子目录、scopeComplete=true，Token 已撤销，租约/worker槽均0。原一次性批准耗尽，不自动继续；不代表子树完整、整库规模、风控或云写验收。C2/C3不开放。

## 数据与容量

迁移 SQL 位于[DDL](../../../backend/app/core/cloud_drive_schema.py)，参数化 sqlite3 实现在[盘点服务](../../../backend/app/service/cloud_inventory_service.py)。本项目没有 ORM，不引入额外映射层。

| 对象 | 约束 |
| --- | --- |
| cloud_drive_inventory_jobs | UUID/request_id 幂等；会话唯一绑定任务；固定范围、模式、排除、预算和会话原到期时间；状态、revision、接收/保存计数和 worker 所有权 |
| cloud_drive_inventory_directories | 每任务路径唯一；pending/reading/partial/complete，generation 和深度；只有完整父目录才能排入子目录 |
| cloud_drive_inventory_entries | 每任务路径唯一；目录及 generation、名称、类型、大小和逻辑字节数；不保存内容、直链或云凭据 |
| cloud_drive_inventory_keeps | entry_id 主键、条目外键级联删除；job_id 及时间；只绑定当前观察，不按名称或路径跨盘点继承 |

默认每任务 10,000 条接收记录、32MiB 元数据预算、200 目录、递归深度 8、每量子 20 目录；overview 强制深度 0。默认值与离线建议中的候选值不是同一承诺，实际执行只认批准任务预算及会话原有 RPC/时间上限。

可批准硬上限：每任务 1,000,000 条、256MiB、10,000 目录、深度 32、每量子 200 目录；全实例最多 1,000 个任务、10,000 个目录队列行、1,000,000 条观察和 256MiB 逻辑缓存字节。全部历史任务共用容量，不因量子继续、取消或重启清零。达到容量停止，不删除旧任务或重新扫描。

接收预算按整条 protobuf 消息先计数，随后才保存，因此一次消息可以超过剩余预算；超过后保留消费计数但拒绝继续。gRPC 单消息上限仍有效。保存每批最多 128 条，数据库唯一索引去重；发现重复则回滚当前保存批次并保留已经消费的接收计数。后续异常可能使尚未提交的不足 128 条观察不落库，目录仍为 partial，不能据此判定文件不存在。

已经满额的全局/任务缓存、接收预算，以及不足两次 RPC 的剩余会话预算，均在下个目录开始前拒绝。单目录需先验令牌再列举，不能浪费最后一次额度只完成自检；该暂停不补额、不伪报目录完成，也不产生未决 RPC 租约。

逻辑缓存字节按路径、名称、远端 ID 的 UTF-8 长度和固定开销估算，不等于 SQLite 文件、索引、保留例外、审计、WAL 或上游网络流量的物理大小。手动清理释放逻辑观察/目录容量，不压缩数据库文件、不执行 VACUUM；不得用普通日志清理删除预算和幂等历史。历史任务/批准的1000行上限不因观察清理而重置，达到后停止新增批准；历史归档须另定政策。

## 状态与进程

- 内部批准得到 queued；显式执行领取唯一 worker 槽后 running。完整完成批准范围得到 completed，overview 只表示所选目录各一层完整，并不表示整棵子树已盘点。
- 已完成目录达到量子边界后 paused/quantum_boundary；只有 worker 已回收、revision 匹配、用户确认、会话/池仍有效才能重新排队。计数、到期时间和已完成目录均保留，不重新列举旧目录。
- 超预算、断流、非法条目和本地保存失败得到 paused；读到一半的目录为 partial。此类任务本批不支持恢复。进程意外退出得到 interrupted，不自动重放。
- 取消先持久化请求；排队任务直接 cancelled，执行中任务须等待监督者回收。取消不代表远端停止，不清除 sending/unknown RPC 租约。即使任务槽已回收，未决 RPC 仍阻止后续访问。
- 子进程 PID 在 stdin 交接前持久化。Windows venv 沿用 CPython multiprocessing 的 base executable 和 `__PYVENV_LAUNCHER__` 启动方式，避免启动器 PID 与执行 PID 不一致；保留隐藏窗口和虚拟环境依赖。
- 监督周期 0.2 秒，只做本地检查，不发网络请求或逐轮写审计；每目录总时间最多 60 秒，排队、自检、网络和本地保存均计入。量子还受不超过会话剩余时间和 900 秒的单调时钟上限保护。
- 监督者确认退出前不释放任务槽；审计记录批准、领取、状态变更、取消和回收，不逐文件写审计。异常状态保留固定原因码，不记录远端原始错误、令牌或端点。

## 本地 API

以下本地观察接口要求应用会话、`scan:run` 和任务所有权；管理员没有隐式跨用户读取例外，请求均不连接 CD2。后续默认关闭的准备/启动接口单独列在[C1-2](c1-2-web-entry.md)，不改变本节本地查询语义。

| 接口 | 参数与结果 |
| --- | --- |
| GET /api/cloud-drive/inventory-jobs | page 默认 1、范围 1..1000；pageSize 默认 20、范围 1..50；只列当前用户任务，倒序分页，返回 items/total/page/pageSize/networkAccess=false；任务总数受 1000 行硬上限约束 |
| GET /api/cloud-drive/inventory-jobs/{jobId} | 规范 UUID；返回状态/reason/revision、批准根、实际选中/排除范围、模式、接收/保存计数、目录状态统计和 limits；freshness 固定 unknown，estimatedTotalEntries 固定 null，executionAvailable/writeAvailable 固定 false；不暴露 PID/worker token/端点 |
| GET /api/cloud-drive/inventory-jobs/{jobId}/entries | afterId 默认 0、非负 int64；limit 默认 100、范围 1..200；按本地自增 ID 分页，返回 items/hasMore/nextId/networkAccess=false；条目含 name/relativePath/isDirectory/size/directoryComplete |
| GET /api/cloud-drive/inventory-jobs/{jobId}/directories | afterId 默认0、非负int64；limit默认50、范围1..200；只读本地目录队列，返回items/hasMore/nextId/jobRevision/networkAccess=false；条目为id/relativePath/depth/status，批准根自身的相对路径为空字符串 |
| POST /api/cloud-drive/inventory-jobs/{jobId}/cancel | 无请求体；返回任务摘要，worker 槽仍持有时 202，否则 200；不把取消受理解释为远端请求已停止 |
| POST /api/cloud-drive/inventory-jobs/{jobId}/advice | 对本地观察分页计算整理建议，参数及保守规则见下节；不创建动作计划 |
| PUT /api/cloud-drive/inventory-jobs/{jobId}/entries/{entryId}/keep | 严格布尔 kept、非负 int64 expectedRevision；保存/取消当前观察的保留例外，200 返回摘要 |
| POST /api/cloud-drive/inventory-jobs/{jobId}/clear-cache | confirmClear 必须严格为 true、expectedRevision 匹配；每次至多4个短事务，每事务至多1000条；清理未结束202，结束200，返回摘要 |

错误：未登录 401、缺权限 403、非所有者或任务不存在 404、无效 UUID 400、分页越界/非严格参数 422、观察状态/版本/条目归属冲突 409、表/存储或审计写入失败 503。清理未显式确认为 true 时400。旧只读工作台不调用内部执行函数；后续 C1-2 另设受控启动边界，仍没有 HTTP 续扫入口。

本地查询示例，任务 ID 和应用令牌取自已批准的当前实例，不使用 CD2 凭据：

```powershell
$headers = @{ Authorization = "Bearer $env:MEDIAAI_SESSION_TOKEN" }
$uri = "http://127.0.0.1:8970/api/cloud-drive/inventory-jobs/$env:MEDIAAI_INVENTORY_JOB_ID"
Invoke-RestMethod -Uri $uri -Headers $headers
$page = Invoke-RestMethod -Uri "$uri/entries?afterId=0&limit=100" -Headers $headers
$page.items
Invoke-RestMethod -Method Post -Uri "$uri/cancel" -Headers $headers
```

结果只能反映已保存观察；unknown 总量不能显示成 0，directoryComplete=false 不能据此提出缺失或删除意见。没有批准任务时返回 404，不自动创建。

摘要追加只读字段：createdAt/updatedAt（Unix秒，分别为任务批准/状态更新时间，不冒充远端观察时间）、storedBytes（逻辑缓存）、remainingEntries及remainingReceiveBytes（按接收预算计算，超限后归零而不隐藏原接收量）。budget只包含maxRpc/usedRpc/remainingRpc/status/expiresAt/expired，不返回会话ID、端点或凭据。expired为读取时本机时间核对，不修改持久化批准状态；剩余额度不代表池未暂停、令牌有效或可继续执行，也不是115侧配额。摘要、条目和目录分页使用短只读事务，防止多条查询拼接出清理前后的混合状态。

目录只返回已发现、已入队范围；pending不是空目录，complete仅表示该目录本层完整，不表示整棵子树完整。清理中/已清理与条目接口一致返回409，不重新发现目录。调用示例：

```powershell
$summary = Invoke-RestMethod -Uri $uri -Headers $headers
$summary.budget
$directories = Invoke-RestMethod -Uri "$uri/directories?afterId=0&limit=50" -Headers $headers
$directories.items
```

## 本机核对

仅用明确配置文件核对本地任务槽，不初始化数据库、不连接 CD2：

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m app.recover_cloud_drive --config config/config.toml `
  --inventory-job-id $env:MEDIAAI_INVENTORY_JOB_ID
```

预期输出任务状态、workerSlotHeld、本机 PID 和主机摘要；没有自动回收。只有确认相关客户端停止、远端静止、同主机且原监督者和 worker 均已退出时，才加 `--apply --confirm-clients-stopped --confirm-remote-idle` 释放任务槽。PID 未知、活进程、异机、探测失败或所有权变化均拒绝；不能伪造 PID 绕过。

任务槽与 RPC 租约必须分开核对；`--inventory-job-id` 和 `--lease-id` 不允许同时提供。释放任务槽不会释放 RPC、恢复预算池、补额或继续任务。后者仍按[C0 维护契约](c0-budget-implementation.md)核对。正式实例启动前先备份数据库、配置与匹配密钥；本批不提供 schema42 回退到 schema39/40/41 的删表脚本。

## 本地整理建议

接口为 `POST /api/cloud-drive/inventory-jobs/{jobId}/advice`，权限/所有者检查与查询一致，只读本地数据库，不记录逐项分析审计，不创建扫描或写任务。

```json
{
  "afterId": 0,
  "limit": 100,
  "folderEntryIds": [],
  "folderTerms": ["论坛推广"],
  "keepEntryIds": []
}
```

字段均可省略。afterId/limit 与分页边界一致且为严格整数；两个 ID 列表均最多 200 个正 int64，必须属于本页，folderEntryIds 还必须是目录。folderTerms 最多 50 个、每个 2..80 字符，禁止路径分隔符、首尾空格和控制字符；只按字面匹配，不执行正则、URL 或命令。拒绝额外字段，不接收 API 令牌。ID 由前一次本地分页得到，不接受名称来隐式扩大范围。

返回保留原分页字段，增加 ruleVersion=`metadata-advice-v1`、规则及当前选择摘要 rulesHash、jobRevision、freshness=`unknown`、contentReadBytes=0、externalSubmission=false、writeAvailable=false 和 keepExceptionsPersisted=false。

每项包含 decision=`keep/review`、protection、evidenceLevel=`none/low/medium`、固定 reasons、partialObservation、folderSuggestion、selected=false、actionAvailable=false。无已校准概率，无“可执行删除”状态；keep 表示默认保留，不代表经正文鉴定为无广告。

- 字幕、NFO、校验/播放列表、海报图、说明文档、特殊影视结构、季目录、可识别的预告/花絮/特典等优先保护；本次 keepEntryIds 优先于线索。未命名为关联媒体的未知内容无法仅靠元数据识别。
- 推广短语、域名或 URL 样式仅为可解释线索；同时命中两类也只要求人工复核。不因 TXT/图片/视频后缀、小体积、单独 AD 或“广告”字样推断删除；不读取文件来补证。
- 目录净化必须显式指定影片目录 ID，不自动选择电影根、欧美等组织目录。只移除用户批准的字面词，保留年份、季、分辨率和已识别版本标识；结果为空、特殊结构、父目录观察不完整或已观察到同名目标时阻止建议。
- 未发现精确同名目标时仍返回 conflict=`unknown`；没有实机大小写、Unicode、对象身份或条件改名验证，不能解释为写前检查通过。
- advice 中 keepEntryIds 仍仅作用一次请求（keepExceptionsPersisted=false），不悄悄变成写接口；通过独立 keep 接口保存的例外在刷新和翻页后仍生效，条目返回 userKept=true，savedKeepScope=current_job_entries。重新盘点不继承。重复查看只重新计算本地有界页面，不发 RPC、不读取文件内容。

调用示例：先查询 entries 取得明确的影片目录 ID，再填入请求，不将组织目录全部选中；应用令牌仍取自已有登录流程。

```powershell
$body = @{ afterId = 0; limit = 100; folderEntryIds = @(); folderTerms = @('论坛推广'); keepEntryIds = @() } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$uri/advice" -Headers $headers -ContentType 'application/json' -Body $body
```

## 合成规模基准

[基准脚本](../../../backend/benchmarks/cloud_inventory_scale.py)只生成 128 条合成批次，使用独立临时库，不加载正式配置、不发网络、不记录秘密。测量本地落库耗时、100 次 keyset 分页 P95、峰值 RSS 和 SQLite/main/WAL/SHM 大小；不以模拟元数据量换算 2PB 实机支持能力。

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe backend/benchmarks/cloud_inventory_scale.py --entries 100000
.\.venv\Scripts\python.exe backend/benchmarks/cloud_inventory_scale.py --entries 1000000
```

预期：返回 temporaryDatabase=true、realCloudRpc=0、保存数量与合成数量一致；结束删除临时库。100次keyset查询覆盖前、中、后段，而非只查前十万条。只覆盖本地 sink/SQL，不测真实 gRPC、CD2 缓存、115 流量放大、风控或全库查重。合成客户端不实施实际网络总期限，因此百万条本地保存成功不证明真实百万文件扁平目录可在60秒内读完；未完整目录仍按partial保护。真实 TLS/分批协议仍以独立联测为证据。正式部署应使用自身受支持的 Python 环境，不依赖本机测试解释器目录。

## 只读工作台

入口 `/cloud-inventory`，导航“目录整理”，要求 `scan:run`；用户确认沿用 Vue3、Element Plus 与既有六主题，不新增 UI 依赖。该入口仍属于未发布源码，不回写 v1.3.0 功能清单。

1. 从本地已批准任务中选择盘点，核对模式、选中/排除范围和新鲜度未知状态。没有任务时显示空态；新增“新建盘点”入口遵守[C1-2](c1-2-web-entry.md)的单目录、固定预算和HTTPS门禁，默认不启用执行。原请求未知时入口改为“核对原请求”，不提供自动续扫。
2. 每页读取 100 条，上一页/下一页使用本地 keyset 游标；“全部条目 / 目录建议 / 广告线索”只筛当前页，页脚明确当前页数量，不冒充整个任务筛选或总库统计。
3. 明确勾选本页影片目录、添加批准的字面净化词，再确认只生成建议。改词后旧建议标记待分析；翻页、换任务或刷新清空选择，不能把上一页目录带入下一页。
4. 详情允许保存/取消本次本地保留例外；表格动作使用公共有色纯图标、固定右列及可访问名称。无云端改名/移动/删除按钮；显示“需人工复核”不是删除授权。
5. 执行中按 1.5 秒只读本地摘要，不自动重新读取目录列表；隐藏页面暂停观察。观察失败停止自动轮询，手动刷新恢复。已保存结果有变化时提示更新，不静默替换用户正在分析的页面。
6. 取消需确认；202 只是取消意图，不清除未知 RPC。旧任务响应、卸载后响应和旧会话响应不得覆盖当前视图。
7. “导出本页”只下载当前筛选视图（最多200条），不会自动翻页读取整个任务。UTF-8 BOM CSV、相对路径、规则版本/摘要；所有字段引用并转义，公式前缀按纯文本处理。草稿、加载或已清理时禁用导出；不导出批准绝对根、云身份或凭据。这不是可执行动作计划。
8. “清理观察”需确认；执行时移除旧结果，逐请求串行推进，隐藏页面在当前响应后停止后续批次，切换任务/卸载不继续派发。失败不重试，先刷新摘要再显式继续，不假装全部回滚。
9. 范围详情包含范围、预算、目录覆盖三个页签。预算随原有本地摘要更新，不新增云请求；目录覆盖仅在用户打开页签或手动分页/刷新时查询，每页50条，显示pending/reading/partial/complete，关闭弹窗或换任务后忽略旧响应。清理后不查询目录缓存。批准及任务更新时间不等同观察时间，新鲜度仍为unknown。

最终浏览器验证为合成 API 的 216 个六主题/亮暗/侧栏/视口场景，加16个清理中/已清理状态场景，共232场景通过。另以真实 FastAPI 和临时库验证登录、100+9 分页、保护条目、目录建议、取消、弹窗 ESC 和焦点恢复及生命周期维护。视口含 3840×2160、1920×1080、1536×864/764、1366×768、1280×720、1024×640、800×600、390×844；窄屏任务选择器独占一行、保留可读宽度。这里是 CSS 视口证据，不代替真实 4K/Windows200%/浏览器125%或读屏验收。最终自动化次数与剩余限制见[本批工作记录](../../work-logs/progress-2026-10-16-cd2-inventory.md)。

## 本地观察生命周期

用户已批准保守政策，不自动按天清理、不跨盘点复用例外。jobs 新增 cache_state=available/clearing/cleared、cleared_at、selected_json；选中范围独立于目录缓存，schema41 升级时从 depth=0 目录保存到 selected_json。预算、request_id/hash、原期限和接收消耗不重置。

只有 completed/cancelled/interrupted 且 worker_token 为空、同批准会话没有 reserved/sending/unknown RPC 租约时才允许清理。暂停任务必须先取消；异常残留 worker 只能按维护流程核对退出，不能借清理按钮释放槽。每一个分批事务重新检查所有者、权限、revision、终态、worker 和 RPC。

清理顺序为观察条目（相应保留例外外键级联）再目录；每事务至多1000条，最多4个事务后响应。每批递减 stored_entries/stored_bytes、递增 revision；接收总数、已完成目录历史计数和批准预算不变。开始/结束写同事务审计，不逐行记日志；清理本身不删除审计。中断后保留 clearing，可刷新后按新 revision 显式继续。全部清理后保留任务 tombstone 和范围；条目/建议接口409，摘要仍可读，页面明确提示并禁用分析/导出。不提供自动重新扫描。

以下调用使用现有应用登录会话；变量 `$uri`、`$headers` 与上节一致。每次清理后先看 cacheState/revision，不循环重试未知响应。

```powershell
$summary = Invoke-RestMethod -Uri $uri -Headers $headers
$entryId = $env:MEDIAAI_LOCAL_ENTRY_ID
$body = @{ kept = $true; expectedRevision = $summary.revision } | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "$uri/entries/$entryId/keep" -Headers $headers -ContentType 'application/json' -Body $body

$summary = Invoke-RestMethod -Uri $uri -Headers $headers
$body = @{ confirmClear = $true; expectedRevision = $summary.revision } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$uri/clear-cache" -Headers $headers -ContentType 'application/json' -Body $body
```

本批不支持：跨盘点长期例外、整任务后台导出、按天自动删除、历史批准归档、数据库物理压缩、部分目录自动恢复。真实 CD2/115 接入条件不因本地缓存维护已通过而解除。

## 生命周期验证

后续复核补充：摘要轮询收到 clearing/cleared 时统一作废观察和目录缓存、晚到请求、详情与选择，导出/分析/保留再次检查状态；确认框晚到不能提交旧保留。独立 worker 首次业务核对前初始化受控日志，启动失败保留 jobId 与固定原因，在所有权仍匹配时保存暂停原因。修复及最终验证见[复核修复记录](../../work-logs/progress-2026-10-17-cd2-review-fixes.md)。

临时库测试覆盖schema41升级、重复初始化、保留刷新与取消、跨盘点不继承、所有者/条目/版本隔离、未结束或worker槽未释放拒绝清理、未知RPC不释放、分批中断后继续、审计失败回滚以及清理后的409和幂等摘要。浏览器另验证实际CSV下载、当前视图范围、保留往返、202→200清理及禁用旧结果。

十万条合成本地缓存清理使用26次服务调用，至多4000条/次，总1.825秒，调用P95 193.556毫秒、最大219.587毫秒；预算/接收计数保留、只记录开始和完成两条清理审计。直接生成临时SQL缓存，未发送RPC，不证明真实网络、HTTP并发、2PB规模或115风控。物理库24,657,920字节未压缩，不能把逻辑容量回收写成磁盘空间已释放。

最终命令、次数与已知限制以[本批记录](../../work-logs/progress-2026-10-16-cd2-inventory.md)为准；Windows受控WebDAV跳过、真实显示环境和真实CD2接入条件不据此关闭。

## 预算与目录覆盖追加验证

后端两环境各972项（960通过、12受控WebDAV跳过），前端42文件307项及类型/生产构建通过；新增弹窗9视口×六主题×亮暗的108场景通过，覆盖预算只查摘要、目录50+1分页、无越界/命中错误、ESC焦点返回。真实临时API验证预算过期显示、完整目录只代表本层、明确刷新和清理后不读缓存。仅目录页签显式查询本地，不使用管理页登录信息。审计页沿用现有权限和查询，通过字典补齐CD2预算/盘点的中文类型与筛选项。

百万条本地合成观察保存134.774秒，100次前中后段keyset查询P95 3.563ms、最大响应15,173字节；峰值RSS44,597,248字节、相对初始峰值增加516,096字节；main303,415,296字节、当次WAL/SHM为0，结束清理临时库。以上不是网盘流量、CD2缓存、真实超大扁平目录或多客户端高频并发验收。

## 真实接入续验与错误追溯

2026-10-17，真实TLS、精确根两权限Token及含Build日期/时间的版本自检已通过；首次独立worker列目录在可用元数据返回前停止，接收/保存0，目录partial、scopeComplete=false，不能标为目录为空或盘点完成。后续受控续验及消费以[当日工作记录](../../work-logs/progress-2026-10-17-cd2-acceptance.md)为准，网页创建/执行入口仍关闭。

实机失败暴露不同层错误白名单不一致的问题。现将16项标准gRPC失败码集中定义，预算、任务和worker日志复用固定枚举，并补齐中文原因。合成TLS/独立进程测试验证NOT_FOUND可追溯，任意文本/非字符串仍归并worker_failed，不记录远端details或Token；该合成错误码不代表实机首次失败就是NOT_FOUND。暂停、未知租约和禁止自动重放的行为不变。

后续用户确认新根后仍not_found；另获一次虚拟根诊断授权，在精确根Token下请求虚拟`/`收到72条、17,902字节，全部为虚拟根直接子项。旧适配器因误按全局路径校验而拒绝，诊断未落库、未知租约保留。适配器已补请求/响应命名空间转换，独立worker合成联测断言传输为`/`、落库仍是批准根下完整路径；40项定向36.016秒通过。最新全量及真实修复后结果继续以当日工作记录为准，不沿用修复前979项冒充最终通过。

最终全量为Python3.14/3.11各982项，均970通过、12受控WebDAV跳过。用户另批准全量通过后在传输任务均0时正常重启CD2一次、核对旧进程退出，review并完整冷却后，修复后的正常独立worker实际overview已completed/scope_complete：接收并保存72条、目录complete1、worker槽/RPC租约0，Token撤销并核对不存在。只读核对72条均为批准根的直接子目录，落库仍使用全局绝对路径；本层完整不证明子目录内容完整，也不开放网页创建/执行、目录改名或删除。原失败任务和所有消费/审计保留。
