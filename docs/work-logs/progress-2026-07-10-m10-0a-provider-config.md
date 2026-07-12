# 2026-07-10 M10-0A 元数据源配置基础

## 结论

M10-0A 已完成后端元数据源配置基础。当前只提供配置表、配置接口、脱敏保存和配置级测试，不发起 Bangumi、TVDB 或豆瓣代理真实外部请求。

## 变更范围

- 数据库 schema version 提升到 `18`。
- 新增 `metadata_provider_configs` 表。
- 默认种子 Provider：
  - `tmdb`
  - `imdb`
  - `bangumi`
  - `tvdb`
  - `douban_proxy`
- 新增 `metadata_provider_service`：
  - 列表查询
  - 单项保存
  - API Key 加密保存和脱敏输出
  - 配置级测试
- 新增设置接口：
  - `GET /api/settings/metadata-providers`
  - `PUT /api/settings/metadata-providers/{provider}`
  - `POST /api/settings/metadata-providers/{provider}/test`
- 版本号升级到 `0.10.0`，表示进入 M10 开发线。

## 边界

- 本阶段不实现真实 Provider 搜索。
- 豆瓣代理不内置任何第三方地址。
- 豆瓣代理未配置 Base URL 时返回失败，不发起请求。
- M10-0B 继续实现 Provider registry 和多源编排接入点。

## 验证

- 已补充数据库迁移和 settings API 针对性测试。
- 完整验证在提交前执行。
