# 2026-07-09 M9-2 用户权限守卫

## 本次范围

- 将 M9 权限模型从角色改为用户直接权限。
- 接入首批敏感写接口权限守卫。
- 保持旧实例兼容：用户表为空时允许原有写接口继续使用，避免管理员初始化前被锁死。

## 实现内容

- `users` 表使用 `permissions_json` 保存用户权限列表，不再新增 `role` 字段。
- 认证响应只返回当前用户和 `permissions`，不返回角色。
- 新增通用 `require_permission(permission)` 依赖。
- `PUT /api/settings` 需要 `settings:write`。
- 媒体源新增、编辑、启停、删除和批量删除需要 `source:write`。
- 扫描任务创建需要 `scan:run`。
- TMDB / AI 外部提交接口需要 `metadata:submit`。
- 外部提交拦截记录覆盖需要 `metadata:submit`。
- 真实重命名执行需要 `rename:execute`。
- 初始化用户后，未登录访问已接入守卫的写接口返回 401；权限不足返回 403。

## 验证

- 已先新增 `backend.tests.test_permission_guard` 并确认未接守卫时失败。
- 已实现权限守卫后运行 `backend.tests.test_permission_guard`，通过 7 个测试。
- 已运行迁移、认证和权限守卫组合测试，通过 20 个测试。

## 后续

- 回滚能力落地后继续接入 `rollback:execute`。
- 前端登录页和全局 401 / 403 处理仍待实现。
