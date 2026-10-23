# CD2 只读接入准备

类型：操作手册（接入与升级准备）。核对日期：2026-10-21。适用未发布C0-C3开发源码schema55；不是稳定版v1.3.0/schema39已交付部署功能。下文C1试读的schema42和72条结果仅为历史证据，不是当前迁移目标。公司CD2/CLOUDAPI1.1.2及家庭精确1.1.3的小夹具C2/C3产品链、家庭兼容本地回归已验；运行中NAS候选未部署家庭兼容增量，正式媒体写入仍关闭。实际结果见[C4报告](../development/cloud-drive/c4-acceptance-report.md)和[版本核对](../development/cloud-drive/cd2-runtime-compatibility.md)。

历史C1接入曾经用户专项授权，通过NAS本机SSH维护证书和正常重启；该历史授权不转为后续自动维护权限。原始范围与证据见[原实机验收](../work-logs/progress-2026-10-17-cd2-acceptance.md)及[入口验收记录](../work-logs/progress-2026-10-17-cd2-review-fixes.md)。

已有地址和主账号信息不需重复提供。此前Node24对HTTP同端口使用HTTPS得到EPROTO，不能只改URL协议。后续独立HTTPS端口已配置并通过专用证书信任验证，不使用跳过验证或明文主账号登录兜底；换主机、端点、证书或部署环境时需重新核对。

## 1. 准备信息

先记录以下信息，不需要提供主账号密码：

| 项目 | 预期结果 | 未满足时 |
| --- | --- | --- |
| CD2 实际版本、安装方式及 MediaAI 部署位置 | 确定同机/容器/跨主机边界，确认原生 gRPC 可达性 | 不凭 HTTP 管理页可达判为接口通过 |
| 专用试读目录 | 用户明确批准的小范围媒体子目录，不是云盘根或整个电影分类 | 先选样本，不为估算数量递归枚举 |
| HTTPS 主机名、证书链和 CA 来源 | 主机名与证书匹配，客户端能验证链 | 不使用跳过验证、主机名覆盖或主账号 JWT 兜底 |
| 仅列目录的 API Token | 用户在本机受控界面提供，不进聊天、Git、文档和进程命令行 | 未有受限令牌则保持关闭 |
| 同步、备份和媒体服务器任务 | 明确试读窗口及其他客户端可能叠加的流量 | 不自动修改或停用其他服务 |

## 2. 安全通道

CD2 官方帮助说明：核心服务 HTTP 默认端口为 `19798`，HTTPS 默认端口为 `19799`，后者默认关闭，需在设置中启用并配置证书。这是官方默认值，不代表已安装实例的实际配置。[官方帮助](https://www.clouddrive2.com/en/help.html)

2026-10-16 初次对已有主机的官方 HTTPS 默认端口做无认证 TLS 连接检查时返回 `ECONNREFUSED`，未关闭证书校验、未重试或跟随跳转。该历史结果仅说明当时入口拒绝连接，不能据此推断未启用原因；后续配置与重启后的结果见下文。

后续用户生成自签名证书后，端口已能响应TLS。自动生成证书只含回环地址，不能用于NAS局域网IP；已在NAS本机另行生成包含实际IP的新证书，并通过既有SSH主机密钥核对、公开证书指纹和文件权限检查。证书路径通过官方`SetWebServerConfig`更新，原配置有受限备份，未改端口和其他设置。实测该实例返回成功后仍提供旧证书，单独关闭/开启HTTPS配置也未停止实际监听，不能把接口成功当成证书加载成功。

用户随后批准短暂重启CD2。核对官方`GetServiceCapabilities`可重启、`GetAllTasksCount`上传/下载/复制均0后，仅发送一次`RestartService`；约2.06秒后新进程接管，应用监护父进程保留，未调用fnOS脚本的强杀分支、重启NAS或应用中心。NAS回环和当前Windows机器均以专用公开证书为信任、保持IP/有效期校验，严格TLS通过、指纹一致、ALPN为`h2`。独立原生gRPC无凭据`GetRuntimeInfo`返回`UNAUTHENTICATED`，证明协议入口可达且认证生效，不等于令牌或目录验收通过。挂载记录数恢复至重启前数量，但未读取挂载目录，不把此项当成115文件可用性验证。

该fnOS实例的SSH策略拒绝TCP端口转发（`administratively prohibited`）。未修改SSH策略、建立替代隧道或对外暴露管理端口；维护直接通过获授权的SSH会话在NAS本机调用回环管理接口，管理凭据只在内存处理。原生HTTP2/gRPC入口已验证，受限Token仍需单独核对；浏览器未配置该公开证书信任时仍可能提示证书不受信任，不以网页警告要求关闭应用校验。

CD2 官方指南区分原生 HTTP/2 gRPC 与浏览器 gRPC-Web；能打开 Web 管理页不能证明本项目使用的原生协议可用。[官方指南](https://www.clouddrive2.com/api/CloudDrive2_gRPC_API_Guide.html)

- 本项目端点只接受 HTTPS origin，不带路径、查询串或账号信息。使用IP时证书SAN须包含该IP；使用DNS名称时须匹配SAN且从MediaAI所在环境正确解析。
- 反向代理须支持原生 gRPC、HTTP/2 和流式响应，保留认证元数据；普通 HTTP `proxy_pass` 或只支持 gRPC-Web 的配置不能直接视为兼容。
- 若使用 Nginx，应核对 gRPC 模块及 HTTP/2 模块，显式关闭上游自动切换/重试。若代理到 TLS 上游，还需显式开启上游证书校验并配置可信 CA；不要把默认行为当成已验证。代理读超时是相邻读取间隔，并非整个流的总期限。[Nginx gRPC 模块](https://nginx.org/en/docs/http/ngx_http_grpc_module.html)
- 全链路和实际端点须验证；同机受控网络内部是否允许 h2c 是单独部署决策，不默认把令牌发到现有跨主机 HTTP 端点。上述证书维护和重启具有用户专项授权，不扩大为自动修改fnOS、防火墙、DNS或其他应用的权限。
- MediaAI 仅接收用于验证的公开 CA/证书内容，不需要服务器私钥。代理、CD2 和诊断日志均不得记录 Authorization、TokenInfo 正文或 TLS 会话密钥。

预期：客户端验证证书成功，ALPN/HTTP2 与原生 gRPC 转发都通过。常见错误：证书绑定另一个主机名、容器无法解析名称、只转发了网页、代理重试隐藏真实请求数。遇错先停在通道检查，不循环调用云目录来试错。

## 3. 创建最小权限令牌

由用户在 CD2 界面创建应用 API Token，设置明确根目录、权限和有限期限；具体按钮名称以实际版本为准。官方指南中的通用“只读令牌”示例还包含读取和搜索权限，不能直接套用到本项目。[官方令牌说明](https://www.clouddrive2.com/api/CloudDrive2_gRPC_API_Guide_zh-CN.md)

本项目接受条件：

- `allow_list=true`；可选 `allow_view_runtime_info=true`，用于自检版本。
- `allow_read`、`allow_search` 及所有创建、写入、改名、移动、删除、云账号/系统管理等其他权限均为 false。未知权限字段也拒绝，不把“看起来只读”当成通过。
- Token 的 `rootDir` 必须与批准会话根精确一致。以 CD2 返回的规范路径为准，不猜测挂载盘符或 `/115` 别名；应用配置允许更大的媒体根不等于令牌可以越权。
- 使用有限期限并按用户计划撤销；不为了测试长期保存主账号 JWT。过期或权限不符合时停止，不自动登录、更换接口或扩大权限。

预期：内部自检返回受限令牌验证成功；有版本权限时返回有界版本字符串。失败原因码如 `token_root_mismatch`、`overprivileged_or_missing_list_permission`、`unknown_token_permissions` 必须核对后处理，不能改成默认放行。

实际1.1.2兼容边界：`GetApiTokenInfo`为公共方法，不附加Authorization；Token仅放在该方法的TLS请求正文中，不记录正文。运行信息及列目录仍必须携带受限Bearer令牌，不能一并去掉认证头。版本展示接受长度1..64的受限ASCII及Build日期/时间，拒绝控制字符、HTML和边缘空白；版本文本不作为命令或权限依据。

同实例受限Token使用虚拟根命名空间：根已精确绑定媒体子目录时，列该根的传输路径为`/`，不是再次提交全局完整路径。应用先核对Token根与批准根精确一致，再在适配器边界转换请求和响应；批准、预算、任务及本地结果仍使用全局绝对路径。这不等于创建全局根Token，也不允许用户把批准根配置为`/`。当前最终独立worker验收接收并保存72个直接子目录、本层完整；这些子目录的内容没有读取，不以此证明子树完整。

## 4. 本地配置与启动前检查

管理员使用“系统设置 → 目录整理（CD2 / 115）”维护端点、批准根、公开CA、间隔、冷却与独立开关，配置保存于数据库。完整操作及字段要求见[配置手册](../manuals/cd2-settings.md)，事务和worker修订关系见[配置设计](../design/cd2-database-settings.md)。

当前开发结构为schema55；[cloud_drive]文件配置不再读取，不自动导入或启用。升级前先在旧版本解决待执行任务和未知保护、确认worker实际退出，再停服备份数据库及匹配的凭据密钥；存在未决状态时不要先升级。迁移保留原保护，新配置表默认关闭；不能以首次保存绕过未知结果。配置保存不连接云端，无需重启生效，但不证明TLS/HTTP2或版本已验。

网页“目录整理”可观察已批准任务、分析本地记录、取消与清理；“新建盘点”受默认关闭的C1-2策略限制。内部 worker 使用匿名 stdin 临时交接参数，不保存云凭据；没有允许用户自行用无操作准入器绕过预算的支持用法。

### 4.1 网页入口操作

1. 管理员先核对应用本身的可信HTTPS及CD2的TLS/HTTP2。应用与CD2是两段独立信任；浏览器须显示HTTPS，反向代理须限定可信来源，不能信任任意转发头。普通HTTP只能查看本地观察，不显示Token输入框。不要为开发验收关闭证书校验。
2. 在已备份的目标实例通过系统设置维护限定媒体根、固定HTTPS端点及公开CA，再明确开启“启用网页本层盘点”并保存，无需重启。预期：具有`scan:run`的用户打开“目录整理”→“新建盘点”，能看到批准根和固定预算。策略可用仅表示本机配置可用，不证明CD2可达或旧租约已解除；未满足时按提示核对，不重复试读。
3. 用户在CD2获取精确绑定本次选定目录、仅`allow_list`（可选版本自检）的短期Token。仅在HTTPS密码框输入，不使用主账号密码，不写入文件或聊天。填写批准根内的完整目录，核对4RPC/30秒/100条/1MiB、本层不递归后明确确认。
4. 点击“确认并开始”后，页面先保存原请求编号，再准备无秘密任务，最后仅一次交接Token。预期：任务受理后返回工作台，约1.5秒读取本地摘要，Token输入清空；completed/scopeComplete只证明本次本层完整，不证明孩子目录或云端新鲜度。
5. 超时、断线或刷新后，通过“核对原请求”显式查询。queued任务需先在工作台取消，不自动继续启动；404只表示当前用户未查到本地意图，不证明没有远端执行。核对后才能确认移除标记，此操作不撤销Token/取消任务。不要手工清URL后重复操作来试错。
6. 每次结束在CD2撤销临时Token并核对不存在；网页不自动管理CD2 Token。失败或有未决租约时停止，保留预算/审计，按既有核对流程处理。关闭/隐藏页面会清除未交接Token；已发请求无法凭关闭页面撤回，仍须核对原任务。

本轮页面在264个合成布局场景及功能流程中通过；另以严格信任的临时应用HTTPS和真实产品接口→worker验收一次，本层72条、2次应用RPC、临时Token撤销核对，槽位/租约0。正式部署/可信代理、真实浏览器访问正式环境与实际系统缩放仍未验，不直接把临时监听配置推广至正式服务。

不要把 CD2 挂载目录作为普通本地/SMB/NFS 来源来绕过试读门禁。原扫描器的行为未在本批改造，不自动获得新的云盘预算、范围和部分完成语义；尤其不能把整盘挂载当作一次普通全量扫描。

## 5. 小范围放行与证据

通道、令牌及具体试读目录均确认后，才进行独立受控试读。原指定目录的最终worker已completed/scope_complete，接收/保存72条，complete1，worker槽/RPC租约均0；临时Token均已撤销并确认不存在，原批准会话4RPC耗尽。后续C1-2另获一次批准，新会话使用2/4RPC、同样72条本层完整；Token撤销、旧会话不变，一次性门禁已消耗，即使有剩余额度也不自动继续。此处complete只指选定根本层，不覆盖子目录内容。新实例首轮只批准一个小目录、本层概览，明确总RPC、条目、元数据字节、时间和目录预算，不事先递归统计。

记录：实际 CD2 版本、批准根/排除范围、RPC 已用量、应用接收/保存数量、目录完整性、耗时、停止原因、其他客户端状态及观察新鲜度。令牌自检也计入 RPC；应用 RPC 数不等于 115 侧次数，缓存命中也不能承诺零上游流量。

失败、截断或取消后，未决租约和暂停必须保留，不能凭进程退出或冷却到期重试。按[预算核对](../development/cloud-drive/c0-budget-implementation.md)和[盘点核对](../development/cloud-drive/c1-inventory-implementation.md)分开处理；本机核对不代替远端状态确认。

固定标准gRPC错误码保留到预算、任务和worker日志，页面显示中文原因；远端details和凭据不入日志。历史请求若因旧白名单丢失具体码，应标为未知，不凭新合成测试猜测原始原因。服务重启具有副作用，必须另获用户授权，并先检查上传/下载/复制任务；不能把正常重启作为自动重试机制。

通过只读试验仍不开放改名、隔离或删除。C2/C3当前受控实现及公司小夹具证据不消除外部并发替换竞态；维护窗口、单写者、前后身份核对、未知不重试仍是必要边界，不能宣称原子身份保证。试读只影响观察/缓存，不以清空CD2缓存或删除云文件来“还原”；任何正式媒体写及反向新计划均须另行授权，正文取样和永久删除不开放。

## 6. 正式升级前核对与回退

本节是尚未执行的操作准备，不是正式升级记录。稳定版镜像部署仍按[fnOS手册](fnos-ghcr-docker.md)；该手册的v1.3.0/schema39不能直接改写成未发布schema55镜像已可用。

### 6.1 固定实例和维护窗口

1. 记录实际运行的代码版本或镜像tag/digest、Compose项目、工作目录、配置绝对路径、数据库绝对路径、data/logs挂载及密钥来源。相对数据库路径须按应用工作目录解释，不默认相对于配置文件目录。不能用本机默认开发库代替待升级实例。
2. 暂停接收新的扫描、预览探测、文件写、回滚和外部提交任务；与操作者确认其他客户端不修改目标范围。先等待应用及worker正常结束，再按实例原启动方式停止服务；不强清执行槽，不以页面关闭或PID消失证明远端静止。
3. 按原请求核对未决动作及租约。状态不明、review、保护槽或仍有worker时停止升级准备，保留原库、日志和请求编号，不直接DELETE/UPDATE“修复”。冷却结束不是原请求成功或失败的证明。

预期：确认目标实例及全部执行进程归属，已知文件事实可追溯。常见错误：只停止Web进程而worker仍在运行、混用家庭与公司端点、把新安装的空库当成已有实例。

### 6.2 本机只读核对

已有预算及同步探测核对命令不导入app.main，不自动初始化数据库。使用目标版本的环境，在仓库backend目录执行，配置参数必须指向明确的现存文件：

```powershell
Set-Location D:\project\MediaAI-Rename\backend
..\.venv\Scripts\python.exe -m app.recover_cloud_drive --config D:\path\to\instance\config.toml
..\.venv\Scripts\python.exe -m app.recover_cloud_directory --config D:\path\to\instance\config.toml
```

上述路径是占位示例，先替换，不在正式部署中直接使用开发仓库默认值。不加`--apply`或释放确认参数。上述CLI对应schema55；schema39没有CD2表，schema54没有新配置表，旧库执行预算查询可能报存储不可用；这不是许可初始化旧库，应按其版本检查，不把缺表算为“未决0”。

对已确认的schema54/55库，可使用只读SQLite连接运行以下补充SQL。先核对版本，再单独执行第二段；不能对schema39盲跑新表查询：

```sql
PRAGMA query_only = ON;
SELECT value FROM app_meta WHERE key = 'schema_version';
```

```sql
BEGIN;
SELECT 'inventory_worker' AS kind, EXISTS(
  SELECT 1 FROM cloud_drive_inventory_jobs
  WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('queued','running')) AS requires_review
UNION ALL
SELECT 'directory_execution', EXISTS(SELECT 1 FROM cloud_directory_executions
  WHERE slot_held=1 OR worker_pid IS NOT NULL OR status IN ('accepted','running','review'))
UNION ALL
SELECT 'directory_recovery', EXISTS(SELECT 1 FROM cloud_directory_recoveries
  WHERE slot_held=1 OR worker_pid IS NOT NULL OR status IN ('accepted','running','review'))
UNION ALL
SELECT 'quarantine_execution', EXISTS(SELECT 1 FROM cloud_quarantine_runs
  WHERE slot_held=1 OR worker_pid IS NOT NULL OR status IN ('accepted','running','review'))
UNION ALL
SELECT 'rpc_lease', EXISTS(SELECT 1 FROM cloud_drive_rpc_lease)
UNION ALL
SELECT 'probe_lease', EXISTS(SELECT 1 FROM cloud_directory_probe_leases)
UNION ALL
SELECT 'directory_unknown', EXISTS(SELECT 1 FROM cloud_directory_rpc_intents
  WHERE phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL)
UNION ALL
SELECT 'recovery_unknown', EXISTS(SELECT 1 FROM cloud_directory_recovery_rpcs WHERE phase IN ('sending','unknown'))
UNION ALL
SELECT 'quarantine_unknown', EXISTS(SELECT 1 FROM cloud_quarantine_rpcs
  WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL);
SELECT state, cooldown_until FROM cloud_drive_budget_pool WHERE id=1;
PRAGMA integrity_check(1);
PRAGMA foreign_key_check;
ROLLBACK;
```

项目使用参数化sqlite3，没有ORM；对应的项目服务调用为`cloudBudgetStatus(settings, includeLeaseIdentity=True)`及`inspectDirectoryProbes(settings)`，上面的CLI是其可运行入口，不为手册引入另一持久层。补充SQL只检查CD2本机状态，不覆盖传统文件动作、所有外发任务、远端状态或备份一致性；全部为0也不直接授权升级。缺表/字段、数据库忙或完整性异常均停止，不自动修表。

### 6.3 一致性备份及隔离演练

1. 确认服务和worker停止且没有其他数据库写入者后，备份完整data/logs/config、独立凭据密钥、证书公开CA及旧制品/Compose。若SQLite仍有WAL，必须采用经过验证的一致性备份方式，不能只复制主sqlite文件；不手工删除WAL/SHM。备份放在不公开且权限受限的位置。
2. 保存旧版本、schema、备份文件清单和哈希；密钥只记录来源和受限备份位置，不输出密钥值。`MEDIAAI_CREDENTIAL_KEY`环境值优先于密钥文件，恢复须保持实际生效的匹配值。旧v1/v2密文还依赖原database_path字符串，演练不能随意改变该字符串。
3. 在独立实例和端口用副本演练升级55、重复启动、账户/权限、凭据可读性、任务历史、日志/审计及完整性。三个CD2执行开关均false，旧任务不重放；禁止先导入应用再设置隔离配置。不要把生产媒体的可写挂载带入演练。
4. 以旧制品和升级前成套备份演练恢复；不能用新代码打开旧备份来证明回退，它会再次迁移。已有隔离39→54及旧备份恢复证据只证明测试实例，不替代部署位置和实际凭据恢复验收。

预期：备份能恢复且新旧实例隔离。常见错误：只有数据库无密钥、沿用同一端口/数据挂载、测试发现导入app.main、复制运行中主文件后称“一致性备份”。

### 6.4 正式变更与失败处理

通过准备后仍须另行确定部署窗口及目标制品，不推送未发布标签或自动打开执行开关。正式启动后先验证健康、登录、权限、凭据读取、本地历史和审计，不用正式媒体试写。可信应用HTTPS、CD2精确版本、账号映射修订及最小Token分别复核；新一次真实操作单独确认。

失败时停止新实例，保留失败库与日志。若没有任何远端或文件动作，可按已验证方案恢复旧制品及整套旧备份；若已有动作或结果未知，先核对原意图与对象身份，不能只恢复旧库抹去动作事实。没有通用schema55→39降级SQL，不修改app_meta伪装版本。

### 本次开发验收实例更新

2026-10-22，公司fnOS入口 `https://192.168.50.43:8972/` 已从数据库配置版再次更新至 `1.3.0-c4-review-validation`，schema55，含本轮CR-01至CR-10修复。内部网络副本、最终停服一致性备份、新运行副本HTTPS/鉴权/逐产品文件哈希及数据保持检查通过；用户确认本次原账号登录、系统设置和目录整理页面正常。旧schema55与更早schema54原数据/容器/镜像保留，三个云开关仍关闭。当前TLS仍依赖原专用证书目录，不按旧目录名清理。此项不是公开稳定镜像发布、自启/续期或真实媒体写入验收，回退分支本轮未触发。见[最新更新记录](../work-logs/progress-2026-10-22-fnos-review-upgrade.md)；[此前schema54至55更新](../work-logs/progress-2026-10-21-fnos-db55-upgrade.md)保留历史证据。上文是通用升级准备，下文的schema54候选为历史证据。

## 7. 空实例首次部署与独立验收

本节适用于已确认不继承旧数据的新实例，不代替第6节的升级流程，也不表示正式部署已获准。独立fnOS验收结果见[C4报告](../development/cloud-drive/c4-acceptance-report.md)；临时validation镜像不是稳定标签，不直接接管正式入口。

### 7.1 独立实例核对顺序

1. 核对固定镜像摘要、独立目录及空闲端口；只将白名单源码/编译产物用于构建，不把开发机数据库、运行配置、密钥、日志或媒体带入构建上下文。明确镜像架构、Python版本和依赖检查结果，不能用Windows全量测试冒充Linux全量。
2. 新建data/logs/config及空媒体目录，不复用弃用实例的目录；data/logs按需写，配置与空媒体只读。核对实际挂载，而不仅查看Compose声明；凭据密钥须在后续生成后持久化，不在镜像中预置。
3. 初期只绑定NAS环回，默认管理员自动创建及密码重置关闭，三个CD2执行开关false、端点为空。不在未初始化的HTTP入口开放LAN或公网，不把NAS的127.0.0.1当成Windows的localhost。普通bridge可能提供出站路由，不等于隔离网络；改变网络范围先单独确认。
4. 新制品先核对容器内健康、空库schema55及完整性（既往schema54镜像结果不能代替本轮），再核对宿主有效端口、页面和未登录接口。HostConfig.PortBindings只是配置意图，须同时核对NetworkSettings.Ports与实际NAS回环请求；容器running或内部200不能替代宿主入口通过。
5. 核对返回HTML和静态资源与固定构建哈希一致，受保护接口未登录401，云端配置仍关闭。匿名请求不初始化用户、不提交Token，也不触发盘点。此阶段不需要读取媒体或用主账号登录CD2。

预期：新实例仅在批准的地址运行，构建、持久化和访问边界均有可复核记录。常见错误：只检查容器状态、没有有效端口却公布访问地址、把临时HTTP健康通过写成可信HTTPS/登录通过。

### 7.2 正式入口与初始化

1. 先确定正式访问地址、端口、版本/摘要和HTTPS终止方式。证书须覆盖实际IP或域名；浏览器信任与应用到CD2的CA是两项独立配置，不复用CD2服务器私钥。没有明确授权时不修改宿主防火墙、系统信任或其他应用代理。
2. 保持新实例无用户且不对未受控来源开放管理员初始化入口。管理员通过已验证的可信HTTPS页面自行初始化，使用独立密码；密码不写入文档、命令行、镜像或聊天。不要为验收临时打开admin/123456或隐藏密码重置。
3. 验证登录、退出、直接用户权限、会话及应用审计；项目采用Bearer令牌，普通登录使用sessionStorage，记住登录使用localStorage，须分别核对服务端期限、浏览器保存和退出撤销，不以Cookie设置代替验证。HTTPS反向代理只信任已知代理，不能接受任意客户端伪造的转发头。页面和资源返回200不等于这些交互已验收。
4. 在无云执行、无媒体挂载的条件下验证成套备份及隔离恢复；确认data/config/密钥与目标制品匹配。再由操作者确认是否接管正式入口及日常服务启动策略，不默认把临时容器的restart=no用于正式服务。
5. 正式入口通过后，才分别确认CD2端点/精确构建、批准目录、映射修订、受限Token与操作预算。仅初始化管理员或上线MediaAI不授权开启三个云开关，也不授权正式媒体改名、隔离或删除。

预期：正式HTTPS、初始化、身份和备份逐项通过后再交付访问地址。常见错误：先对LAN暴露空用户库、通过HTTP传管理员密码、自动继承临时Token、把公司1.1.2证据当成家庭1.1.3兼容依据。

### 7.3 失败及保留

记录失败发生在登录前、命令发送前、资源创建后或HTTP检查阶段；未知结果先只读查询，不重放创建、启动或连接网络。需要重新派发时保留旧失败及一次锁，用新授权和独立记录继续。核验脚本停止不代表服务进程已停止，必须实际核对容器状态。

临时验收结束后，保留已批准运行的服务及其可追溯记录，清理由操作者另行确定。仅处理本轮已知完整ID及专用目录，先确认没有业务数据/其他容器引用；不使用全局prune、不删除media或云目录，也不为了磁盘空间清除未决RPC与审计。

### 7.4 专用证书与先初始化方案

当前部署与运维核验已于2026-10-23推进至固定v1.4.0、unless-stopped和一次容器重启，见[最新记录](../work-logs/progress-2026-10-23-fnos-stable.md)；下方初始化顺序与restart=no候选结果保留历史，不表示当前仍为候选。宿主重启及故障回退未验，证书采用人工维护规程而非自动续期。

本次fnOS部署准备已确认采用直接Uvicorn TLS、MediaAI专用私有CA及服务器证书，不复用CD2私钥、不新装反向代理，显式设置`--no-proxy-headers`。Uvicorn原生支持证书/私钥参数；是否启用及是否生效须检查实际容器CLI和TLS，而不是仅凭文档推断。[Uvicorn部署说明](https://www.uvicorn.org/deployment/)

1. 私有CA及服务器私钥只在NAS专用受限目录生成和保留，目录700/私钥600；应用仅只读挂载服务器证书/私钥与公开CA，不挂CA私钥。服务器证书SAN必须覆盖实际入口IP或域名，保持链、用途、有效期及IP校验，不关闭浏览器或客户端验证。
2. 先仅绑定NAS环回HTTPS，检查健康、未登录401、bootstrap可用及空库。首次bootstrap尚未认证，不能先把空用户实例开放到LAN后等待管理员创建。操作人员通过已验证SSH，在容器内使用专用CA校验的HTTPS调用`POST /api/auth/bootstrap-admin`，密码只在交互终端隐藏输入，不进argv、配置或聊天。
3. 初始化前明确确认，发送前保留无秘密的一次派发记录，POST结果未知不重发。用户报告成功后另做只读核对：管理员已存在、bootstrap不可用、初始化审计保留；没有这些证据不能开放入口。不会自动创建默认admin/123456，也不打开密码重置。
4. 只导出公开CA，独立核对SHA256后由操作人员决定是否信任。私有CA信任涉及设备信任边界，不自动导入系统根；需要浏览器信任时优先当前用户范围，不扩大到全机。CA私钥须受限保管，应用停用或私钥泄露后需撤销本机相应信任，不能把CA文件与私钥一起分发。
5. 初始化确认后，再按已批准范围切换LAN绑定并重新检查TLS、登录/退出、持久化、审计和云开关。候选使用新目录及未发布验证镜像，不等于正式版本已发布；发布、版本和运行策略仍单独核对。

本次CA有效期1095天、服务器证书365天，实际开始/到期及指纹记录在证书和受限验收报告。当前无自动续期交付，运维需在到期前预留续签窗口；续签不得借机更改CD2信任、正式媒体权限或关闭验证。证书与匹配私钥应纳入受限备份与恢复验收，不仅备份数据库。

人工检查、服务证书续签、CA轮换及吊销状态未确认的处理顺序见[证书维护规程](mediaai-certificate-maintenance.md)。此规程不表示已经启用自动任务或换证。

本轮操作结果：本人初始化、关闭bootstrap和审计、LAN HTTPS入口、客户端严格信任及备份副本真实启动均已核对；另经用户明确同意，仅向当前Windows用户Root导入匹配的公开CA，未改LocalMachine或导入私钥。Windows支持在当前用户Root保存信任证书，具体存储说明见[Microsoft证书导入文档](https://learn.microsoft.com/en-us/powershell/module/pki/import-certificate?view=windowsserver2025-ps)。用户已确认浏览器无警告并登录成功。

备份副本恢复使用独立data/config、备份服务器证书及公开CA、不发布端口且云开关false；验证同镜像启动、管理员摘要/权限、schema/审计及TLS保持后清理本轮恢复资源，原备份保留。不在文档中提供密码或自动重置，不将未输入密码的摘要保持检查称为恢复副本登录通过。当前候选restart=no，正式新制品切换、自启和续期仍须按最终部署窗口处理，不能把临时候选当成已发布制品。实际证据见[C4报告](../development/cloud-drive/c4-acceptance-report.md#最新部署覆盖)。
