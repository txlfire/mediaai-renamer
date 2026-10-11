# 2026-10-08 外部请求共享预算

类型：实施过程记录。接续rollback-background第七节674项检查点；应用1.0.1/schema32起步，脏develop，不提交、发布或维护正式媒体。

## 恢复入口

用户在解释范围后确认“没问题，继续”，已授权默认每站点2请求/秒、单站点1实际请求、实例全局4实际请求；AI与元数据分别配置并支持Provider覆盖。只管理已配置服务的HTTP，包括本机/局域网AI，不限制本地API/文件操作/WebDAV。等待计入预算；未知AI结果不重发，不扩大站点或授权范围。

当前schema33实施及本机回归完成，最新证据读第四节；第三节保留过程及接续定位。无活动测试会话，开发服务保留。本机通过不关闭真实供应商/Linux/NAS/混合负载或整批审查门禁。

后续工程已进入schema34待处理迁移，当前恢复入口见[迁移后台记录](progress-2026-10-08-pending-move-background.md)。本文件705项及活动状态仅表示此预算检查点，不能代替后续修正终态。

## 第一节：实施清单

- 已实现：核对实际transport、认证/详情/重试与测试连接入口；SQLite共享准入及速率、实际响应读取完成前保留并发槽。进程存活时不靠到期释放，明确进程退出才清除遗留网络槽，不推断外部结果或重放。
- 已实现：config.toml独立参数、校验及隔离worker配置传递；schema33空预算表及索引，旧记录不生成请求意图。数据库只记录origin摘要、PID、随机槽及时间/限制，不存URL、API Key、正文、文件名或路径。
- 已实现：AI、TMDB/Bangumi/TVDB/豆瓣代理及既有IMDb可达性HTTP统一预算，元数据overall deadline传到线程；重定向不绕过预算或转发认证，限流等待耗尽明确显示“未发送”，实际网络超时仍为结果可能未知。
- 本机专项及完整回归通过：速率/并发跨线程跨进程、等待/锁竞争/读取占槽、进程退出恢复、配置无效/分组覆盖、旧库幂等、各服务实际transport及禁止隐式重定向；不调用真实付费服务或历史凭据。日志记录失败及最终终态，外部/平台门禁另行保留。

已确认项目后端/测试Skill，常规实现medium、关键安全审查high；无子代理、工作树或新UI依赖。安全审计保留/Edge/NAS/笔记本分辨率仍待，宿主WSL挂载/exec失败、Docker未找到，不修宿主环境。

## 第二节：接入与专项回归

- 已实现：schema33两个空预算表及索引、严格配置及worker传递；AI与TMDB/Bangumi/TVDB/豆瓣代理实际HTTP共享预算，连接测试也接入；HTTP不自动重定向，等待计入总预算，多源overall deadline传入执行线程。
- 已实现：取得资格后至响应读取/关闭完成前持槽；存活或无法核实PID不按时间回收。关闭异常保留槽并诊断。数据库仅保存origin摘要及运行状态，最多1024站点/64槽，过一天的无活跃站点按批次清理。
- 已实现：等待/存储拒绝明确提示本次未发送；一次性授权仍消费，不自动重试，结局审计submission_executed=false。已经发送的网络未知结果保留既有不重放策略。
- 首轮核心10测试通过（3.402秒）。首次接入发现循环导入，9导入错误，改为延迟加载PID核对；第二轮100测试有3错误，修正TMDB测试替身新增可选参数兼容；第三轮119测试全部通过（38.609秒）。日志依次为external-budget-first、external-budget-wiring-first/second/third，位于.codex/run-logs（2026-10-08）。不把失败轮次覆盖或描述为通过。
- 正在补充：跨进程真实槽竞争、本机HTTP重定向、存储异常/关闭故障、未发送审计及实际客户端多HTTP接入；补齐V4/V3共用预算。尚未宣称完整回归、发布或真实外部供应商验证。
- 边界首轮49测试22.958秒有2错误：跨进程退出槽无法回收，确认Windows旧PID检查仅凭OpenProcess句柄判断，进程退出但仍被引用时误判存活，增加GetExitCodeProcess核实，无法核实继续保留；另一个为新增测试使用错误ParsedMediaName参数，修正测试构造。跨进程单站点/全局竞争、本机302不跟随、未发送审计等已通过；待修复后重跑，不使用mock掩盖真实退出行为。
- 修复后external-budget-boundaries-second：112项54.100秒全部通过，包含扫描/重命名/回滚关联恢复回归；最后external-budget-final-focused：27项10.953秒全部通过，增加两类连接测试、认证切换共用预算、父进程stdin配置及三个worker入口重建。专项会话均exit0结束。

## 第三节：完整验证及接续

当前external-budget-python311-full-2026-10-08.log正在执行，尚无终态。已同步目标设计（仅目标边界）、手册24.2、部署schema33迁移边界、README未发布能力及审查F06。速率限制是准入间隔，不承诺进程被调度暂停后实际网络包绝对匀速；不新增供应商配额承诺、多机协调或硬取消。待完整回归、前端回归及健康终态，再写检查点。

完整回归期间最终审查追加：多源泛化错误丢失“未发送”提示，增加预算异常专门分支与回归；TMDB详情诊断标记预算失败/未发送；预算容量拒绝补安全阶段诊断；巨整数配置先比较范围避免math.isfinite抛OverflowError。此时运行中的完整测试不能作为这些追加修改的最终证据，结束后重跑对应专项及最终完整回归。

实际HTTP入口清单追加核对：settings_service已有IMDb网站可达性测试直接urlopen，接元数据预算和imdb配置覆盖，测试未发送提示；不新增IMDb搜索实现或扩大覆盖授权范围。WebDAV继续使用独立协议transport，不纳入本预算。最终专项/全量应包含这次追加接入。

- 首轮完整external-budget-python311-full：703项244.574秒，693通过10NAS跳过，exit0；模块加载早于最终提示/IMDb追加，不作为最终代码证据。
- external-budget-prompt-focused：83项32.386秒有1错误，新IMDb真实入口专项发现缺Request/URLError导入（原保存测试未覆盖调用入口），补回明确导入；其余预算、多源提示及设置专项通过。保留失败证据，修复后再跑。
- external-budget-prompt-focused-second：83项33.536秒全部通过。最终完整external-budget-python311-final-full正在执行；开发5173、8970健康及5173代理均HTTP200，尚未将本轮前端旧174证据替代最新回归。

### 接续定位（只读核对，未新增任务实现）

- F02剩余实际入口：rename_previews的metadata-match/metadata-multi-match及ai-parse批量仍同步返回；rename_operations/dry-run、rename_rollback_plans/{id}/dry-run、pending_files/move也同步执行。已有扫描/重命名/回滚202链不能重复开发。
- 优先核对pending_file_service.move_pending_files：使用pending_move本地动作意图，跨设备copy/delete仍可能长时间阻塞，尚未纳入共同文件写槽。下一批应复用已有监督器和FIFO写任务边界，冻结目标集合/目录，逐项当前权限及实际身份重检，未知不重放；不能只加BackgroundTasks或把HTTP超时当取消。
- 元数据/AI后台化需固定实际目标服务和用户确认范围，保留原同步接口兼容、新受理只读观察；预算槽不能替代任务状态、日志归属和取消边界。不能因预算等待失败自动扩展后备站点或重新消费一次性授权。
- F04审计独立保留、Edge实机方案、NAS专用目录、笔记本分辨率仍待答；Linux/容器/CI运行前提未满足，不修宿主或使用历史凭据。36R是累计编号，六大工作包仍有子项，当前阶段通过不等于整个审查已完成。

## 第四节：最终检查点

- 后端最终：独立哈希锁Windows Python3.11.15，705项245.186秒，695通过、10受控NAS跳过，exit0。日志external-budget-python311-final-full-2026-10-08.log包含最终提示、IMDb接入及所有31新增回归；前一703项是追加修改前证据，不作最新终态。
- 前端：174项6.71秒全部通过（32文件），external-budget-frontend-2026-10-08.log；vue-tsc及Vite构建通过，Vite阶段1.21秒，external-budget-build-2026-10-08.log。主JS749.88kB/gzip248.37kB、CSS417.25kB/gzip57.93kB，500kB警告仍开放，不调阈值隐藏。
- 编码与git diff --check通过，external-budget-encoding-2026-10-08.log。健康采集首次PowerShell管道语法错误未执行，改用脚本块后健康日志确认5173/8970/api/health/5173代理均200；没有因此重启或清理正式实例。
- 所有必要测试/构建会话exit0结束，仅保留既有隐藏开发服务（b2/ui-config隔离实例）。应用1.0.1、schema33、脏develop；不提交、升级应用版本、重打包旧ZIP或发布。已有schema30 ZIP不是当前代码制品。
- 已同步README未发布能力、目标设计、手册24.2、部署迁移边界、主修复计划、F06审查与长期记忆；过程失败轮次保留。下一项定位见第三节，先待处理迁移与共同文件写槽衔接，再其他长任务，不重复扫描/重命名/回滚202核心。
- 未验：真实外部供应商、Linux/NFS、跨主机协调（不支持）、代表性多worker混合负载/P95、Edge实机及4K组合缩放、NAS专用写目录、实际Docker/CI/镜像及正式升级回退。安全审计保留策略待答，不因运行日志规则删除安全审计；本轮不存在凭据外发或正式媒体试写。

验证命令（均从项目根目录）：

```text
.codex/test-artifacts/review-fixes/dependency-lock/venv311/Scripts/python.exe backend/run_tests.py discover backend/tests -v
npm.cmd run frontend:test
npm.cmd run frontend:build
npm.cmd run check:encoding
git diff --check
```

Node命令仅在对应进程PATH前置D:/nvm/v24.15.0，不修改系统环境。后端最终与专项均经backend/run_tests.py临时配置，不直接导入main初始化默认库；历史首次直接测试的默认库触碰事实保持原记录，不自行还原。
