# 2026-07-09 M9-1 认证后端基础

## 本次范围

- 在 M9-0 治理表基础上实现认证后端首批能力。
- 不启用现有业务接口权限守卫，避免影响 M1-M8 已有流程。

## 实现内容

- 新增 `POST /api/auth/bootstrap-admin`，仅在用户表为空时创建首个本地管理员。
- 新增 `POST /api/auth/login`，校验用户名和密码后签发访问 token。
- 新增 `POST /api/auth/logout`，吊销当前访问 token。
- 新增 `GET /api/auth/me`，按 Bearer token 返回当前用户和权限列表。
- 新增用户权限枚举，权限直接保存到用户记录，不引入角色模型。
- 密码使用 PBKDF2-SHA256 哈希保存。
- 会话 token 只保存 SHA-256 哈希，接口只在登录时返回明文 token。

## 验证

- 已先新增 `backend.tests.test_auth_api` 并确认路由缺失时失败。
- 已实现认证服务和路由后运行 `backend.tests.test_auth_api`，通过 6 个测试。
- 已运行 `backend.tests.test_database_migrations` 和 `backend.tests.test_auth_api`，通过 17 个测试。

## 后续

- 补前端登录页、当前用户状态和 401 / 403 统一处理。
- M9-2 接入敏感接口权限守卫。
- M9-3 开始把登录、设置保存、重命名和外部提交覆盖写入审计日志。
