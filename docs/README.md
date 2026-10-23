# 文档索引

类型：文档导航。更新日期：2026-10-22；当前稳定版：`v1.4.0`，数据库 schema `55`，公开Release、固定镜像与校验结果见[发布记录](work-logs/progress-2026-10-22-release-1.4.0.md)；旧v1.3.0为schema39。

最新入口：[v1.4.0说明](releases/v1.4.0.md)、[CD2配置](manuals/cd2-settings.md)、[盘点](manuals/cd2-inventory.md)、[目录改名](manuals/cd2-directory-rename.md)、[广告复核](manuals/cd2-ad-review.md)、[隔离恢复](manuals/cd2-quarantine.md)。本轮审查修复及fnOS验收版更新通过，三个执行开关关闭；真实广告标签用户选择保留待验。下方阶段计数、未发布和NAS未更新描述保留历史语境，当前门禁以[项目状态](development/project-status.md)为准。

## 使用与维护

2026-10-23运维更新：fnOS已切换固定v1.4.0并验证一次容器重启，见[部署记录](work-logs/progress-2026-10-23-fnos-stable.md)；证书有效期检查及[人工维护规程](deployment/mediaai-certificate-maintenance.md)已补齐，不代表NAS整机开机或自动续期通过。下方较早NAS未升级表述为历史。

| 需要做什么 | 文档 |
| --- | --- |
| 查看已交付能力、证据和剩余边界 | [当前项目状态](development/project-status.md) |
| 使用页面、配置账号、扫描、匹配、重命名与恢复 | [完整用户手册](manuals/MediaAI-Renamer-用户手册.md) |
| 安装、升级或回退 Docker / fnOS 实例 | [部署与更新](deployment/fnos-ghcr-docker.md) |
| 配置 HTTPS、认证及条件 MOVE | [WebDAV 部署与兼容](deployment/webdav.md) |
| 为未发布 CD2 接入及升级准备安全通道、受限令牌与匹配备份 | [CD2 接入与升级准备](deployment/cd2-readonly-preflight.md) |
| 准备真实广告线索人工标签及统计口径（尚无真实误判率） | [C4 广告线索标注验证](development/cloud-drive/c4-ad-label-validation.md) |
| 查看后台任务接口、状态、容量和清理 | [后台任务接口及维护](development/background-jobs/api-and-maintenance.md) |
| 查看本轮制品、校验值与发布状态 | [v1.4.0 发布说明](releases/v1.4.0.md) |

## 开发与设计

- [开发指南](development/development-guide.md)：环境、规范、隔离验证及提交流程。
- [常用命令](development/common-tasks.md)：规范启停、测试、构建和发布命令入口。
- [Agent 开发规范](development/agent.md)：工程约束，不替代当前任务的用户授权。
- [系统设计](design/project-design.md)：已确认的目标状态与安全约束。
- [完整主题设计](design/workbench-theme-design.md)及[设计执行入口](../design.md)。
- [主题实施计划](development/ui-refresh/implementation-plan.md)、[验收清单](development/ui-refresh/acceptance-checklist.md)和[六主题扩展计划](development/ui-refresh/theme-expansion-plan.md)。
- [后台任务实施记录](development/background-jobs/implementation-plan.md)。
- [安全治理候选方案](development/security-governance-proposal.md)：尚未批准的政策，不是默认启用项。
- [CD2 / 115超大库整理评估](development/cloud-drive/cd2-115-assessment.md)、[C0基础实现](development/cloud-drive/c0-implementation.md)、[持久化预算](development/cloud-drive/c0-budget-implementation.md)及[C1内部盘点](development/cloud-drive/c1-inventory-implementation.md)：未发布的只读工作台、当前条目保留、当前视图导出及手动观察清理，C1迁移至schema42，当前整体源码目标见下方C2。[C1-2受控网页入口](development/cloud-drive/c1-2-web-entry.md)后端/页面及本地验证完成、默认关闭，一次专项真实HTTPS接口本层验收通过；正式部署及实际系统缩放待验。云端目录改名和广告删除未开放，不属于v1.3.0已交付功能。
- [C2-0目录改名能力评估](development/cloud-drive/c2-capability-assessment.md)及[安全设计](design/cd2-directory-rename-safety.md)：资料、离线模型及受控空目录试验已核对；用户接受维护窗口受控首版，不承诺原子源身份或外部并发安全，不因此授权正式媒体写。
- [C2详细核验清单](development/cloud-drive/c2-acceptance-checklist.md)及[历史原生空目录协议报告](development/cloud-drive/c2-controlled-probe-report.md)：保留2026-10-17协议改名/改回/同名拒绝及清理证据，不冒作下方2026-10-18产品链结果。
- [C2实施计划](development/cloud-drive/c2-implementation-plan.md)、[固定计划契约](development/cloud-drive/c2-plan-contract.md)、[执行契约](development/cloud-drive/c2-execution-contract.md)、[恢复与反向契约](development/cloud-drive/c2-recovery-contract.md)及[操作说明](manuals/cd2-directory-rename.md)：固定计划、独立预算、原生连接器、隔离worker、路径修订、HTTPS执行和统一日志、只读位置核对、部分批次本地同步、反向新计划及本人记录已接入。开发与授权空目录产品验证完成：后端1099项（12跳过）、前端380项、最新396浏览器场景及24次手机提示交互通过；真实正反/冲突产品链通过，53应用RPC/15管理/174.836秒，根和五Token清理已核对。源码schema50，旧库未迁移，正式部署/实机及正式媒体写仍未放行；见[验收记录](development/cloud-drive/c2-acceptance-report.md)和[收尾日志](work-logs/progress-2026-10-18-cd2-c2-recovery.md)。

CD2设置与迁移见[配置手册](manuals/cd2-settings.md)及[配置设计](design/cd2-database-settings.md)，观察保留/本页导出/清理见[盘点手册](manuals/cd2-inventory.md)。C3/C4 已续作，未发布源码schema55（CD2配置入库）：见[实施计划](development/cloud-drive/c3-c4-implementation-plan.md)、[广告复核安全设计](design/cd2-ad-review-safety.md)、[复核契约](development/cloud-drive/c3-ad-review-contract.md)、[取样/隔离能力核对](development/cloud-drive/c3-capability-assessment.md)、[人工复核手册](manuals/cd2-ad-review.md)、[综合验收清单](development/cloud-drive/c4-acceptance-checklist.md)及[本轮验收](development/cloud-drive/c4-acceptance-report.md)。人工复核与本地固定计划不读取正文或外发，也不直接执行隔离、删除；实际隔离须走独立批准链。

实机与续作：公司1.1.2最小Token产品链已通过，76应用RPC/20管理、255.271秒。家庭精确1.1.3兼容及1206项后端回归（1194通过/12受控跳过）通过；独立C2空目录产品链53应用RPC/15管理、163.192秒，C3小TXT产品链76应用RPC/19管理、248.900秒通过。正向、反向新计划、同名保护及各轮夹具/Token收尾完成，家庭无保留测试根；NAS候选未升级。见[版本核对](development/cloud-drive/cd2-runtime-compatibility.md)、[当日交接](work-logs/progress-2026-10-20-cd2-company.md)及[本轮C0—C3综合审查](work-logs/review-2026-10-20-c0-c3.md)。

2026-10-19历史检查点：[隔离产品计划](development/cloud-drive/c3-quarantine-implementation-plan.md)Q1至Q4及Q5本地门禁完成，见[接口与数据](development/cloud-drive/c3-quarantine-contract.md)、[隔离恢复手册](manuals/cd2-quarantine.md)及[当时复核](work-logs/review-2026-10-19-c3-quarantine.md)。当时补强后1194项后端（1182通过/12受控跳过），前端431项/类型构建及528浏览器、隔离39→54/旧备份恢复已有证据；网络超时及仅识别1.1.2均属于当时状态，后续家庭兼容及实机结果见上段。执行仍默认关闭，未提交或发布；辅助测试误初始化默认库的偏差及限制见[第28节](work-logs/progress-2026-10-19-cd2-quarantine.md#28-版本核对与辅助验证隔离偏差)，不能以其后哈希稳定抵消。

## 历史与证据

- [总设计与阶段回溯](design/MediaAI-Renamer-总设计文档.md)：M0-M11 历史记录。
- [v1.3.0 发布验收记录](work-logs/progress-2026-10-15-release-1.3.0.md)、[v1.2.0 发布说明](releases/v1.2.0.md)。
- [工作日志目录](work-logs/)与[历史归档](archive/README.md)。

M1-M11 专项设计、计划和手册保留阶段语境；执行当前操作优先使用完整手册和现行维护文档。工作日志、旧版 Release 及早期计划不随新版本覆盖，历史“未发布”不代表当前状态。

双方互审材料位于本机 `docs/development/reviews/`，原型草案位于本机 `design-plans/`，两者不入版本库。公开进度集中在当前项目状态页，公共入口不依赖这些本地材料；真实账号、密钥、内网地址和媒体路径不进入公开文档。
