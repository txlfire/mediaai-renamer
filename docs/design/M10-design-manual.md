# MediaAI Renamer M10 设计手册

版本：草案
阶段：M10 多站点元数据源
更新日期：2026-07-09

## 1. 阶段目标

M10 在 TMDB、IMDb 补充和 AI 候选回填稳定后，补齐多站点元数据源能力。目标不是替代 TMDB，而是让 TMDB 低置信、未匹配或类型不适配的条目，可以按规则调用补充源，并将多源候选统一评分、展示和回填。

目标：

- 建立统一 Metadata Provider 接口。
- 将 TMDB、IMDb 补充、后续 Bangumi、TVDB、豆瓣代理纳入统一编排。
- 支持站点启用、优先级、降级和失败隔离。
- 支持多源候选合并、去重、评分和来源展示。
- 继续复用 M6 敏感词与外部提交保护、M7 匹配来源选择、M8 命名模板版本快照。

## 2. 范围

### 2.1 本阶段实现

- 统一 Metadata Provider 抽象。
- 多源匹配编排器。
- 多源候选缓存和来源记录。
- 站点优先级配置。
- Bangumi 动漫补充源。
- TVDB 剧集补充源。
- 豆瓣代理补充源，代理地址由用户配置。
- 多源候选弹窗和来源标签。
- 多源匹配结果按来源、置信度和字段完整度排序。

### 2.2 本阶段不实现

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

### 4.4 TVDB

- 用于剧集季集信息补充。
- 适合海外剧集、季集结构不完整或 TMDB 分集信息不足时调用。
- 重点补齐 season、episode、episode title 等结构化信息。

### 4.5 豆瓣代理

- 用于国产影视、港台剧和中文别名补充。
- 不内置代理地址，必须由用户配置 Base URL。
- 页面必须提示代理服务由用户自行提供并承担可用性和合规风险。

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
- `raw_data`
- `expires_at`

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

兼容要求：

- 现有 TMDB 单条和批量接口保持可用。
- 多源匹配作为新增入口，不破坏现有 TMDB 匹配按钮逻辑。
- 候选确认回填继续走现有预览回填逻辑。

## 9. 前端设计

系统设置：

- 新增“元数据源”或扩展“刮削设置”。
- 展示 TMDB、IMDb、Bangumi、TVDB、豆瓣代理配置。
- 支持启用、禁用、优先级、Base URL、API Key、超时和连接测试。
- 豆瓣代理必须展示用户自备代理说明。

命名预览：

- 增加多源匹配入口。
- 候选弹窗展示来源标签、字段来源、分数和原因。
- 批量多源匹配前展示站点、数量、敏感词保护和失败隔离提示。

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
