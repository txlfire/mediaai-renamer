# 2026-07-09 M9 用户信息维护设置页

## 范围

- 新增 `/api/users` 用户维护接口。
- 在系统设置中新增“用户信息”分类。
- 支持本地用户列表、新增用户、编辑显示名称、启停用户、直接权限勾选和重置临时密码。

## 设计约束

- 不引入角色模型，权限直接挂在用户记录上。
- 用户维护操作复用 `settings:write` 权限。
- 后端阻止移除最后一个启用且具备 `settings:write` 的用户。
- 新增用户和重置密码后标记为需要用户下次登录改密。

## 验证结果

```powershell
npm.cmd run backend:test     # 218 tests passed
npm.cmd run frontend:test    # 14 files, 73 tests passed
npm.cmd run frontend:build   # passed，存在既有 Vite chunk size warning
npm.cmd run check:encoding   # passed
git diff --check             # passed
```
