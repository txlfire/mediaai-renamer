# 登录记忆与免登录配置设计

## 目标

登录页支持浏览器原生保存密码，并提供固定文案“一周免登录”。免登录实际时长由管理员在系统设置中配置，默认 7 天。

## 交互设计

- 登录表单增加“记住密码”和“一周免登录”两个独立复选框。
- “记住密码”只控制标准 HTML 自动填充语义，不在应用数据库、localStorage 或 sessionStorage 中保存明文密码。
- “一周免登录”文案固定，不向登录用户暴露管理员配置的实际时长。
- 未勾选“一周免登录”时，会话有效期为 24 小时，Token 写入 sessionStorage。
- 勾选“一周免登录”时，会话有效期读取 `auth.remember_login_days`，Token 写入 localStorage。

## 后端设计

- `POST /api/auth/login` 增加 `rememberLogin` 布尔参数，默认 `false`，保持旧客户端兼容。
- 新增长会话热配置 `auth.remember_login_days`，默认 7，范围 1 至 30。
- 登录服务根据参数选择 24 小时或配置天数计算 `expiresAt`。
- 审计日志只记录用户名和长会话选择，不记录密码或 Token。
- 配置继续通过现有 `settings:write` 权限保护，仅管理员可查看和修改。

## 前端设计

- 用户名输入框使用 `name="username"` 和 `autocomplete="username"`。
- 密码输入框使用 `name="password"`；勾选“记住密码”时使用 `autocomplete="current-password"`，否则使用 `autocomplete="off"`。
- API 客户端同时读取 sessionStorage 和 localStorage；短会话写入 sessionStorage，长会话写入 localStorage。
- 登录、退出登录和 401 失效处理统一清理两处 Token。
- 兼容已有 localStorage Token，不迁移也不主动失效。

## 安全与边界

- 浏览器是否弹出保存密码提示由浏览器或密码管理器策略决定。
- 配置变化只影响后续新登录会话，不修改已签发会话的到期时间。
- 普通用户没有 `settings:write` 权限时，无法读取敏感系统设置详情或保存配置。

## 验证

- 后端覆盖默认短会话、默认 7 天长会话、管理员修改时长、非法范围和旧请求兼容。
- 前端覆盖 Token 双存储、登录请求参数、退出清理和登录页自动填充属性。
- 运行后端认证与设置测试、前端 API/Store 测试、前端构建及编码检查。
