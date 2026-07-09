# 2026-07-09 M9 用户权限基础与版本收尾

## 范围

- 将开发版本从 `0.8.0` 升级到 `0.8.1`。
- 保留最近正式发布版本为 `v0.8.0`，避免将未完整验收的 M9 标记为正式 `v0.9.0`。
- 同步 README 和 M9 开发计划中的当前阶段说明。

## 已完成

- M9-0：数据库 schema version 提升到 `14`，补齐用户、会话、审计和回滚治理表。
- M9-1：完成本地管理员初始化、登录、退出、当前用户、权限枚举和前端登录态联动。
- M9-2：完成敏感接口权限守卫和前端高风险操作权限禁用。

## 验证结果

```powershell
npm.cmd run backend:test     # 210 tests passed
npm.cmd run frontend:test    # 14 files, 71 tests passed
npm.cmd run frontend:build   # passed，存在既有 Vite chunk size warning
npm.cmd run check:encoding   # passed
git diff --check             # passed
```
