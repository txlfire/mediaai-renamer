# MediaAI Renamer M10 设计手册

版本：0.10.7
阶段：M10 多站点元数据源
更新日期：2026-07-11

## 1. 阶段目标

M10 在 TMDB、IMDb 补充和 AI 候选回填稳定后，补齐多站点元数据源能力。目标不是替代 TMDB，而是让 TMDB 低置信、未匹配或类型不适配的条目，可以按规则调用补充源，并将多源候选统一评分、展示和回填。

目标：

- 建立统一 Metadata Provider 接口。
- 将 TMDB、IMDb 补充、后续 Bangumi、TVDB、豆瓣代理纳入统一编排。
- 支持站点启用、优先级、降级和失败隔离。
- 支持多源候选合并、去重、评分和来源展示。
- 继续复用 M6 敏感词与外部提交保护、M7 匹配来源选择、M8 命名模板版本快照。

## 2. 范围

### 2.1 M10 总体实现目标

- 统一 Metadata Provider 抽象。
- 多源匹配编排器。
- 多源候选缓存和来源记录。
- 站点优先级配置。
- Bangumi 动漫补充源。
- TVDB 剧集补充源。
- 豆瓣代理补充源，代理地址由用户配置。
- 多源候选弹窗和来源标签。
- 多源匹配结果按来源、置信度和字段完整度排序。

### 2.2 当前已落地范围

截至 `0.10.7`：

- 已新增 `metadata_provider_configs` 配置表。
- 已提供元数据源配置 API，支持启用、禁用、优先级、Base URL、API Key、超时和重试。
- 已提供配置级测试接口，豆瓣代理未配置 Base URL 时不会发起连接测试。
- 已抽象 Provider registry，TMDB 接入现有配置，IMDb、Bangumi、TVDB、豆瓣代理进入统一配置和编排模型。
- 已新增重命名预览多源匹配后端入口：
  - `POST /api/rename-previews/{preview_id}/metadata-multi-match`
  - `POST /api/rename-previews/metadata-multi-match/batch`
- 已支持 `primary_only`、`fallback`、`parallel` 三种编排模式。
- 已支持单 Provider 失败隔离、Provider 执行结果摘要和批量摘要。
- 已为候选写入 `merge_key`、`source_priority`、`field_completeness`、`field_sources`。
- 已支持多源候选按去重键合并，优先保留字段更完整、来源优先级更高的候选。
- 已复用外部提交保护，命中敏感词时不调用 Provider。
- 已接入 Bangumi 官方 `POST /v0/search/subjects` 搜索接口，仅搜索动画条目并显式排除 NSFW。
- Bangumi 支持匿名访问和可选 Bearer Token，Token 仅在后端解密使用。
- Bangumi 候选已映射中文标题、原始标题、年份、简介、评分、海报、标签和匹配原因。
- Bangumi 支持独立超时、网络/5xx 重试、中文错误分类和真实连接测试。
- Bangumi Token 仅允许发送到官方 HTTPS 主机，连接测试要求系统设置写权限。
- 元数据源配置支持通过 `clear_api_key=true` 显式清除已有 Token。
- 已接入 TVDB 官方 API，使用 `/login` 换取 Bearer Token，并通过 `/search` 搜索 series。
- TVDB 支持剧集候选映射、季集号补充和 episode title 写入 `raw_data`。
- TVDB API Key 仅在后端解密使用，Base URL 固定规范化为官方 `https://api4.thetvdb.com/v4`。
- TVDB 连接测试走真实官方搜索，并返回中文成功或失败信息。
- 已接入用户自备豆瓣代理，调用 `{base_url}/search` 并按统一候选结构映射标题、年份、简介、评分、海报、类型、演员、导演和外部 ID。
- 豆瓣代理连接测试走真实代理搜索，固定使用非用户关键词 `douban`，未配置 Base URL 时不发起请求。
- 豆瓣代理 API Key 仅在后端解密使用，请求时通过 `Authorization: Bearer ...` 发送到用户配置的代理 Base URL。
- 系统设置“刮削设置”已接入补充元数据源卡片，支持 Bangumi、TVDB、豆瓣代理配置维护和连接测试。
- 重命名页已接入多源匹配入口，支持选中行和当前列表批量匹配。
- 候选弹窗已展示候选来源和 `field_sources` 字段来源。
- 批量多源匹配前已展示数量、敏感词保护和外部提交风险说明。

### 2.3 当前未落地范围

- 多源匹配任务表 `metadata_match_jobs` 尚未创建。
- 多源候选缓存仍复用重命名预览现有 `metadata_candidates_json`，尚未扩展独立缓存字段。

### 2.4 本阶段不实现

- 不抓取未授权网页，不绕过站点限制。
- 不内置第三方豆瓣代理地址。
- 不做媒体库管理器入库。
- 不做多站点账号共享和凭据市场。
- 不做 WebDAV、FTP、SFTP、S3 远程协议；这些仍属于 M11。
- 不让外部站点直接改真实文件。

## 3. Provider 设计

建议统一接口：

```text
search(query, context) -> MetadataCandidate[]
get_detail(provider_id, media_type, context) -> MetadataCandidate
test_connection(settings) -> ProviderTestResult
```

Provider 输出字段保持和当前候选结构兼容：

- `provider`
- `provider_id`
- `media_type`
- `title`
- `original_title`
- `localized_title`
- `chinese_title`
- `english_title`
- `year`
- `season`
- `episode`
- `overview`
- `score`
- `match_status`
- `match_reason`
- `raw_data`

新增建议字段：

- `source_priority`：站点优先级。
- `source_confidence`：站点自身置信度。
- `field_completeness`：字段完整度。
- `merge_key`：多源去重键。
- `field_sources`：候选字段来源。

当前实现中，`source_priority`、`field_completeness`、`merge_key`、`field_sources` 写入候选 `raw_data`；Bangumi 还写入 `match_basis` 和 `match_reason`，供后续前端展示。

## 4. 站点定位

### 4.1 TMDB

- 继续作为默认主站。
- V4 优先、V3 降级逻辑保持不变。
- 仍是电影、海外剧和通用影视的首选来源。

### 4.2 IMDb 补充

- 继续作为已有补充能力。
- 主要用于补充外文标题、IMDb ID 和跨源识别。

### 4.3 Bangumi

- 用于动漫、番剧、日漫相关条目的补充匹配。
- 仅在用户启用后调用。
- 适合 TMDB 未匹配、低置信或用户选择动漫类型时调用。
- 使用官方 `POST /v0/search/subjects`，固定过滤动画类型 `2` 并设置 `nsfw=false`。
- Access Token 可选；配置后使用 Bearer Header，列表接口不返回明文。
- Base URL 固定规范化为官方 `https://api.bgm.tv`，避免 Token 被转发到第三方主机。
- 搜索返回的是作品级条目，不伪造 Bangumi 未提供的 season、episode 字段。
- 非浏览器请求使用包含项目标识、版本和项目主页的 User-Agent。
- 请求和响应两端都排除 NSFW 条目；网络错误和 5xx 重试使用短指数退避。

### 4.4 TVDB

- 用于剧集季集信息补充。
- 适合海外剧集、季集结构不完整或 TMDB 分集信息不足时调用。
- 重点补齐 season、episode、episode title 等结构化信息。
- 仅在用户启用并配置 API Key 后调用。
- 使用官方 `POST /login` 获取有效期约一个月的 Bearer Token，并在后续请求中发送 `Authorization: Bearer ...`。
- 使用官方 `GET /search?query=...&type=series` 搜索剧集候选。
- 当本地解析已有 season 和 episode 时，调用 `GET /series/{id}/episodes/default` 精确补集标题。
- Base URL 固定规范化为官方 `https://api4.thetvdb.com/v4`，避免 API Key 被转发到第三方主机。

### 4.5 豆瓣代理

- 用于国产影视、港台剧和中文别名补充。
- 不内置代理地址，必须由用户配置 Base URL。
- 页面必须提示代理服务由用户自行提供并承担可用性和合规风险。
- 后端调用统一代理协议：`GET {base_url}/search?query=...&media_type=...&limit=...`，可附带 `year`、`season`、`episode`。
- 代理响应支持 `items`、`results` 或 `data` 数组，数组元素会映射为统一 `MetadataCandidate`。
- API Key 可选；配置后只随代理请求发送到用户配置的 Base URL。

## 5. 编排策略

多源匹配建议分三种模式：

- `primary_only`：只使用 TMDB。
- `fallback`：TMDB 未匹配或低置信时调用补充源。
- `parallel`：对用户明确选择的条目并行调用多个启用站点。

默认建议：

- 普通电影和剧集：`fallback`。
- 用户手动选择“多源搜索”：`parallel`。
- 批量操作：默认 `fallback`，并显示预计调用站点和数量。

失败隔离：

- 单个站点失败不阻断其他站点。
- 站点超时只记录该站点失败原因。
- 所有站点失败时保留本地解析结果。

## 6. 候选合并与评分

候选合并规则：

- 同一站点同一 `provider_id` 去重。
- 不同站点通过标题、年份、季集、外部 ID 和相似度生成 `merge_key`。
- 合并时保留所有来源，不丢失原始候选。

排序维度：

- 标题相似度。
- 年份匹配。
- 媒体类型匹配。
- 季集匹配。
- 站点优先级。
- 字段完整度。
- 用户选择的匹配来源。

前端必须展示：

- 候选来源。
- 分数和原因。
- 哪些字段来自哪个站点。
- 低置信提示。

## 7. 数据设计

建议新增或扩展：

### 7.1 `metadata_provider_configs`

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| provider | `tmdb` / `imdb` / `bangumi` / `tvdb` / `douban_proxy` |
| enabled | 是否启用 |
| priority | 优先级 |
| base_url | 站点或代理地址 |
| api_key_encrypted | 加密密钥，可空 |
| timeout_seconds | 超时时间 |
| max_retries | 重试次数 |
| created_at / updated_at | 创建和更新时间 |

### 7.2 `metadata_candidate_cache`

当前已有候选缓存基础，M10 可扩展：

- `provider`
- `provider_id`
- `merge_key`
- `source_priority`
- `field_completeness`
- `field_sources`
- `raw_data`
- `expires_at`

截至 `0.10.7`，多源候选仍缓存在 `rename_previews.metadata_candidates_json` 中，未新增独立候选缓存字段。

### 7.3 `metadata_match_jobs`

如批量多源匹配需要更完整任务追踪，可新增轻量任务表：

- `id`
- `media_source_id`
- `match_mode`
- `provider_list`
- `status`
- `total_count`
- `success_count`
- `failed_count`
- `blocked_count`
- `created_at`
- `updated_at`

## 8. API 设计

```http
GET /api/settings/metadata-providers
PUT /api/settings/metadata-providers/{provider}
POST /api/settings/metadata-providers/{provider}/test
POST /api/rename-previews/{preview_id}/metadata-multi-match
POST /api/rename-previews/metadata-multi-match/batch
GET /api/metadata-match-jobs/{job_id}
```

当前已实现：

```http
GET /api/settings/metadata-providers
PUT /api/settings/metadata-providers/{provider}
POST /api/settings/metadata-providers/{provider}/test
POST /api/rename-previews/{preview_id}/metadata-multi-match
POST /api/rename-previews/metadata-multi-match/batch
```

当前未实现：

```http
GET /api/metadata-match-jobs/{job_id}
```

兼容要求：

- 现有 TMDB 单条和批量接口保持可用。
- 多源匹配作为新增入口，不破坏现有 TMDB 匹配按钮逻辑。
- 候选确认回填继续走现有预览回填逻辑。

## 9. 前端设计

系统设置：

- 已扩展“刮削设置”。
- 展示 TMDB、IMDb、Bangumi、TVDB、豆瓣代理配置。
- 支持启用、禁用、优先级、Base URL、API Key、超时和连接测试。
- 豆瓣代理必须展示用户自备代理说明。

命名预览：

- 增加多源匹配入口。
- 候选弹窗展示来源标签、字段来源、分数和原因。
- 批量多源匹配前展示站点、数量、敏感词保护和失败隔离提示。

截至 `0.10.7`，以上前端入口和候选展示已接入；“当前列表全部多源匹配”基于前端当前已加载列表执行，不等同于后端全库 all 接口。

## 10. 验收标准

- TMDB 现有流程不回退。
- 启用 Bangumi 后，动漫条目可走补充匹配。
- 启用 TVDB 后，剧集条目可补充分季分集信息。
- 启用豆瓣代理后，国产影视可使用用户配置代理补充候选。
- 豆瓣代理未配置 Base URL 时，不发起请求并给出明确提示。
- 命中敏感词时，多源匹配不得提交任何外部站点。
- 单站点失败不影响其他站点返回候选。
- 候选确认回填后仍按当前命名模板生成目标名，并记录模板版本快照。

## 11. 风险和处理

- 不同站点字段语义不一致：保留来源和字段来源，避免隐式覆盖。
- 豆瓣代理可用性不可控：用户配置化，并在 UI 中明确责任边界。
- 多源并行可能增加网络成本：批量执行前明确站点和数量。
- 站点 API 限流：每个 Provider 独立超时、重试和失败记录。
- 通用凭据保护：当前沿用项目历史兼容密文格式，后续统一设计版本化强加密和旧密文迁移，避免单个 Provider 形成不兼容分叉。
