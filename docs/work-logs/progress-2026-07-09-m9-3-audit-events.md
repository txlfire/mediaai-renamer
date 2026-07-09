# 2026-07-09 M9-3 审计日志

## 本次范围

- 新增审计事件服务和 `/api/audit-events` 查询接口。
- 审计查询要求 `audit:read` 权限。
- 接入登录成功 / 失败、管理员初始化、退出登录、密码变更、用户维护、系统设置保存、媒体源变更、外部提交拦截处理和真实重命名执行事件。
- 系统设置新增“审计日志”页，支持按事件类型、结果和操作者筛选，支持分页和详情弹窗。
- 审计详情统一脱敏 API Key、token、密码和 SMB secret 等敏感字段。

## 验证

- `npm.cmd run backend:test -- backend.tests.test_audit_api backend.tests.test_auth_api backend.tests.test_users_api backend.tests.test_config backend.tests.test_database_migrations`：通过，实际运行后端全量 221 个测试。
- `npm.cmd run frontend:test`：通过，14 个测试文件，73 个用例。
- `npm.cmd run frontend:build`：通过；Vite chunk 体积提示为既有提示。
- `git diff --check`：通过。

## 后续

- M9-4 实现重命名历史和回滚后，继续接入回滚计划创建、dry-run 和执行审计。
- M9-5 任务治理页实现后，将扫描任务、重命名批次和回滚计划聚合到统一任务视图。
