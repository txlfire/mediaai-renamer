# MediaAI Renamer M10 用户手册

版本：`0.10.6`
覆盖范围：M10 元数据源配置、多源匹配编排、Bangumi、TVDB、豆瓣代理和前端多源入口
更新日期：2026-07-11

## 1. 功能范围

M10 的目标是把 TMDB、IMDb、后续 Bangumi、TVDB、豆瓣代理纳入统一的元数据源配置和匹配编排模型。

截至 `0.10.6`，当前已落地：

- 元数据源配置表和配置接口。
- TMDB、IMDb、Bangumi、TVDB、豆瓣代理统一配置模型。
- Provider registry。
- 多源匹配后端入口。
- `primary_only`、`fallback`、`parallel` 三种编排模式。
- 多源候选去重键、字段完整度、来源优先级和字段来源记录。
- 批量多源匹配 Provider 摘要。
- 敏感词和外部提交保护复用。
- Bangumi 动画条目真实搜索、连接测试和统一候选映射。
- TVDB 剧集条目真实搜索、连接测试、季集信息和 episode title 补充。
- 用户自备豆瓣代理真实搜索、连接测试和统一候选映射。
- 系统设置“刮削设置”中的补充元数据源配置卡片。
- 重命名页面多源匹配按钮、匹配模式选择和批量确认提示。
- 多源候选弹窗中的来源标签和字段来源展示。

## 2. 元数据源配置

后端已提供统一配置接口：

```http
GET /api/settings/metadata-providers
PUT /api/settings/metadata-providers/{provider}
POST /api/settings/metadata-providers/{provider}/test
```

支持的 Provider：

- `tmdb`
- `imdb`
- `bangumi`
- `tvdb`
- `douban_proxy`

配置字段：

- `enabled`：是否启用。
- `priority`：优先级，数值越小优先级越高。
- `base_url`：站点或代理地址。
- `api_key`：API Key 或 Token，保存时加密。
- `clear_api_key`：设为 `true` 时清除已保存密钥；空 `api_key` 仍表示保留原值。
- `timeout_seconds`：超时时间。
- `max_retries`：最大重试次数。

安全规则：

- 列表接口只返回 `has_api_key`，不返回明文密钥。
- 空 API Key 或掩码值不会覆盖已有密钥。
- 豆瓣代理不内置第三方地址，必须由用户自行配置 Base URL。
- 豆瓣代理未配置 Base URL 时，测试接口返回失败提示且不发起连接测试。
- 豆瓣代理配置 API Key 后，仅随请求发送到用户配置的代理 Base URL。
- Bangumi Access Token 可不配置；配置后加密保存，只在后端请求时解密。
- Bangumi 搜索固定排除 NSFW 条目。
- Bangumi Base URL 只接受官方 `https://api.bgm.tv`，防止 Token 被发送到第三方主机。
- TVDB API Key 必填；TVDB Base URL 只接受官方 `https://api4.thetvdb.com/v4`，防止密钥被发送到第三方主机。
- Provider 连接测试要求当前用户具有 `settings:write` 权限。

前端入口：

- 进入“系统设置”。
- 打开“刮削设置”。
- 在“补充元数据源”卡片中维护 Bangumi、TVDB、豆瓣代理。
- Bangumi 和 TVDB Base URL 固定为官方地址；豆瓣代理 Base URL 由用户自行填写。
- 每个 Provider 可单独保存和测试连接。

Bangumi 当前行为：

- 仅在 `bangumi.enabled=true` 时进入多源编排。
- 使用官方动画条目搜索接口，支持中文标题和原始标题候选。
- 返回年份、简介、评分、海报和标签等官方可用字段。
- 作品级搜索不伪造 season、episode，季集仍以本地解析结果和后续补充源为准。
- 网络失败、鉴权失败或频率限制只影响 Bangumi，不会中断 TMDB 或其他站点。

TVDB 当前行为：

- 仅在 `tvdb.enabled=true` 且已配置 API Key 时进入多源编排。
- 使用官方 `/login` 以 API Key 换取 Bearer Token，再调用 `/search` 查询 series。
- 对带有季集信息的剧集，会继续调用 `/series/{id}/episodes/default` 按 season 和 episodeNumber 补充 episode title。
- TVDB 候选会写入 `season`、`episode`、`raw_data.episode_title` 和匹配原因。
- 网络失败、鉴权失败或频率限制只影响 TVDB，不会中断 TMDB 或其他站点。

豆瓣代理当前行为：

- 仅在 `douban_proxy.enabled=true` 且已配置 Base URL 时进入多源编排。
- 调用用户配置的 `{base_url}/search`，请求参数包含 `query`、`media_type`、`limit`，并在本地解析存在时附带 `year`、`season`、`episode`。
- 代理响应支持 `items`、`results` 或 `data` 数组。
- 候选会映射标题、原始标题、年份、简介、评分、海报、类型、演员、导演、IMDb ID 和 TMDB ID。
- 连接测试使用固定关键词 `douban`，不会提交用户文件名。
- 网络失败、鉴权失败或频率限制只影响豆瓣代理，不会中断 TMDB 或其他站点。

## 3. 多源匹配模式

后端多源匹配支持三种模式：

- `primary_only`：只使用当前优先级最高的可用主站。
- `fallback`：优先使用主站，高置信命中后停止；低置信或失败时继续补充源。
- `parallel`：对启用的 Provider 逐个执行查询，并隔离单站点失败。

当前 API：

```http
POST /api/rename-previews/{preview_id}/metadata-multi-match
POST /api/rename-previews/metadata-multi-match/batch
```

当前后端入口只缓存候选和状态，不自动回填目标文件名。用户仍需要在后续候选确认流程中选择回填，回填后继续按当前命名模板生成目标名。

前端使用：

- 在重命名页面选择匹配来源：解析标题、原文件名或上级文件夹标题。
- 选择多源匹配模式：低置信后补充或并行搜索。
- 点击“多源匹配”对选中行执行匹配。
- 通过下拉菜单选择“当前列表全部多源匹配”对当前已加载列表执行批量匹配。
- 操作前会弹出确认，说明外部提交、敏感词保护和不会真实重命名。

## 4. 候选治理

M10 多源候选会在 `raw_data` 中写入以下信息：

- `source_provider`：来源 Provider。
- `source_label`：来源显示名称。
- `source_priority`：来源优先级。
- `field_completeness`：字段完整度。
- `merge_key`：跨源去重键。
- `field_sources`：字段来源映射。

候选排序会综合：

- 标题、年份、类型、季集匹配分。
- 字段完整度。
- Provider 优先级。

当多个候选疑似同一作品时，后端按 `merge_key` 去重，保留字段更完整、优先级更高的候选。

候选弹窗会展示：

- 候选来源。
- 匹配分数和状态。
- 标题、英文标题、年份、ID、评分、语言、类型、演员、导演、海报和概述。
- 展开行中的字段来源和原始数据。

## 5. 批量摘要

批量多源匹配返回：

- `total_count`：处理条目数。
- `success_count`：条目级成功数。
- `failed_count`：条目级失败数。
- `blocked_count`：外部提交保护拦截数。
- `skipped_count`：跳过数。
- `provider_success_count`：Provider 执行成功次数。
- `provider_failed_count`：Provider 执行失败次数。
- `provider_skipped_count`：Provider 跳过次数。

每条结果还包含 `provider_results`，用于显示每个 Provider 的执行状态和候选数量。

## 6. 外部提交保护

多源匹配复用 M6 的敏感词和外部提交保护：

- 命中文件名敏感词时不提交 Provider。
- 命中路径敏感词时不提交 Provider。
- 命中解析标题敏感词时不提交 Provider。
- 被拦截的记录进入外部提交拦截列表。

## 7. 当前限制

- “当前列表全部多源匹配”基于前端当前已加载的列表执行，不等同于后端按筛选条件扫描全库。
- 多源匹配只缓存候选和状态，不自动修改真实文件名。
- `metadata_match_jobs` 任务追踪表尚未实现。
- 多源候选暂时复用重命名预览的候选缓存字段。
