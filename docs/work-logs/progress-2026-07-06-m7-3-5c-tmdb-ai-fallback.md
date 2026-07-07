# M7-3.5C TMDB 后备 AI 串联匹配

日期：2026-07-06

版本：v0.7.9

## 本次范围

- 新增后端复合匹配接口：
  - `POST /api/rename-previews/metadata-match/ai-fallback`
  - `POST /api/rename-previews/metadata-match/all/ai-fallback`
- 后端执行顺序为先 TMDB，再对 `failed`、`low_confidence`、`blocked` 的条目执行 AI 解析。
- AI 后备结果只返回候选和用量汇总，不自动回填目标文件名。
- 重命名页新增匹配模式：
  - 仅 TMDB
  - 仅 AI
  - TMDB→AI
- TMDB→AI 模式执行前展示外部提交确认，说明 TMDB 提交、AI 后备提交、敏感词保护、Token 成本和不自动真实重命名。
- 前端对 AI 后备候选进行缓存，用户可通过行内 AI 操作逐条查看和回填。

## 验收更新

- 更新 `docs/development/m7/M7-增量扫描验收清单.md` 中批量 AI、TMDB 后备 AI、结果摘要和 Token 用量相关条目。

## 验证

- `npm.cmd run backend:test -- test_rename_previews.py`
- `npm.cmd run frontend:test`
- `npm.cmd run frontend:build`

## 后续

- AI Provider 多模型管理和一键切换仍未完成，作为 M7 剩余收尾项处理。
- M7-4 需要执行完整回归、更新用户手册和阶段收尾报告。

