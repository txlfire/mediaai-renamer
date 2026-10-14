# 2026-10-14 后台化与提交发布接续

类型：实施与发布工作记录。用户已确认扩大流程；采用mediaai-backend-api、mediaai-test-release。起始develop/ea7787d、ahead6、工作区保留所有既有修改；版本1.1.0，schema38。

## 当前入口

- [实施清单](../development/background-jobs/implementation-plan.md)。统一剩余表仍为审核实施计划第6.5节，不另造已完成总表。
- 前置桌面适配已完成：504常规场景、24专项、227前端测试、类型/构建、编码/diff及52引用通过，详见[桌面日志](progress-2026-10-14-desktop-adaptation.md)。不能把这些数量当新后台工程的测试结果。
- 标准5173/8970保持运行，不手动停服；测试主模块仅由backend/run_tests.py临时配置启动，不使用真实凭据或媒体/NAS写入。

## 核对与决策

F02四类入口仍同步；现有隔离进程监督、共享外发逐HTTP预算和短事务干跑可复用。独立background任务受理不占实际文件写槽，稳定ID查询和不重试未知结果是本阶段门禁。原同步API保持兼容，新前端入口单独接入。

新任务/结果需有清理边界，不将运行日志规则套用到安全审计；不把历史完整回归冒充迁移后结果。普通结果存储不含raw_data/提示词，凭据只参与摘要。各节实现、红测/修正、实际命令/退出码、未验范围继续追加。

## 进度

1. 后台受理与schema39迁移、结果清理及幂等记录：已实现，尚待完整回归。
2. 隔离进程监督、外发/发布守卫、四类前端后台入口及全局观察抽屉：已实现，正在补边界验证。
3. 可执行回归、制品/版本/Git/发布：待执行。

## 中断接续检查点

中断前仅20项后台专项通过（12.393秒），不能作为之后计数、任务中心、前端接入改动的证据。接续时标准服务均已停止，已使用`npm.cmd run dev:windows -- start`恢复5173/8970并保持运行。前端类型检查与构建成功；主包507.12kB，保留既有500kB警告。

正在补前端稳定UUID、未知响应查询不重发、跨会话隔离、结果清理提示及列表上限。旧存储会静默丢弃第51条待核查ID，已改为保留记录并拒绝新增。两组旧store测试仅替换适配层mock；另补真实后台store契约测试，避免以mock掩盖传输变化。尚未提交、升级版本或发布。

## 用户暂停检查点

2026-10-14用户要求“暂停一下，记录当前工作”。已暂停开发、停止本轮未结束的桌面验收Node进程（PID34680，命令行已核对为verify-desktop.mjs），未停止标准前后端。已运行的87项专项自然完成并收取退出码。浏览器新专项此前已失败并由finally关闭其浏览器。所有已知本轮测试session均收取终态；后续不得依据旧PID起停。

### 当前代码与边界

- develop仍为ea7787d、领先origin/develop六提交；已有主题、Harness修复与新后台工程均保留在脏工作区，未回退其他修改。
- 四类新增后台入口：rename_dry_run、rollback_dry_run、metadata_batch、ai_batch。POST /api/background-jobs返回202；按客户端UUID查询摘要/结果、同ID幂等，原同步API兼容保留。旧TMDB批量/AI后备组合并未全部改为新入口。
- schema39增加background_jobs及background_job_items，缓存计数和索引。任务绑定当前用户、输入/来源及配置摘要；隔离子进程不导入main、不初始化用户；授权发放前可靠保存PID。
- 任务共用独立探测/外发单执行槽，不占实际文件写FIFO。外发排队后再查守卫；多源线程继承上下文；发布前复核权限/配置，AI另复核原输入，元数据检查来源和已发布预览。
- 回滚逐项探测已避免每项重读整批，初始和最终仍校验整批。新增监督者PID，使“认领后、子进程PID交接前监督者已退出”可安全回收；不能证明实际进程退出的槽继续保留，不自动重试。
- 普通结果默认单任务4MiB、总64MiB、7天；受理容量预留、最多200条终态有界清理、聚合最终实际字节检查。原始供应商数据/提示词不进入任务结果；幂等摘要/请求ID及安全审计不被结果清理删除，不自动VACUUM。此上限不是整个SQLite文件大小上限。
- 前端保存UUID后才POST；受理响应不明确则保留ID并只查询原任务；owner/会话代次隔离；摘要轮询前台1.5秒、隐藏6秒、去重，不在轮询中下载完整结果。最多50条本浏览器待核查ID，满后拒绝新提交，不丢弃旧记录。关闭观察抽屉不取消已受理任务；退出登录也不自动重放/撤销业务任务。
- 新顶部后台任务抽屉、任务中心类型/跳转、已清理结果禁用提示、显式核查、关联批次只读入口已接。新进度关联、关联批次恢复、前端实交互仍需浏览器补证。
- config/config.example.toml已增加background_jobs参数。版本仍1.1.0；本轮拟发布v1.2.0只是目标，尚未改版本文件、打包、commit、push、tag或创建Release。

### 验证事实

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| 前端回归 | 36文件、245项通过，6.01秒；这是新增进度/关联入口修改前的单测检查点，最终仍需重跑 | npm.cmd run frontend:test，本轮工具输出 |
| 类型/构建 | vue-tsc与Vite通过，1.16秒；主包508.18kB，既有500kB警告未关闭 | .codex/run-logs/background-frontend-build-2026-10-14.log |
| 后台专项中间值 | 29项通过，13.960秒；之后又补AI返回输入复核、真实元数据发布等，不当作最终全量 | .codex/run-logs/background-jobs-current-2026-10-14.log |
| 后端首轮完整 | 845项、287.486秒、24错误、10受控WebDAV跳过，未通过 | .codex/run-logs/background-full-2026-10-14.log |
| 事务修正后专项 | 87项通过、42.090秒、退出0；覆盖后台、元数据、预览API及分页 | .codex/run-logs/background-transaction-focused-2026-10-14.log |
| 摘要过滤量测 | 100次查询、响应382字节、P95 14.684ms；临时任务total_count设为1000、结果字段1MiB，实际明细仅2条。不是实际1000项执行/混合负载验收 | 上述87项专项日志 |
| 桌面新入口矩阵 | 1280x620、1366x668两个视口循环已结束；剩余中途取消，退出-1。无本轮完整终态JSON，不计504通过 | .codex/run-logs/background-desktop-2026-10-14.log |
| 后台浏览器新专项 | 入口等待30秒超时、退出1、0成功场景；不是产品崩溃证据 | .codex/run-logs/background-browser-2026-10-14.log、.codex/test-artifacts/ui-refresh/background-browser-results.json和background-failure.png |

全量24错误的根因已定位：_assertPreviewEditable在未开启事务时会执行BEGIN IMMEDIATE；_update_preview_metadata原先直接调用它后又执行BEGIN IMMEDIATE。已改为短BEGIN读快照并commit释放，再在名称/存在性计算后开启短发布事务。保留最终输入与当前权限复核，不在慢探测时占写锁。87专项通过，但完整845项尚未重跑，原失败日志保留。

后台浏览器脚本失败已从saved unmatched定位：**/api/**同时匹配Vite源码/src/api/client.ts；新脚本漏了“非/api/路径continue”保护，误返回409导致模块加载失败。暂停时只记录，未修脚本。旧desktop脚本有这项保护。本轮未生成最终桌面JSON；desktop-results.json和desktop-panel-results.json如仍存在，属于上一轮证据，不能据此宣称新全局入口完成验收。

### 下次接续顺序

1. 先读取本节并运行标准status/健康查询；运行中不要再次start，也不要用历史PID。读取git diff，保留现有工作。
2. 修复verify-background.mjs路由拦截，仅处理pathname以/api/开头，其余continue；重跑失响应/刷新恢复、未知核查、清理禁用、1000项合成大结果和六态/常用视口。这是本地合成验证，不能证明真实供应商、真实缩放或实际最高批量端到端性能。
3. 最终前端测试、类型/构建；重跑两组桌面矩阵及新抽屉交互，实际结果写新日志，不覆盖历史失败和旧通过证据。新后端字节聚合截断分支、会话切换受理迟到等仍须检查覆盖。
4. 用锁定Python3.11.15重跑完整后端；若失败先修正。普通日志已有降级/超时故障注入输出，不能把预期日志当执行失败，也不能把真实错误忽略。
5. 补后台接口/维护手册、当前设计事实、版本和发布说明；执行编码、diff、本地引用和敏感信息检查。打包内容/隔离启动、旧schema升级及合成实例回退证据仍未补。
6. 通过门禁后按已授权develop→main→tag→Release发布并核对CI/GHCR。GitHub CLI已认证，最新Release实际仍v1.0.1，不能宣称v1.2.0存在。发布授权不包含真实NAS写入或正式实例升级/回退。

推荐接续命令（均在仓库根；只改当前进程PATH）：

```powershell
$env:Path = 'D:\nvm\v24.15.0;' + $env:Path
npm.cmd run dev:windows -- status
npm.cmd run frontend:test
npm.cmd run frontend:build
.\.codex\test-artifacts\review-fixes\dependency-lock\venv311\Scripts\python.exe backend/run_tests.py discover backend/tests -q
node .codex/test-artifacts/ui-refresh/verify-background.mjs
node .codex/test-artifacts/ui-refresh/verify-desktop.mjs
node .codex/test-artifacts/ui-refresh/verify-desktop-panels.mjs
```

root .venv为Python3.14.6，不能拿它替代锁定3.11发布回归证据。测试main只经backend/run_tests.py临时配置，不接真实数据/凭据。完整回归或浏览器测试未恢复前不要提交发布。

暂停即时服务核对：npm.cmd run dev:windows -- status显示两端running，后端健康返回1.1.0/ok。访问http://127.0.0.1:5173/；服务保持运行，不修改系统缩放、NAS配置或正式用户。

## 恢复执行检查点

用户要求“继续工作，直到完成”，撤销暂停，沿用已确认开发/测试/提交发布范围。接续状态检查发现两端停止，使用标准脚本恢复5173/8970，未操作NAS/真实媒体。浏览器脚本补非/api路径continue；第一轮恢复已通过失响应UUID/刷新观察及未知核查/清理禁用，后续布局测试却优先恢复待核查ID而不是新任务，修正为下拉框显式选择新任务，不改变产品防重放逻辑。

完整后端、桌面与浮层矩阵正在重新运行。另补聚合用量超限仍保留逐项已知结果、受理迟到成功/401的会话隔离测试；结果抽屉新增汇总/逐项切换，摘要因大小压缩时默认展示逐项。尚未拿中间通过作为最终发布证据。

## 发布前最终验证

本节覆盖暂停和恢复过程中的中间状态，不删除失败证据。版本已同步1.2.0、schema39。提交前复核修正了已知AI失败的逐项状态（failed而非completed），并增加幂等中断审计；普通结果清理不删除中断审计。未修改真实供应商配置、NAS/正式媒体，测试仅临时库、合成transport和隔离新包。

| 项目 | 最终结果 | 日志/证据 |
| --- | --- | --- |
| 后端锁定Python3.11.15完整 | 850项、275.780秒、840通过+10受控WebDAV跳过，0错误/失败，退出0 | release-backend-reviewed-1.2.0-2026-10-14.log |
| 前端单测 | 36文件247项、5.70秒、退出0 | background-frontend-final-2026-10-14.log |
| 类型/构建/制品 | vue-tsc与Vite通过1.25秒，主包508.84kB，保留500kB警告；最终ZIP141条目 | release-package-1.2.0-2026-10-14.log及release-package-reviewed-1.2.0-2026-10-14.log |
| 新入口桌面矩阵 | 504场景、0溢出/不可达/异常/未配置API | background-desktop-final-2026-10-14.log、desktop-results.json |
| 浮层/固定列/全屏 | 24场景、0异常/未配置API、选择保留 | background-panels-verified-2026-10-14.log、desktop-panel-results.json |
| 后台真实浏览器 | 38场景、4合成POST、39完整结果读取、115小摘要读取；失响应刷新同UUID且未重发，清理禁用、取消核查保留ID、六态六视口通过 | background-browser-release-2026-10-14.log、background-browser-results.json、background-*截图 |
| 1000行合成后台任务 | 受理139.272ms、处理20.670秒；并行166摘要查询最大389字节、P95 32.522ms；Python tracemalloc峰值6.425MiB；完整结果655366字节；外发0、错误0 | background-scale-1.2.0-2026-10-14.log |
| 最终包隔离运行/迁移恢复 | 新装登录/SPA/后台API成功；真实v1.0.1旧源码生成schema20库升级39成功，v2凭据转v3；恢复字节相同旧备份后旧版登录/解密成功 | release-upgrade-reviewed-1.2.0-2026-10-14.log |
| 文档/编码 | 17当前文档72本地引用通过、编码与diff通过；最终提交前再查最新变更 | verify-documents.mjs及编码命令输出 |

最终ZIP SHA256：f69a3f8aead315609fcb76481356758a38b44cc8299f7a0c6fb3450e7ac86343。完整包不含配置实例、数据库、日志、tests、pyc或本地凭据。

850的前置847/848完整通过为历史检查点，24事务错误的首轮保留。浮层第一轮被实施时热更新影响而失败，重跑完整24通过；后台脚本先后修复/api误拦、Element Plus选择输入遮挡及关闭按钮定位，产品功能未因脚本问题降级。浏览器最大870ms为合成大结果点击至DOM可读的时间，含获取/布局，不等于整站P95；tracemalloc不是操作系统总内存，1000行合成AI不是供应商混合压测。

待发布步骤：敏感信息/暂存差异复核、develop提交与main快进、CI/Docker/WebDAV门禁、标签/Release上传及来源验证。真实组合缩放、NFS硬阻塞、全部实供应商、正式实例升级/回退、F04审计/物理维护及登录/内网部署政策仍开放，不以发布授权擅自关闭。

## Git与远端门禁

79暂存文件启发式凭据检查、版本一致性和diff检查通过；排除.codex/.agents、互审目录、实际config/数据库/日志。提交9f3f7c3已推送develop/main；main经过祖先检查后CAS快进，未切换工作目录、未让运行服务读取旧源码。标准代理健康1.2.0/ok。

远端首次WebDAV任务38565921584/38565921281失败，原日志ci-webdav-failed-38565921584.log保留。12:43 UTC的10个真实集成中3项MOVE失败；测试调用仍只传conditional_move_status却遗漏expected_source_version，被当前强ETag门禁在外发前正确拒绝。只更新集成fixture：实际运行端点三语义能力验证，通过后绑定强ETag；新增缺版本不写和错ETag服务端412且文件不变断言。不修改生产写入门禁、不跳过测试、不碰188 NAS。修正后须再等待远端协议门禁，不把本机跳过当集成通过；尚未创建标签或Release。

第二轮c8d949f真实协议验证拒绝WsgiDAV默认未加引号DAV:getetag（38566596297日志保留），未放宽客户端。核对4.3.3实际源码：get_etag为内部无引号标识，HTTP响应会加引号，但DAV属性直接返回原标识。受控fixture添加StrictEtagProvider，只修正DAV属性序列化，底层If-Match/MOVE仍由原WsgiDAV处理；正式后端/包未改。仅在.codex隔离target安装测试依赖，不改requirements锁或标准venv。

本机独立CA/TLS服务可实际完成MOVE、禁止覆盖、反向MOVE和恢复；第一次完整12案例有能力探测/清理unknown，不能算通过。独立缺版本场景1项通过58.401秒；127.0.0.1绕开localhost地址族等待后完整16.676秒仍3项setup unknown，原因继续排查；不降低验证强度。852完整中的1失败为原安全fixture未更新能力验证mock，已补明确返回并58专项通过；失败日志保留。待远端真实Linux确认最终fixture，不能跳过unknown强跑。

800463b的两组Linux/Python3.13真实HTTPS WebDAV12项全部通过，38569127381为1.859秒，38569126220亦成功。Windows临时fixture的另轮恢复连接unknown保留为环境边界，不冒充全套通过。

主流水线38569127567的Linux/Python3.11完整852项有2个旧测试缺陷：UrlRedactionTest只指定临时日志目录，数据库仍为项目默认data，Windows因已有本机库而通过，Linux空库报无system_settings；因此先前“全回归不读取正式库”的断言不成立，不能隐瞒。已把该fixture数据库也放临时目录并初始化，不自行回退/删除本机库。迁移API测试把合法target_directory/结果路径也当异常泄漏，在Windows靠JSON反斜杠转义误通过；改为明确保留合法结果路径，只禁止error_message和item.message回显内部路径。生产接口未改。

增加候选镜像load→隔离容器健康/版本/SPA/认证边界/schema实跑→push门禁，所有测试仍依赖verify成功。仅创建并清理CI runner临时容器，不部署用户NAS。日志完整保留，需等待下一轮CI及制品来源验证后才能发布。
