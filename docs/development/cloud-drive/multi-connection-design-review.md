# CD2 多连接设计评估

类型：技术评估与设计查漏。日期：2026-10-23。状态：依据已确认D1至D8给出实现建议，不是已实现功能或可直接执行的迁库脚本。需求权威入口为[需求分析](multi-connection-requirements.md)；迁移、兼容和验证另见[演进计划](multi-connection-evolution-plan.md)。

MC1接续：以下候选模型与查漏保留为评估依据，目标结构/API/容量及受限维修以[详细契约](multi-connection-contract.md)为准。旧配置无可信历史绑定、元数据客户端未验证双Build等差异均已进入实现清单，不能因本文有示例就认定功能已存在。

## 1. 核查结论

不能采用“多个JSON配置＋activeConnectionId”的方案。它仍会让API和worker按当前选择解释旧任务，并遗漏全局预算、唯一索引、路径世代、恢复与普通文件任务的关联。

建议采用稳定连接、独立目录配置、直接用户授权、不可变任务上下文四条业务主线；显示偏好不进入执行解析。首版保留现有全局请求与未知保护，不同时引入多连接并行调度。风控组与来源命名空间分别建模，但不得在尚未取得身份证据时开放拆组提速。

## 2. 已核对的源码事实

| 事实与证据 | 对改造的影响 |
| --- | --- |
| [配置DDL](../../../backend/app/core/cloud_drive_configuration_schema.py)与[配置服务](../../../backend/app/service/cloud_drive_configuration_service.py)固定id=1 | 不能仅给前端添加列表，服务层解析与修订CAS都需改造 |
| [预算与观察DDL](../../../backend/app/core/cloud_drive_schema.py)：预算池/租约固定1，盘点worker有全局唯一索引 | 多连接首版保留全局保护；不能直接将唯一键换为connectionId后宣称安全 |
| [预算服务](../../../backend/app/service/cloud_drive_budget_service.py)依赖endpoint_hash、root_path和当前cloudDrive | 任务必须解析固定上下文；预算及冷却不能因换配置清零 |
| [路径世代](../../../backend/app/service/cloud_directory_path_service.py)会关闭全部活跃会话并推进普通扫描/文件动作水位 | 定向配置失效需新路径，不能继续无差别调用旧全局推进函数；实际云写仍需保护来源别名 |
| [目录全局保护](../../../backend/app/service/cloud_directory_guard.py)及[隔离DDL](../../../backend/app/core/cloud_quarantine_schema.py)共享保守边界 | 全局单RPC不等于任务级写互斥；写批次及恢复期间也要保留现有槽语义 |
| [worker交接](../../../backend/app/service/cloud_inventory_worker_service.py)、各worker入口按单例修订读库 | stdin增加绑定引用而不是增加可覆盖端点；父进程、子进程与发送前均复核 |
| [网页受控入口](../../../backend/app/service/cloud_inventory_web_service.py)只支持固定单目录预算 | 多配置不能隐式升级为递归扫描或长队列调度 |
| [目录计划](../../../backend/app/service/cloud_directory_plan_service.py)绑定观察、policy_hash、来源摘要和原所有者 | C2/C3所有衍生记录必须沿原链解析，不能只改盘点API |
| [前端接口](../../../frontend/src/api/cloudInventory.ts)没有连接/目录引用 | 表单、任务列表、UUID恢复、CSV和弹窗上下文均需补齐 |
| [端点与路径校验](../../../backend/app/utils/cloud_drive_paths.py)限定HTTPS、无URL秘密、无子路径，按目录段比较 | 不借多连接引入HTTP、反代子路径或平台大小写折叠 |

现有协议投影及客户端只证明已经实现的Token/路径/版本核对，不证明存在可用的永久CD2实例ID或稳定云账号全局ID。不能把设备名、IP、证书指纹或版本号当成账号身份。若后续要自动关联别名，必须另查官方协议并以独立小范围验证支撑；本轮没有因此发送网络请求。

## 3. 领域边界与候选数据模型

领域定义见[术语](../../../CONTEXT.md)。下表是候选实体边界，表名/列名需在实施前冻结，不在本轮预定schema版本号。

| 候选实体 | 主要数据 | 关系与不变量 |
| --- | --- | --- |
| cloud_connections | 稳定UUID、显示名称、enabled/disabled/archived、metadataRevision、安全版本引用 | 无全局active标记；端点不作为主键；删除不复用ID |
| cloud_connection_versions | connectionId、安全修订、规范端点、规范公开PEM/摘要、校验策略 | 安全版本不可变；历史引用不能被UPDATE成新地址；不存Token/私钥 |
| cloud_directory_profiles | 稳定UUID、connectionId、名称、生命周期、元数据修订、安全版本引用 | 目录配置不原地转移连接；显示名称不参与执行身份 |
| cloud_directory_profile_versions | profileId、安全修订、批准根、映射标识、三个动作开关、隔离名称、来源引用 | 根或映射语义变化不可伪装成名称修改；安全开关默认false |
| cloud_connection_user_grants / cloud_directory_user_grants | userId、connectionId/profileId、授权修订、操作者/时间 | 用户与连接/目录唯一；连接、目录、动作权限取交集；无角色或未来目录通配符 |
| cloud_task_contexts | 稳定contextId、actorId、动作类型、连接/目录安全版本、endpoint/trust/root/mapping/policy摘要、创建时显示快照、绑定状态 | 被任务、计划、诊断和恢复显式引用；不能由客户端任意指定摘要覆盖服务端值 |
| cloud_source_namespaces | 稳定内部ID、身份核对状态、来源世代及别名关联证据 | 表示对象与路径语义，不提供独立预算；未知关系不视为独立 |
| cloud_risk_groups及成员关系 | 内部ID、来源关联、风险状态 | 首版全部仍受全局保守预算与未知保护；模型存在不代表可创建独立并发池 |
| cloud_user_preferences | userId、默认connectionId/profileId、偏好修订 | 仅预填，不授予权限；默认失效时提示重新选择，不无声切到其他来源 |

所有业务引用显式外键或等效校验。目录白名单必须验证其connectionId与连接授权一致，撤销连接授权后不能靠残留目录授权继续访问。授权审计独立保留；软删除/归档不级联删除版本、上下文、审计、预算或恢复证据。

不应把所有配置放入单个JSON数组：关联、唯一性、权限过滤、历史快照及修订竞争都需要可索引的稳定关系。公开PEM、可扩展非秘密政策可作为受白名单校验的载荷，但不以JSON绕过外键和归属验证。

本项目使用标准库sqlite3，没有ORM；本轮不引入ORM或生成与仓库不符的ORM模型。当前数据访问入口是配置服务的getCloudDriveConfiguration/resolveCloudDriveSettings及inventoryDb事务。后续DDL和对应sqlite3仓储方法成对评审，不能只给SQL而遗漏运行快照、权限、事务和迁移测试。

用于核对当前单例依赖的只读SQL如下；这是现状检查，不是迁移：

```sql
SELECT id, revision, updated_at FROM cloud_drive_configuration;
SELECT id, state, pause_reason, revision, cooldown_until FROM cloud_drive_budget_pool;
SELECT pool_id, session_id, phase, owner_pid FROM cloud_drive_rpc_lease;
SELECT id, actor_id, status FROM cloud_drive_inventory_jobs
WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running');
```

与项目现有sqlite3访问方式一致的只读示例，不假设存在ORM；使用前传入已有schema55副本路径，不导入应用、不创建库或迁移：

```python
from contextlib import closing
from pathlib import Path
import sqlite3


def inspectCloudConfiguration(databasePath: str) -> dict:
    uri = Path(databasePath).resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        configuration = connection.execute(
            "SELECT id,revision,updated_at FROM cloud_drive_configuration WHERE id=?", (1,)
        ).fetchone()
        pool = connection.execute(
            "SELECT id,state,pause_reason,revision,cooldown_until FROM cloud_drive_budget_pool WHERE id=?", (1,)
        ).fetchone()
        return {"configuration": dict(configuration) if configuration else None,
                "budgetPool": dict(pool) if pool else None}
```

## 4. 修订与影响分级

至少区分显示元数据、安全执行配置、授权、来源路径世代和任务状态五种修订；MC1另明确生命周期修订及会话绑定。单个“全局配置版本号”不足以兼顾安全与可用性。

| 变化 | 建议处理 | 不允许的副作用 |
| --- | --- | --- |
| 默认选择/列表筛选 | 只改个人偏好/视图 | 不推进配置、路径或预算修订，不重置Token或改变已受理任务目标 |
| 连接/目录显示名称 | 元数据CAS及审计，保留创建时快照 | 不使本来有效的盘点观察失效 |
| 端点、CA或TLS策略 | 不同端点新增连接，重新配置目录与直接授权。同端点更新 CA 追加安全版本，保留授权，旧安全依据和诊断失效。 | 不悄悄换地址恢复旧请求；不能据同证书断言实例相同 |
| 批准根、映射语义 | 新目录安全版本；核对父子范围/来源别名及受影响普通文件任务 | 不扩写历史路径；未知别名必要时保留全局旧计划栅栏 |
| 动作开关 | 新目录安全版本/政策修订；复核已受理、尚未发送的动作 | 关闭不代表已发送请求取消成功，开启不授权正式媒体 |
| 用户授权 | 授权修订立即复核，停止后续发送，保护已发出未知请求 | 不为无关用户/目录重新盘点，不恢复旧秘密 |
| 真正改名、隔离及恢复提交 | 推进来源路径世代；影响已知别名及重叠范围，普通挂载路径未知仍采用全局保守栅栏 | 不承诺只影响所选连接，不清理原未知证据 |
| 请求间隔/冷却 | 首版按全局安全政策CAS处理；有占用或未知时禁止放松 | 不按“当前选中连接”覆盖全局池；暂停和已消费额度不复位 |

默认选择零失效与云写跨别名保守失效并不矛盾。配置修改本身没有改变远端路径时，不应无差别推进所有CD2观察世代；但重新映射造成来源身份不确定时不能承诺定向失效。

## 5. 固定上下文的执行链

新建表单明确选择连接/目录 → GET本地政策与安全修订 → 用户确认 → 本地事务复核权限和父子关系/全局保护/版本 → 固定任务上下文及UUID → 明确start交接Token → 父进程复核 → 子进程按contextId读同库快照 → 每次RPC准入再次复核 → 原上下文内提交结果。

后续建议、计划、执行、核对、原UUID查询、反向计划与本地同步仅接受原记录ID及CAS条件。可以携带期望上下文用于防误点，但不得将浏览器提交的connectionId或endpoint作为覆盖来源。外部remoteId至少在固定来源上下文内解释，不能跨连接全局去重。

UUID保留原有全局唯一约束，指纹增加用户与上下文：同用户同UUID同意图返回原记录；同UUID不同上下文409，其他用户查询按不可枚举口径返回404。切换筛选后仍能查询原请求，但页面必须显示原连接而非当前选择；丢响应不自动生成新UUID或换连接重发。

Token仍只通过可信HTTPS及匿名stdin交接，不存队列。首版忙时立即给出原保护原因，不自动保留Token等待轮到另一连接；后台本地历史查询不受云RPC串行影响。

## 6. 权限与前端竞态

管理设置权限不自动等于使用权或替他人执行权。操作者需同时满足账号启用、会话有效、原动作权限、连接授权、目录白名单、本人记录归属、连接/目录状态、固定上下文仍有效。服务层和worker必须复核，不能只依赖路由装饰器或下拉选项。

授权撤销后拒绝媒体内容和导出；必要的脱敏状态、取消意图及管理保护诊断另定义最小返回字段，不能让用户因看不到内容而无法获知仍有未知操作。管理员诊断不自动获得其他用户正文、完整目录或代执行权限；跨用户紧急远端恢复建议不在首版隐含开放。

前端用会话代次＋连接/目录选择代次隔离异步结果。切换未提交草稿时清Token和确认勾选，重取所选政策；已受理/结果未知时保留原UUID及原来源提示，不切换请求目标。API403/404/409返回要使危险按钮失效；旧连接响应不得覆盖新连接列表或用户会话。

页面使用现有Vue3/Element Plus及六主题。列表搜索与分页、状态/操作可达、禁用原因、键盘焦点和长路径局部滚动作为交付条件；不为此引入新的UI依赖。本轮只写交互要求，没有启用UI实现技能或修改页面。

## 7. 分层诊断与状态

显式诊断与保存、切换、盘点分别建模。选择目录配置和本次Token根后，明确确认网络诊断；后台只执行固定阶段，不列目录、不读取正文，不保存秘密。每次诊断也有UUID、所属用户、上下文、截止时间及调用账本。

| 层 | 成功含义 | 失败/未做的准确显示 |
| --- | --- | --- |
| TLS | 指定端点的连接、名称/有效期/链验证通过 | 超时、名称不符、过期、信任失败分别记录；无自动HTTP回退或证书跳过 |
| Token | 已验证的自检接口确认权限与精确根匹配 | 不将主账号密码当Token；错误根/过宽/未知权限拒绝，秘密不进错误文本 |
| 版本 | 得到Core与CLOUDAPI精确Build并匹配已验证组合 | 没有运行信息权限则标记未知，不猜版本、不据此开放云写 |

TLS成功不意味着Token有效；Token自检通过不意味着目录存在或写安全；版本通过不意味着本次媒体动作获授权。允许保留“TLS通过、Token未提供、版本未知”等分层结果；不输出模糊的单一绿灯。

建议Token/运行信息诊断最多2个应用RPC、30秒、小型元数据上限；TLS握手与网络错误另记网络事件，不伪记为115应用RPC。具体字节上限和方法清单须按现有协议投影在实现前冻结。诊断调用与盘点共享全局准入、间隔、暂停和未知保护，不能成为免预算旁路；管理调用也单独计数，不自动登录主账号。

结果绑定contextId、安全修订、时间及实际阶段；改地址/证书/根后旧结果显示已过期。配置页只读刷新不发起诊断，没有后台探活任务、失败轮询或自动故障切换。

## 8. API边界建议

候选API命名供后续详细接口评审，不是已存在端点。v1.4.0旧接口的退出/兼容必须另按演进计划处理，不能默默把旧请求指到默认连接。

| 接口族 | 请求关键字段/响应 | 规则 |
| --- | --- | --- |
| /api/settings/cloud-connections | POST/GET；连接详情及PUT带expectedMetadataRevision或expectedSecurityRevision | settings:write；保存无网络；列表不返回秘密 |
| /api/settings/cloud-connections/{id}/directories | 创建/更新目录及安全修订 | 服务端固定parent，禁止原地跨连接迁移 |
| /api/settings/cloud-connections/{id}/user-grants | userId、期望授权修订、显式directoryProfileIds | 管理权限；所有目录必须属于此连接；事务写审计 |
| /api/cloud-drive/connection-choices | GET当前用户可用连接/目录及脱敏状态 | 只查库；无权目录不暴露根路径，无“全部连接”隐式授权 |
| /api/cloud-drive/inventory-policy | GET带connectionId、directoryProfileId | 返回绑定ID、期望安全修订、policyHash、固定预算；无网络 |
| /api/cloud-drive/inventory-jobs | POST新增明确上下文与原有requestId/selectedPath/confirmRead | 受理时固定目标，不接受仅默认选择 |
| /api/cloud-drive/inventory-jobs/{id}/start | 原expectedRevision、policyHash、apiToken及明确确认 | 从原job解析目标，不新增可覆盖endpoint参数 |
| /api/cloud-drive/connection-diagnostics | POST固定上下文、requestId、诊断阶段和confirmNetwork；Token仅该请求 | 返回分层结果或202及原请求查询入口，不能请求任意RPC方法 |
| 任务/计划/日志历史与查询 | 增加可选connectionId/directoryProfileId筛选及原上下文显示 | 筛选不授予权限，先过滤授权及本人归属再分页/计数 |
| 个人偏好 | 默认连接/目录ID和偏好CAS | 只允许当前可用组合，不能代表用户创建云任务 |

新建盘点的候选调用示例（全部为合成ID；不是现行接口可用承诺）：

```http
POST /api/cloud-drive/inventory-jobs
Content-Type: application/json

{
  "requestId": "c981cd62-d76a-4e47-9a4c-b090d3d4c621",
  "connectionId": "5b0e9ce0-e201-4df6-b43d-de3fb0d9d920",
  "directoryProfileId": "6f880e2d-67ee-4523-85a1-cf0491c205ae",
  "expectedConnectionSecurityRevision": 2,
  "expectedDirectorySecurityRevision": 3,
  "selectedPath": "/cloud-a/media/tv/category",
  "policyHash": "1212121212121212121212121212121212121212121212121212121212121212",
  "confirmRead": true
}
```

前端先用选择的两个ID获取policy，再把policy返回的修订和摘要与本次范围提交；start仍使用原任务ID，Token不出现在prepare载荷中。具体响应的contextId、安全修订、连接/目录显示快照及本地/网络副作用标志必须成对定义并做契约测试。

建议错误语义：字段/父子关系不合法400，认证401，无权按内容敏感度统一403或404，修订/UUID/保护竞争409，明确容量超限429（禁止自动重试），配置存储不可用503；细分稳定reasonCode，页面保留原requestId。不得将云端原文或含Token请求对象直接回显。

## 9. 高风险场景与处理建议

| 场景 | 需要预防的遗漏 | 建议边界 |
| --- | --- | --- |
| 两连接不同地址，同一CD2或同一115 | 按URL分池绕过风控 | 首版全局串行；身份/风控模型不自动授予独立性 |
| 同连接不同云挂载，同名/相同remoteId | 错认同一对象或跨云隔离 | 固定目录配置、来源上下文与原条目关系；跨配置计划拒绝 |
| 不同连接的已知别名范围相等或重叠 | 改名后只失效一个观察 | 同连接批准根重叠应拒绝；跨连接来源路径世代仍核对重叠/别名，未知时保守影响范围 |
| 多个连接指向同一物理媒体，另有SMB/盘符扫描 | 放松普通文件任务栅栏 | 保留全局普通扫描/文件计划水位，直到有可信映射模型 |
| 修改名称/个人默认选择 | 无意义全量失效 | 元数据/偏好与执行修订分开 |
| 管理者在worker启动/发送瞬间撤权 | 路由权限快照过期 | 受理事务、交接及每RPC复核；已发出未知不清槽 |
| 改地址或克隆连接后按旧UUID重试 | 误发新目标/重放未知写 | 原UUID绑定原上下文，冲突不创建第二意图 |
| 停用/归档时仍有未知请求 | 操作入口消失而保护丢失 | 允许脱敏管理诊断和取消后续发送；保留记录与全局保护 |
| 原端点证书过期且有未知写 | 改配置被保护拦截、核对又无法TLS | 需单独受限信任维修流程；不通过普通改配绕过，不对未知写换目标；未证明前保持冻结 |
| 账号禁用后无人可核对原请求 | 默认让管理员接管全部云权限 | 首版仅最小诊断，不隐含代执行；紧急恢复单独评审 |
| 新目录加入后旧“全部目录”授权 | 意外暴露私人媒体 | 保存显式目录ID白名单，不含未来目录 |
| 允许父根但误认为另一未授权子配置可阻止访问 | 用户把配置白名单当成文件系统拒绝规则 | 已确认D8：同连接禁止重复/祖孙批准根，跨连接相同根允许；已知别名提示有效授权并集，未知别名不能假称自动识别。不是通用文件系统deny ACL |
| 恢复备份后IP/DNS被其他设备占用 | 旧Token被送错服务器 | 无Token持久化；重新核对身份/信任，不按名称猜新对象 |
| 修改CA链但旧证书仍可用 | 配置验证被误认为已连接 | 格式检查、真实TLS、来源身份、云写能力分层 |
| 多连接历史/缓存增长 | 配置数乘上原额度、全表查询 | 保留全局容量；索引按owner/connection/profile/time/id，分页计数与授权一起过滤 |
| 连接归档后导出/本地日志查看 | 把只读历史重新变成执行输入 | 有权历史独立可看，失效状态明确；不可自动回填新上下文 |

## 10. 冻结门禁与未放行项

已确认的是D1至D8，不是自动诊断/取Token/并行/代执行。细化实现前必须冻结：所有调用者传播contextId的清单；DDL/外键/索引与CAS仓储方法；分层修订失效表；诊断方法/预算及错误表；证书维修受限状态机；容量上限与迁移批次界限。遇到没有身份证据或无法安全恢复的条件，应记录明确阻塞和人工处理边界，不填入“待实现后自然解决”。

本评估没有证明远端永久实例ID、云账号ID、跨协议对象映射或防外部并发替换能力。这些不作为首版正常使用的自动前提，也不被用来放松全局保护；需要未来新增能力时单独核验。

## 11. 补充查漏：容量、草稿及恢复

- 历史安全版本、诊断、上下文也有容量成本，不只限制连接数量。不可变版本优先由上下文引用，不把16KiB公开PEM重复复制到每条观察；引用中的版本不得被垃圾回收，归档不能删审计和未知证据。未引用版本的清理规则需MC1明确，首版不默认自动物理删除。
- 达到逻辑配额时停止新增配置/诊断/任务，不通过新建连接获得新配额；仍应允许有权只读查询、原请求核对及取消后续发送。物理磁盘已满导致审计写失败时，不能宣称取消/停用已成功，必须保留原请求和保护并明确报告失败。
- 连接复制不是首版必要入口；若提供，要重新选择目标、授权和动作开关，复制的安全版本不能冒用旧验证状态。配置导入/导出亦非首版承诺，避免把网络地址、根和授权列表当无敏感性的公共模板。
- 首版不支持为同一连接设置多个自动备用URL。地址变更需新安全修订；同一真实CD2的不同入口可作为不同具名连接明确选择，但不能作为旧请求失败后的自动目的地。
- 连接/目录停用后的恢复与普通新动作不是同一权限路径。默认阻止新任务；查询既有未知、脱敏管理诊断和取消后续发送仍需入口。远端只读核对必须有原来源证据和新的明确预算，不能为了解死锁而启用云写。
- 信任维修是独立安全门禁：同端点证书更新、新地址替换、云账号重绑的风险不同；维修不得改变原写意图、重发旧RPC或自动释放未知。若不能证明可安全只读核对，保持冻结并给出人工处理前提，不隐藏为笼统连接失败。
- 诊断必须有原UUID查询、时间/安全修订和分层结果；端点/CA变更后旧绿灯失效，不等同故障。诊断失败文本仅稳定错误码与安全摘要，不保存请求体、Token指纹、完整云端原文或任意响应。
- 列表总数与搜索结果也可能泄漏连接/目录存在；普通用户先按连接、目录授权和所有者过滤，再计数/分页。管理列表与可使用连接列表为不同接口，不把管理者可见等同媒体使用权。
