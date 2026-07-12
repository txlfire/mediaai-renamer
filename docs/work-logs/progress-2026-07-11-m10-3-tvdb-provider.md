# 2026-07-11 M10-3 TVDB Provider 工作日志

## 范围

- 修正任务治理列表操作列第三个按钮图标，统一为 Element Plus 图标体系。
- 新增 TVDB 官方 API 客户端，接入 `/login`、`/search` 和 `/series/{id}/episodes/default`。
- 将 TVDB 接入 Metadata Provider registry。
- TVDB 配置测试升级为真实连接测试。
- 同步版本号到 `0.10.4`，更新 M10 设计、计划、验收清单和用户手册。

## 实现要点

- TVDB API Key 通过现有 `metadata_provider_configs.api_key_encrypted` 加密保存，列表接口只返回 `has_api_key`。
- TVDB Base URL 固定规范化为官方 `https://api4.thetvdb.com/v4`，避免 API Key 被发送到第三方主机。
- TVDB 搜索只处理剧集类条目；电影条目不调用 TVDB。
- 有 season 和 episode 时，TVDB 会补充分集标题，并写入 `raw_data.episode_title`。
- TVDB 候选写入 `match_basis` 和 `match_reason`，供后续前端展示来源和原因。
- TVDB 失败由现有多源编排器隔离，不阻断 TMDB、Bangumi 或本地解析结果。

## 测试

- 新增 TVDB 客户端测试，覆盖登录、Bearer 请求、series 搜索、分集补齐和缺少 API Key。
- 扩展 Provider registry 测试，验证启用 TVDB 后进入真实 Provider。
- 扩展元数据源配置服务测试，验证 TVDB 真实连接测试和官方 Base URL 校验。

## 后续

- M10-4 继续实现豆瓣代理 Provider。
- M10-5 继续补前端多源配置入口、候选字段来源展示和批量确认提示。
