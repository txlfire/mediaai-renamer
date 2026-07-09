# 2026-07-09 M9 收尾和 v0.9.0 发布

## 完成内容

- 将版本从 `0.8.2` 修正为 `0.9.0`。
- 补充回滚接口权限守卫测试，覆盖未登录、缺少权限和具备权限三种路径。
- 同步 M9 操作运行日志验收清单，按已落地能力标记完成项。
- 新增 M9 用户手册和 M9 验收报告。
- 更新完整用户手册、README、总设计文档、项目设计文档和 M9 设计手册。
- 明确任务归档入口延期到 M9+ / M10 前治理增强，不纳入 `v0.9.0` 正式范围。

## 验证计划

- `npm.cmd run backend:test`
- `npm.cmd run frontend:test`
- `npm.cmd run frontend:build`
- `npm.cmd run check:encoding`
- `git diff --check`
- `npm.cmd run release:package`

## 发布口径

`v0.9.0` 是 M9 正式发布版本，重点交付本地认证、直接用户权限、审计日志、操作运行日志、重命名回滚和任务治理基础能力。
