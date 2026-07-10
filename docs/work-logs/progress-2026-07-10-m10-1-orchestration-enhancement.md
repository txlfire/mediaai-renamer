# 2026-07-10 M10-1 多源匹配编排增强

## 本次范围

- 增强多源候选结构，写入：
  - `merge_key`
  - `source_provider`
  - `source_label`
  - `source_priority`
  - `field_completeness`
  - `field_sources`
- 新增多源候选去重逻辑，同一 `merge_key` 下保留字段更完整、来源优先级更高的候选。
- 增强批量多源匹配摘要：
  - `skipped_count`
  - `provider_success_count`
  - `provider_failed_count`
  - `provider_skipped_count`
- 保持现有 TMDB 单源匹配接口不变。

## 边界说明

- 本次仍不接入真实 Bangumi、TVDB、豆瓣代理外部请求。
- 豆瓣代理仍只允许用户自备 Base URL，不内置第三方地址。
- 候选确认回填仍沿用现有命名模板构建器。

## 验证

```powershell
$env:PYTHONPATH='backend'; python -m unittest backend.tests.test_metadata_multi_source_service -v
$env:PYTHONPATH='backend'; python -m unittest backend.tests.test_rename_previews.RenamePreviewApiTest.test_batch_metadata_multi_match_reports_provider_summary -v
```

目标测试已通过，完整验证以提交前命令为准。
