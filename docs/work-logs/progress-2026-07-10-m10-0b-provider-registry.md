# 2026-07-10 M10-0B Provider registry 与多源匹配入口

## 本次范围

- 新增 M10 元数据源 Provider registry。
- 新增多源匹配编排服务，支持 `primary_only`、`fallback`、`parallel`。
- 新增重命名预览多源匹配 API：
  - `POST /api/rename-previews/{preview_id}/metadata-multi-match`
  - `POST /api/rename-previews/metadata-multi-match/batch`
- 多源入口复用外部提交保护，命中敏感词时不查询外部 Provider。
- 新入口仅缓存候选和状态，不自动回填目标文件名。

## 边界说明

- TMDB 仍沿用现有 TMDB 配置，保持原单源匹配接口不变。
- Bangumi、TVDB、豆瓣代理本次仅进入 registry 和编排模型，真实外部请求留到后续子阶段。
- 豆瓣代理仍不内置第三方地址。

## 验证

```powershell
$env:PYTHONPATH='backend'; python -m unittest backend.tests.test_metadata_multi_source_service -v
$env:PYTHONPATH='backend'; python -m unittest backend.tests.test_rename_previews.RenamePreviewApiTest.test_metadata_multi_match_api_caches_candidates_without_backfilling -v
```

以上目标测试均通过。
