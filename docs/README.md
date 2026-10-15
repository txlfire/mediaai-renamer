# 文档索引

类型：文档导航。更新日期：2026-10-15；当前稳定版：`v1.3.0`，数据库 schema `39`。

## 使用与维护

| 需要做什么 | 文档 |
| --- | --- |
| 查看已交付能力、证据和剩余边界 | [当前项目状态](development/project-status.md) |
| 使用页面、配置账号、扫描、匹配、重命名与恢复 | [完整用户手册](manuals/MediaAI-Renamer-用户手册.md) |
| 安装、升级或回退 Docker / fnOS 实例 | [部署与更新](deployment/fnos-ghcr-docker.md) |
| 配置 HTTPS、认证及条件 MOVE | [WebDAV 部署与兼容](deployment/webdav.md) |
| 查看后台任务接口、状态、容量和清理 | [后台任务接口及维护](development/background-jobs/api-and-maintenance.md) |
| 查看当前固定制品和校验值 | [v1.3.0 发布说明](releases/v1.3.0.md) |

## 开发与设计

- [开发指南](development/development-guide.md)：环境、规范、隔离验证及提交流程。
- [常用命令](development/common-tasks.md)：规范启停、测试、构建和发布命令入口。
- [Agent 开发规范](development/agent.md)：工程约束，不替代当前任务的用户授权。
- [系统设计](design/project-design.md)：已确认的目标状态与安全约束。
- [完整主题设计](design/workbench-theme-design.md)及[设计执行入口](../design.md)。
- [主题实施计划](development/ui-refresh/implementation-plan.md)、[验收清单](development/ui-refresh/acceptance-checklist.md)和[六主题扩展计划](development/ui-refresh/theme-expansion-plan.md)。
- [后台任务实施记录](development/background-jobs/implementation-plan.md)。
- [安全治理候选方案](development/security-governance-proposal.md)：尚未批准的政策，不是默认启用项。

## 历史与证据

- [总设计与阶段回溯](design/MediaAI-Renamer-总设计文档.md)：M0-M11 历史记录。
- [v1.3.0 发布验收记录](work-logs/progress-2026-10-15-release-1.3.0.md)、[v1.2.0 发布说明](releases/v1.2.0.md)。
- [工作日志目录](work-logs/)与[历史归档](archive/README.md)。

M1-M11 专项设计、计划和手册保留阶段语境；执行当前操作优先使用完整手册和现行维护文档。工作日志、旧版 Release 及早期计划不随新版本覆盖，历史“未发布”不代表当前状态。

双方互审材料位于本机 `docs/development/reviews/`，原型草案位于本机 `design-plans/`，两者不入版本库。公开进度集中在当前项目状态页，公共入口不依赖这些本地材料；真实账号、密钥、内网地址和媒体路径不进入公开文档。
