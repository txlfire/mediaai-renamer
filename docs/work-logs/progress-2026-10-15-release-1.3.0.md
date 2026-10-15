# v1.3.0 正式发布记录

类型：交付实施记录。日期：2026-10-15。用户明确授权移除预览版标识、提交代码及正式发布，覆盖上一主题阶段“未授权提交/发布”的停止条件；不改写历史验证事实。

起点develop/5b38aa3，与远端develop/main一致。采用1.3.0作为六主题新增功能的次版本；schema保持39。所有上一阶段未提交的键盘、焦点、命名布局及文档改动保留并纳入本次审查。默认不提交.codex/.agents、本地互审目录、真实配置、数据库、日志或凭据。

## 进度

1. [完成] 核对分支、远端、发布脚本、GitHub登录；当前最近正式版本1.2.0。本机无Docker。
2. [完成] 移除预览版文案、版本一致性及README/手册/发布说明同步。
3. [完成] 当前源码前端、类型/构建、隔离完整后端、编码/差异、打包及隔离产物验证。
4. [完成] 暂存差异及凭据检查、develop提交推送、main受控快进、CI与镜像门禁。
5. [完成] 新标签、正式非预发布Release、下载哈希与匿名固定镜像来源核验；最终文档提交不移动发布标签。

前端阶段1008等历史结果见[主题终态](progress-2026-10-15-theme-expansion-production.md)，本轮不重复计算为新执行。真实4K组合缩放、全部复合状态/读屏、原供应商/NAS/平台及安全治理边界不因正式发布自动关闭。标准5173/8970服务保持，不部署或修改NAS。

## 发布前检查点

- frontend:test重新执行：39文件276项通过；vue-tsc/生产构建通过，主块513.09kB警告保留。App.test验证health未知不虚构版本、返回后只显示v1.0.1，不再有预览版前缀。
- npm.cmd run release:package -- -SkipBuild通过，使用刚完成的正式构建。ZIP141条目，不含真实config、数据库、日志、测试、缓存或凭据；SHA256为58ea065ca2d9b3f07d564eb403c2cd6a4d7946b0bb1b2cb0887c48d4a40680ee。
- .codex/test-artifacts/release-v1.3.0/verify-package-upgrade.py：隔离新装、v1.0.1真实schema20旧库升级至39、新v3凭据、登录/SPA/后台读取通过；恢复字节相同旧备份后旧版登录与解密通过。只操作新临时目录，标准数据与服务不变。
- verify-built.mjs重新跑24编译产物场景，所有侧栏断言实际显示v1.3.0，页面异常/console error/未配置网络均0。合成health夹具明确设置1.3.0，没有在产品硬编码版本显示。
- YAML结构核对确认verify→candidate load→实际容器smoke→push顺序保留。Windows当前无Docker，本机不冒充容器实跑，待远端门禁。
- 编码及git diff --check通过；当前产品frontend/src/backend/app及README已无预览版字样，M1/M2旧手册中的历史版本标识保留，不伪改历史。
- 完整Windows后端使用此前锁定Python3.11.15环境的backend/run_tests.py隔离配置运行，852项、388.627秒，840通过+12受控WebDAV跳过，0失败/错误，退出0；不使用默认Python3.14代替锁定环境，不把跳过计作真实TLS通过。
- 46个暂存文件凭据形态、私有路径、版本与差异检查通过；不含本地互审或.codex证据。依赖版本与锁的依赖条目未改变，只同步自身1.3.0元数据。ZIP736903字节，前后端健康1.3.0/ok及5173运行保持。

## Git与远端门禁

代码提交f95023170d487f677e678c4b01a733343e117b71已推送develop/main。先核对旧main是develop祖先，再以旧OID比较交换快进main；未切换运行目录、未回退源码或移动旧标签。提交后工作区干净。main CI为38744309500，前端测试构建已通过，Linux完整后端及后续真实TLS/容器门禁运行中；未创建标签或Release，不把排队/执行中写成成功。

选择器在发布源码上再次22/22通过，0页面异常/未配置API；真实点击生成预览的POST仍被隔离拦截器409拒绝，未透传标准后端。

发布前文档复查补正手册页首仍为1.2.0的历史元数据为1.3.0，日期与正文一致；此补正和本节进度记录为纯文档，运行源码、依赖和ZIP不变，后续新标签将包含该补正而不修改已推送代码提交。

首轮main CI 38744309500完整成功：Linux前后端、编码/Compose、真实HTTPS WebDAV及镜像候选实际健康/版本/SPA/认证/schema门禁通过，随后才上传main镜像。纯文档补正提交162395d4d1b12a007e60198b8ca22aab6723b1ac已推送develop/main；与f950231只有手册和本记录两份文档差异。新建v1.3.0注释标签指向162395d，旧标签不动；同源码main流水线38744879773和标签流水线38745100603运行中，正式Release尚未创建。

## 正式发布终态

2026-10-15 21:48:28（Asia/Shanghai）创建[正式v1.3.0](https://github.com/txlfire/mediaai-renamer/releases/tag/v1.3.0)。本节覆盖上文运行中状态，不删除过程事实。

| 项目 | 最终结果 | 证据 |
| --- | --- | --- |
| 标签源码 | v1.3.0固定162395d4d1b12a007e60198b8ca22aab6723b1ac；运行变更f950231，其后两文件只补文档 | git tag/远端refs；不移动v1.2.0或v1.3.0 |
| main CI | 38744879773完整成功；前序38744309500也成功 | .codex/run-logs/ci-main-docs-1.3.0-38744879773-full.log |
| 标签CI | 38745100603完整成功；Linux852项、836通过+16平台/集成跳过；独立真实TLS WebDAV12项全过1.914秒 | .codex/run-logs/ci-tag-1.3.0-38745100603-full.log |
| 统一镜像 | 先实际运行health=1.3.0/ok、SPA、私有接口401、schema39，再push | 标签CI Build and push unified image |
| 固定镜像来源 | ghcr.io/txlfire/mediaai-renamer:v1.3.0，linux/amd64；匿名读取、OCI版本和162395d源码标签一致 | .codex/run-logs/release-registry-verified-1.3.0-2026-10-15.log |
| Release资产 | 非草稿、非预发布，ZIP uploaded，736903字节、141条目 | gh release view，资产ID644011195 |
| 下载校验 | 远端实际下载与本地SHA256一致 | .codex/test-artifacts/release-v1.3.0/downloaded/ |

ZIP SHA256：58ea065ca2d9b3f07d564eb403c2cd6a4d7946b0bb1b2cb0887c48d4a40680ee。

固定镜像摘要：sha256:acf402a9fdb2212c1af646a8a730a8d498f926f78b6afc82dfaf5c87bffd6945。main/latest是滚动别名，不替代固定版本来源。发布后仅提交文档终态，不重打ZIP、不改标签或固定镜像；后续文档main CI另核对，不以标签通过替代其终态。

执行记录：npm.cmd run frontend:test、frontend:build、release:package -- -SkipBuild、check:encoding；锁定Python3.11的backend/run_tests.py；隔离包升级与正式产物浏览器脚本；git差异/暂存检查、commit/push/受控快进/tag；gh run watch/view、gh release create/view/download及下载哈希；严格HTTPS匿名registry校验。完整命令参数和CI日志在本地证据目录，不提交令牌或运行数据。

历史小问题：PowerShell未引用v1.3.0^{}导致只读rev-parse参数解析失败，引用后正确返回162395d，没有改引用；上传前registry预查返回404，等待标签流水线上传后严格匿名验证通过，没有关闭TLS、修改包权限或重试外发业务。CI保留Node20 Actions运行时弃用及Ubuntu镜像即将迁移的告警，本轮不借发布擅自升级工作流。

剩余边界保持：实际4K/200%/125%及用户笔记本实机、全读屏/WCAG/复合状态、NFS硬阻塞、真实供应商及正式实例升级回退仍需对应证据；0.1秒高压SQLite争用不算已扩容。审计清理/登录策略只有候选方案，不随发布启用。没有操作188 NAS或更改实际账户、凭据和媒体。标准5173/8970保持运行，健康已返回1.3.0/ok。
