# 2026-07-11 M10-2 Bangumi Provider 工作日志

## 完成范围

- 新增 Bangumi 官方 API 客户端，接入 `POST /v0/search/subjects`。
- Bangumi 启用后由 Provider registry 标记为真实可搜索 Provider。
- 支持匿名搜索和可选 Access Token，Token 从数据库加密字段读取且不进入 API 输出。
- 请求固定过滤动画类型并设置 `nsfw=false`。
- 支持配置 Base URL、超时、重试次数和项目 User-Agent。
- 对网络错误和 HTTP 5xx 执行有限重试；鉴权失败、频率限制和无效 JSON 返回中文错误。
- 将中文标题、原始标题、年份、简介、评分、海报和标签映射为统一 `MetadataCandidate`。
- 在候选 `raw_data` 中记录 `match_basis` 和 `match_reason`，多源摘要同步返回命中原因。
- Bangumi 配置测试接口升级为真实连接测试，返回状态、中文消息和响应耗时。
- Bangumi Base URL 限制为官方 HTTPS 主机，避免可选 Token 被转发到第三方地址。
- 连接测试增加 `settings:write` 权限守卫，配置接口支持 `clear_api_key=true` 显式清除 Token。
- 响应端再次过滤 NSFW 条目，网络/5xx 重试增加短指数退避。
- 修复完整回归发现的短词 `AV` 子串误判：独立 `AV` 路径仍拦截，`save` 等普通词不再误伤。

## 兼容性

- 现有 TMDB 单源接口和调用逻辑未修改。
- 多源入口仍只缓存候选，不自动回填目标文件名。
- Bangumi 失败由现有编排器隔离，不阻断 TMDB 或其他 Provider。
- 外部提交保护仍在构建 Provider registry 和发起外部请求之前执行。
- Bangumi 搜索是作品级结果，不把本地 season、episode 伪装为站点返回字段。
- TVDB、豆瓣代理真实搜索和前端多源入口仍未实现。
- 当前凭据密文格式沿用项目历史兼容实现；强加密与旧密文迁移需在后续安全阶段统一处理。

## 测试

- 新增 Bangumi 请求参数、User-Agent、Bearer Token、重试、鉴权错误和候选映射测试。
- 新增 Provider registry 密钥解密与真实可用状态测试。
- 新增 Bangumi 连接测试成功、失败和响应时间测试。
- 新增 Bangumi 匹配原因进入多源摘要测试。
- 更新批量多源测试，使用 Fake Bangumi，避免自动化测试访问公网。
- `npm.cmd run backend:test`：258 个测试通过，0 失败。

## 环境说明

- 当前执行环境使用 `curl.exe` 直连 `api.bgm.tv:443` 时曾出现连接超时，因此没有把本机真实联网结果作为完成依据。
- 请求协议和字段映射依据 Bangumi 官方 OpenAPI 与官方 User-Agent 规则实现；运行环境的实际连通性可通过 `POST /api/settings/metadata-providers/bangumi/test` 验证。

## 版本

- 项目版本由 `0.10.2` 升级为 `0.10.3`。
