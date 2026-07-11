# 2026-07-11 M10-4 豆瓣代理补充源

## 完成内容

- 新增 `DoubanProxyClient`，通过用户配置的 Base URL 调用 `/search`。
- 支持代理请求参数 `query`、`media_type`、`limit`，并按本地解析结果附带 `year`、`season`、`episode`。
- 支持 `items`、`results`、`data` 三种候选数组响应格式。
- 将豆瓣代理候选映射为统一 `MetadataCandidate`，包含标题、年份、简介、评分、海报、类型、演员、导演、IMDb ID 和 TMDB ID。
- 将豆瓣代理接入 Provider registry 和配置级真实连接测试。
- API Key 继续加密保存，列表接口只返回 `has_api_key`。
- 未配置 Base URL 时连接测试直接返回中文失败提示，不发起外部请求。
- 版本号升级到 `0.10.5`。

## 设计边界

- 系统不内置任何第三方豆瓣代理地址。
- 代理服务由用户自行提供，项目只定义后端调用协议和候选映射。
- 连接测试固定使用非用户关键词 `douban`，不提交用户文件名。
- 单个豆瓣代理失败只影响该 Provider，不阻断 TMDB、Bangumi、TVDB 或本地解析。

## 验证记录

- `npm.cmd run backend:test -- --pattern test_douban_proxy_client.py`：通过，272 个测试。

后续仍需完成前端多源 Provider 配置入口和候选字段来源展示。
