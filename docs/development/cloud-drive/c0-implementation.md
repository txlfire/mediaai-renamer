# CD2 C0 基础实现与接入门禁

类型：实施与演进。更新：2026-10-16。依据：[范围评估](cd2-115-assessment.md)。

本文记录首批基础实现；后续持久化预算与schema40进度见[预算实施](c0-budget-implementation.md)，schema41内部盘点见[C1实施](c1-inventory-implementation.md)。首批“未交付预算/队列”是当时范围，不代表后续源码仍未实现；真实扫描入口仍未开放。

## 1. 本批次边界

本批次为未发布的后端基础，不是完整C0/C1交付。包含离线分类扫描建议API、内部只读gRPC连接器和合成TLS测试。没有页面入口、真实目录盘点或云端写操作；v1.3.0 / schema39不变，不迁移数据库、不重启开发服务。

| 项目 | 当前行为 |
| --- | --- |
| 离线建议 | 配置限定根后可通过已认证API调用；不探测CD2、不读取云文件、不创建扫描任务 |
| 内部连接器 | 仅单目录有界读取和令牌/版本自检；没有HTTP/CLI调用入口，尚未接入运行队列 |
| 真实接入 | 首批不含持久化预算；后续已进入独立预算实施，真实批准范围、独立任务队列及受限令牌仍是接入门禁；不得用无操作准入上下文挂接生产 |
| 目录改名/广告处理 | 仍未实现，不接受主账号密码，不允许下载、移动或删除 |

原扫描器不适合直接用于此场景：已有全量遍历、全库暂存和missing判定不具备新盘点所需的范围及部分完成语义。后续独立实现，不能仅添加CD2来源类型就复用全流程。

## 2. 离线规划配置与契约

静态配置`[cloud_drive].approved_root`默认为空；不接收账号、密码、令牌或端点，修改后需规范重启生效。仅允许CD2格式绝对路径，至少两个目录段，例如`/cloud/movies`；实际授权范围后续还须与令牌根及真实云盘映射核对，不能只靠目录深度证明。

拒绝空中间目录段、`.`/`..`、反斜杠、控制字符及可隐藏分隔符/穿越的百分号编码。尾斜杠统一去除，大小写及Unicode组成形式保持原样，不借用Windows路径比较。所有选择和排除按目录段检查，不让`/cloud/movies2`通过`/cloud/movies`的范围校验。

接口均要求应用登录和`scan:run`权限，不使用CD2令牌作为应用会话。

### GET /api/cloud-drive/status

返回：`planningAvailable: boolean`、`approvedRoot: string|null`、`executionAvailable: false`、`writeAvailable: false`、`networkAccess: false`。读取配置不等于连接测试。未配置时仍返回200，`planningAvailable=false`。

### POST /api/cloud-drive/scan-advice

请求：

```json
{
  "selectedPaths": ["/cloud/movies/western", "/cloud/movies/western/2025"],
  "excludedPaths": ["/cloud/movies/western/private"],
  "mode": "subtrees"
}
```

`selectedPaths`必填1至100项，`excludedPaths`默认空且最多100项；每项字符串最多4096字符，范围校验另限UTF-8路径4096字节。`mode`为`overview`（默认，只看一层）或`subtrees`（所选子树）；拒绝额外字段和非字符串路径。

返回200，字段：

| 字段 | 类型及语义 |
| --- | --- |
| status各字段 | 同上；始终不开放执行或写入 |
| mode | 请求的规划方式，不自动升级为递归 |
| scopes | `{path, recursive, includeDirectFiles}`数组；保留所选父目录直接文件，不只处理子目录 |
| excludedPaths | 合并后的有效排除目录；必须与所选范围相交 |
| omittedSelections | `{path, reason}`数组；reason为`excluded`或`coveredByParent` |
| estimatedEntries / estimatedDirectories | 均为null，未联网、未计数 |
| suggestedBudget | 单RPC、最小间隔2秒；每量子200目录/10000条目/900秒，内容0字节；只是候选预算，不是已执行限流或115承诺 |
| recommendations | `{code,message}`数组，提示先浅概览、分类串行、当前未执行和总预算约束 |

上述请求得到一个`/cloud/movies/western`递归scope，`2025`标记`coveredByParent`，保留`private`排除。浅概览时不合并父子选择，因为父目录的一层列表不覆盖子目录自身的一层内容。

错误：未登录401、缺权限403；未配置规划根409；越界/无有效范围/无关排除400；结构或数量错误422。错误不回显路径、端点或输入秘密。所有结果只作为建议，没有任务ID、可执行授权或云端副作用。

PowerShell调用示例（仅本机应用API；环境变量须由已有登录流程提供）：

```powershell
$headers = @{ Authorization = "Bearer $env:MEDIAAI_SESSION_TOKEN" }
$body = '{"selectedPaths":["/cloud/movies/western"],"excludedPaths":[],"mode":"subtrees"}'
Invoke-RestMethod -Uri 'http://127.0.0.1:8970/api/cloud-drive/scan-advice' `
  -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

预期：配置匹配且有权限时返回建议；没有配置则409而不是启动扫描。不要在聊天、共享脚本或文档里填写真实会话令牌。本批次未修改运行实例配置，默认访问会显示未启用。

## 3. 内部连接器约束

模块：`backend/app/service/cd2_metadata_client.py`。调用必须提供`admission(deadline)`上下文；没有默认放行器。后续预算实施要求上下文返回RPC permit，发起/完整成功/截断分别显式记录，见预算文档。构造仅接收HTTPS origin、内存中的受限令牌、批准根、准入控制及可选受信CA，不接受密码、URL内凭据、关闭证书验证或TLS主机名覆盖。

准入等待计入同一次操作总截止时间（默认15秒，允许0.05至60秒）。明确关闭客户端重试和环境HTTP代理；不自行重试认证、超时、断流或未知失败。不能据此承诺CD2内部或115侧没有重试。RPC只有以下三项，不提供通用RPC执行器：

| RPC | 前提和输出 |
| --- | --- |
| GetApiTokenInfo | 公共方法不附加Authorization，令牌放在TLS请求的StringValue中；仅验证结果，不输出原始TokenInfo |
| GetRuntimeInfo | 仅当令牌有运行信息权限才调用；仅输出有界版本字符，不输出OS信息 |
| GetSubFiles | 先重新验证令牌；传输路径相对已核对的Token根，只读单目录，不递归，不取详情、图片、直链或文件正文 |

仅接受`allow_list=true`，另可有`allow_view_runtime_info`；其他41项已知权限均拒绝，包含下载权限`allow_read`。未知权限字段也拒绝。令牌根须与批准根精确匹配，禁止用全账号令牌再声称应用自限等价于受限令牌。

目录读取默认最多保存1000条、接收8MiB序列化元数据/128消息；可调硬上限为10000条、32MiB/1024消息。gRPC另限单消息默认1MiB、最多4MiB，避免解析一个无限大的消息。总超时包含令牌自检与列表。每次接收的完整消息可能超过剩余字节/条目预算，硬内存边界还取决于单消息上限，不能声称接收量精确等于保存量或115流量。

这是C0小范围探测用的有界收集器，不是C1大目录完整盘点实现。超过10000条的单目录会保持部分完成；后续需增加有界流式持久化和批准总预算，不能反复读取同一前缀来冒充游标续扫。

`forceRefresh=false`，optional `checkExpires=false`保留presence。缓存不能保证不访问上游或观察最新，输出freshness固定unknown。返回路径必须是当前目录的直接子项且在根内；拒绝搜索结果、本地对象、根、压缩包目录、异常类型/大小及重复路径，不跟随它们继续遍历。

达到数量、字节或消息限制时返回`complete=false`及明确stopReason；恰好达到限制也保守判为部分完成，不额外探测结束。每条本地校验前后及流结束时核对总截止时间，正常且未超时的流结束才标完整。异常以固定错误码抛出，保留有界partialObservation且不标完整；不回显服务端details。调用结束/截断先取消传输流；**取消不证明CD2/115上游工作已经停止**，后续账号调度必须按未决/暂停处理，不能凭取消立即继续下一分类。

## 4. 协议与依赖依据

依据[CD2官方gRPC指南](https://www.clouddrive2.com/api/CloudDrive2_gRPC_API_Guide.html)和[官方proto](https://www.clouddrive2.com/api/clouddrive.proto)，2026-10-16核对的proto SHA256为`a00b4620cbce0a9bd6009f8e771ba68ea7375080ca7f15f4c7c206310ba2ec9d`。文档版本不是用户服务端实测版本。

`cd2_metadata_protocol.py`用标准protobuf descriptor/message_factory声明消息投影，只包含所需字段，不引入完整写入SDK、不手写二进制编解码。测试验证optional字段presence及未知权限保留；实际CD2若使用不同根映射或版本不兼容，保持失败，不能猜路径降级。

2026-10-17真实最小权限自检发现认证契约差异：官方proto将`GetApiTokenInfo`列为无需Authorization的公共方法，附加受限Bearer时该实例会返回PERMISSION_DENIED。连接器仅对该方法不发送Authorization，令牌仍通过受信TLS请求体交给服务端核对，并执行原有根/权限/未知字段验证；运行信息和列目录继续携带受限Bearer。没有失败后尝试主账号或匿名列目录的降级分支，不扩大Token权限。回归服务按公共方法无认证、授权方法保留Bearer分别断言，避免合成fixture重复同一错误假设。实际续验进度见[10月17日记录](../../work-logs/progress-2026-10-17-cd2-acceptance.md)。

同日实际公共自检修复验证通过后，运行信息返回值因Build展示格式被旧校验拒绝。版本字符串按最多64字符的受限ASCII展示校验，允许Build日期/时间所需空格和冒号，不将整串限定为语义化版本号；不允许边缘空白、控制字符、HTML或双向控制字符。真实接入仍须完整自检和预算验收，不因放宽展示字符集而放宽权限、路径或TLS。

同日进一步以精确根受限Token做一次虚拟根诊断：唯一GetSubFiles传输路径为`/`时收到72条、17,902字节，响应路径全部为虚拟根下直接子项，没有返回全局绝对路径。此前把全局批准路径直接传给受限Token，导致真实NOT_FOUND；更换根目录名不足以修复这一命名空间差异。

适配器现保持对外输入、批准、准入和本地观察为全局绝对路径：先校验请求在精确批准根内，再将根自身转换为`/`、子目录转换为去除该根前缀后的绝对虚拟路径。响应先按原规则校验虚拟路径，再拼接批准根并重新校验长度及规范性，且必须是当前请求目录的直接子项。不猜测两种路径格式、不接受全局路径作为另一种降级、不放行`..`、双斜杠或编码分隔符。公开API、数据库结构、原批准及旧会话不变；仍需真实worker落库与完整性验收，诊断元数据不冒充盘点完成。

后续最终验收已完成：修复后的正常独立worker按同一精确根、本层overview及4RPC/30秒预算，接收/保存72条、17,902字节接收元数据，本层完整；Token撤销核对、RPC租约/worker槽为0。子目录内容未读取，网页执行入口未开放。最新两套Python全量各982项通过（970通过、12受控跳过），完整证据与边界见当日工作记录。

新增固定依赖[grpcio 1.84.0](https://pypi.org/project/grpcio/1.84.0/)（Apache-2.0）和[protobuf 7.36.2](https://pypi.org/project/protobuf/7.36.2/)（BSD-3-Clause）；两者支持Python>=3.10并提供Windows/Linux所需wheel。通过既有uv命令更新哈希锁，无其他包版本变动。不引入grpcio-tools运行依赖或UI依赖。

## 5. 下一批实施顺序

1. 冻结独立盘点的持久化任务、账号组、预算/冷却、准入租约及暂停恢复模型；同一实例跨进程共享，网络操作不持有数据库事务。另做schema迁移与兼容验证，不复用仅针对AI/元数据HTTP的预算表。
2. 实现固定批准范围及总预算、串行分类队列、目录完成边界断点、部分目录重读去重、根直接文件、排除及缓存容量。没有公开条目游标，不能伪造按条精确续扫。
3. 有界候选规则、保留例外、本地分页/导出、进度和日志；离线分析不重新连接CD2。
4. 接入页面前确认UI Skill，按现有Vue3/Element Plus与六主题实现，不添加无授权UI依赖。
5. 真实115验证须先确定专用小目录、TLS/CA与受限令牌、现有同步/备份影响。分级扩大，不扫描账号根，不做真实全库压力测试。

验证结果及未完成项见[本批次工作记录](../../work-logs/progress-2026-10-16-cd2-c0-foundation.md)。C2目录改名、C3取样/隔离/删除仍需独立门禁，不随只读实现自动开放。
