# MC5 网页接入记录

类型：实施与验证记录。日期：2026-10-27。目标见[详细契约](multi-connection-contract.md)，本页仅记录已接入批次与未完成项，不替代用户操作手册或发布结论。

当前已恢复；视觉公共基线、目录整理与多连接工作区细化及分层诊断入口已通过本地验证。最新前端68文件617项及类型/构建通过；C3关键浏览器链、历史来源过滤、保护管理和MC6仍待完成。用户要求功能优先，不重复各业务功能的视口/主题全组合；第7节595项是诊断接入前历史证据。

最新排期：用户随后要求视觉调整前置为恢复后的下一步；先落实[视觉v2公共基线与代表页](../ui-refresh/ui-v2-implementation-plan.md)，再推广现有页面，后续诊断、历史及保护管理等直接按新规范实现。此前“完成MC5/MC6再做视觉”的安排不再执行；C3关键链待验事实保持，不作为此次纯文档调整已完成。

## 1. 设置批次

| 所有者 | 当前职责 |
| --- | --- |
| CloudDriveSettingsPanel | 成功读取旧数据库快照时维持原单配置表单；只有完整多连接模式描述才挂新管理器。加载错误不推断模式或回退 |
| CloudConnectionManager | 本地分页/名称筛选，明确选择管理对象；新增、名称、安全配置、启禁用和归档分别维护 |
| CloudDirectoryManager | 当前连接目录分页；新增根/映射，独立名称和动作政策，生命周期修订维护 |
| CloudConnectionGrantEditor | 选择本地用户，明确连接使用权和目录ID白名单；翻页保留勾选，不授予未来目录 |
| CloudGlobalPolicyPanel | 全实例唯一请求间隔与失败冷却，单独修订保存；不按连接拆分额度 |
| useCloudSettingsScope | 用户/权限/会话/请求代次校验，隐藏与卸载清理草稿；迟到确认不能继续请求，本地白名单错误反馈 |

新连接和目录的禁用默认值由后端确定，页面不自动启用。批准根及映射不原地重绑；变更时另建目录配置。新建请求具有独立UUID，结果未知后不能直接重发，须先刷新核对本地状态。配置保存、刷新、切换与授权仅访问MediaAI数据库，不诊断、不盘点、不读取云端正文。

模式判定没有新增迁移按钮。正式运行库尚未迁移时仍显示旧设置，不据此宣称多连接已经部署；离线迁移及运行链验收属于MC6。

## 2. 接口与调用

设置接口均要求settings:write，使用既有带会话隔离的apiClient。浏览器不保存CD2账号或Token；公开CA只在当前安全配置编辑草稿中维护。服务端接口结构、容量及错误边界见[接口契约](multi-connection-contract.md#6-接口定义)。

| 操作 | 接口与关键参数 | 调用适配器 |
| --- | --- | --- |
| 列表/详情 | GET /api/settings/cloud-connections；page/pageSize/search；详情路径含connectionId | fetchCloudConnections / fetchCloudConnection |
| 新增连接 | POST同列表；requestId/name/endpoint/caCertificatePem | createCloudConnection |
| 名称 | PUT /api/settings/cloud-connections/{id}/metadata；name/expectedMetadataRevision | renameCloudConnection |
| 更新证书 | PUT 同连接 /security；保留原 endpoint，提交 caCertificatePem 和 expectedSecurityRevision。不同地址需新增连接。 | updateCloudConnectionSecurity |
| 生命周期 | PUT同连接/state；state/expectedStateRevision | setCloudConnectionState |
| 目录 | GET/POST同连接/directories；新建含requestId/name/approvedRoot/mappingKey/quarantineFolderName | fetchCloudDirectoryProfiles / createCloudDirectoryProfile |
| 目录名称/政策/状态 | PUT同连接/directories/{profileId}/metadata、policy、state；分别使用各自原修订 | renameCloudDirectoryProfile / updateCloudDirectoryPolicy / setCloudDirectoryState |
| 用户授权 | GET/PUT同连接/user-grants/{userId}；allowed/directoryProfileIds/expectedGrantRevision | fetchCloudUserGrant / saveCloudUserGrant |
| 全局政策 | GET/PUT /api/settings/cloud-drive-policy；minIntervalSeconds/failureCooldownSeconds/expectedRevision | fetchCloudGlobalPolicy / saveCloudGlobalPolicy |

调用示例只修改本地名称，不连接CD2；与组件一样先保留原名称修订，冲突由调用者显示并要求重新加载，不自动重试：

```ts
import { fetchCloudConnection, renameCloudConnection } from "./api/cloudConnections";

export async function saveConnectionName(connectionId: string, name: string) {
  const original = await fetchCloudConnection(connectionId);
  return renameCloudConnection(original.id, name, original.metadataRevision);
}
```

## 3. 本地验证

- 157前端源/配置文件冻结，65文件539项单测通过，类型和生产构建通过，现有大包警告保留。覆盖模式分支、新增清空、独立CAS、跨页授权、未知保存、撤权、换用户、隐藏与迟到响应。
- 冻结浏览器6视口×六主题亮暗×5设置表面共360场景，以及名称和用户授权两次合成保存通过。API和外站请求均拦截，不接真实库或云端。
- 390px弹窗越界的失败及修复过程、补充视口结果按[本日工作记录](../../work-logs/progress-2026-10-27-resume-ui-mc5.md)留证。CSS视口模拟不等于真实Windows缩放或完整无障碍认证。

## 4. 后续批次

1. 任务分页明确选择连接/目录/子路径，不按个人偏好隐式选中；切换清除Token与旧政策。
2. 本地政策、准备、小型回执与202受理分别校验；未知只查原UUID，关闭不推断停止。
3. 分层显式诊断、历史来源筛选、C2/C3原上下文和保护摘要接入；未绑定旧历史维持只读。
4. MC6隔离迁移、整链与文档验收，再按已确认视觉v2推进代表页和共享层。

上述后续未在设置批次交付，不复用历史真实云一次授权，不迁移运行库、升级NAS、提交或发布。

## 5. 选源与受理增量

2026-10-27后续批次已接CloudSourcePicker和InventoryStartDialog多连接分支：授权分页但不选默认；范围切换先废止旧票据/政策/Token；本地政策校验六项修订；原UUID先落URL，再本地prepare、一次HTTPS交接和原任务本地查询。编号出现后选择器保持挂载，未知状态只隐藏并禁止编辑，避免卸载废止正在交接的意图。

前端159文件冻结，完整558项及类型/构建通过；完整8视口192场景及正常/未知/错误回执三个功能链已通过。此批只接盘点启动，未完成本页第4节其余批次；原设置157文件539项为历史批次证据，不覆盖新源码。

## 6. C2原来源增量

CloudTaskBinding严格区分旧单配置、已绑定来源和未绑定旧历史。绑定字段仅部分存在时拒绝执行；派生动作的context与父计划不同，只核对原连接、目录和路径的对应关系，不能用当前下拉框补来源。摘要展示原名称、当前配置名和原任务路径。

DirectoryExecutionDialog与DirectoryRecoveryDialog对已绑定计划使用显式原来源政策/受理适配器，再按本轮原UUID读取完整状态并核对回执context；小回执不是执行或恢复完成。原来源不符、未绑定历史、导航失败、隐藏/撤权及未知结果仍保持保护，未放宽维护窗口确认。

浏览器发现“结束核对”清理不存在的恢复编号仍导航的问题，已按独立红灯复现修复：四类编号同值或本已为空时不导航，真正变化仍拒绝导航失败，其他任务参数保持。

修复后前端162文件最终冻结、完整585项与类型/构建通过；执行96场景及日志/停止/本地提交/结束核对/未知不重交接，恢复与历史/反向288场景均已通过，覆盖八种CSS视口及六主题亮暗。最初失败、修订快照与最终结果按[工作记录](../../work-logs/progress-2026-10-27-resume-ui-mc5.md)分开保存。C3仅补绑定类型，组件接续中，不能套用本批结果。

## 7. C3增量与恢复入口

后续C3组件已接原来源政策、move/observation的小型受理回执及原UUID完整状态核对；历史先读原计划再读其政策，仅run编号恢复通过原planId补齐原计划，不使用新任务选源。未知不重复交接Token，旧未绑定历史只读，部分绑定字段缺失拒绝回退；源变化废止未交接草稿但不替换已有任务来源。

最新67文件595项（12.45秒）及类型/构建通过，500kB包告警保留；本批浏览器关键链尚待验证，不宣称C3交付或真实云通过。下一步仅补正常隔离、恢复、冲突、未知结果关键链，再推进显式分层诊断、历史过滤和保护管理；不重复C2的384个显示组合。2026-10-27按用户要求暂停，待恢复指令。

## 8. 新视觉工作区与分层诊断

目录整理将目录命名和广告复核分组，维护动作仍绑定原任务；摘要展示原来源及现名。多连接目录/授权移入独立抽屉，关闭保留原草稿确认、子表忙碌和会话隔离。对应85项定向与6个关键合成浏览器场景已过，未重做全部显示组合。

新CloudDiagnosticDialog位于目录整理的“诊断连接”；已持有编号时入口改为“核对原诊断”。它只接多连接接口，未迁移库显示原因，不借旧配置回退。阶段分别为TLS与受限Token/精确版本；两者独立确认、独立UUID，不随打开或保存自动执行。已授权disabled来源可用于显式诊断，普通盘点仍只允许enabled，archived均拒绝。

| 操作 | 接口及约束 | 页面调用 |
| --- | --- | --- |
| 本地政策 | GET /api/cloud-drive/connection-diagnostic-policy；connectionId/directoryProfileId/selectedPath/stage；校验六修订及固定预算 | fetchCloudDiagnosticPolicy |
| 一次交接 | POST /api/cloud-drive/connection-diagnostics；原requestId、固定来源、policyHash、六expected修订、stage、confirmNetwork；仅token-runtime带apiToken；可信HTTPS | submitCloudDiagnostic |
| 原结果 | GET /api/cloud-drive/connection-diagnostics/{id} 或 /diagnostic-requests/{requestId}；只读本地，核对原来源/context/stage | fetchCloudDiagnostic / findCloudDiagnosticRequest |
| 停止 | POST /api/cloud-drive/connection-diagnostics/{id}/cancel；只请求停止，不释放未知或占用 | cancelCloudDiagnostic |

读取本地政策的调用示例，不执行诊断、不携带秘密：

```ts
import { fetchCloudDiagnosticPolicy, type CloudDiagnosticStage } from "./api/cloudDiagnostics";
import type { CloudSourceTicket } from "./utils/cloudSourceDraft";

export async function readDiagnosticPolicy(ticket: CloudSourceTicket, stage: CloudDiagnosticStage) {
  const { connectionId, directoryProfileId, selectedPath } = ticket.source;
  return fetchCloudDiagnosticPolicy(connectionId, directoryProfileId, selectedPath, stage);
}
```

POST小回执不表示通过；TLS、Token、双Build结果分别展示。运行中每1.5秒只读原UUID，最多20次；未知、读取失败、完成、隐藏或撤权停止自动刷新，后续手动核对，不重交Token。终态无未决/占用才可清本地标记；Token只在当前交接内存，关闭等场景清引用，不承诺物理内存清零。

最新68文件617项、类型/构建及4个合成诊断场景通过；具体失败和修复过程见[检查点19](../../work-logs/progress-2026-10-27-resume-ui-mc5.md#检查点-19分层显式诊断入口本地验证通过)。不等于真实云、运行库迁移或正式部署通过，也不代表历史来源过滤与最小保护入口已接。

## 9. 历史与本地读取边界

此后已接盘点、目录改名、广告复核、隔离四类本地历史筛选及最小保护入口；历史筛选不改已打开任务的来源。C3隔离、反向新计划、冲突、未知原编号与核对后本地提交的五个合成浏览器场景通过，关联批次前端640项通过。最小保护入口仍独立于业务记录查看权限。

2026-11-21补齐盘点摘要、建议、覆盖明细和隔离计划／历史在撤权、换用户、会话失效或页面隐藏后的前端清理。过期代次不应用响应、不继续下一段读取；当前来源返回403或404时清除旧数据。回到页面后按原编号手动读取，未提交的确认不继续，URL中的原请求编号、服务端动作、预算及未知保护不变。沿用既有API和授权，不迁移运行库，不访问真实CD2。验证见[续作记录](../../work-logs/progress-2026-11-21-resume-inventory.md#7-mc5读取边界与多语言接续)。
