# 2026-07-09 M9 默认管理员密码与改密提示

## 范围

- 在 `config/config.toml` 和 `config/config.example.toml` 中新增隐藏认证配置。
- 支持空用户库按配置创建默认 `admin / 123456`。
- 默认 admin 登录后标记为必须修改密码，用户不修改时下次登录继续提示。
- 新增当前用户修改密码接口和隐藏 admin 密码重置接口。

## 设计约束

- 测试环境的 `AppSettings()` 默认不自动创建 admin，保留旧库空用户兼容路径。
- admin 密码重置接口默认关闭，必须显式设置 `admin_password_reset_enabled = true` 才能使用。
- 用户主动设置的新密码不能继续使用默认密码。

## 验证结果

```powershell
npm.cmd run backend:test     # 215 tests passed
npm.cmd run frontend:test    # 14 files, 73 tests passed
npm.cmd run frontend:build   # passed，存在既有 Vite chunk size warning
npm.cmd run check:encoding   # passed
git diff --check             # passed
```
