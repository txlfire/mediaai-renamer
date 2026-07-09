# 2026-07-09 M9-0 治理 schema

## 本次范围

- 启动 M9-0 数据迁移和兼容策略开发。
- 新增 M9 治理表初始化和旧库迁移。
- 补充数据库迁移测试。

## 实现内容

- 数据库 schema version 提升到 `14`。
- 新增 `users` 表，保存本地用户、权限列表、启用状态和密码哈希。
- 新增 `user_sessions` 表，保存会话 token 哈希、过期时间和吊销时间。
- 新增 `audit_events` 表，保存审计事件、操作者、目标、结果和脱敏详情。
- 新增 `rename_rollback_plans` 表，保存回滚计划摘要。
- 新增 `rename_rollback_items` 表，保存回滚计划条目。

## 验证

- 已先写 `test_existing_m13_database_is_migrated_to_m9_governance_schema` 并确认失败。
- 已实现迁移后运行新增迁移测试通过。
- 已运行 `backend.tests.test_database_migrations` 全量迁移测试，通过 11 个测试。

## 后续

- M9-1 继续实现本地管理员 bootstrap、登录、退出、当前用户接口和权限枚举。
- 当前阶段尚未启用接口权限守卫，不影响现有页面和 API。
