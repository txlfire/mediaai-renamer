# 2026-07-04 M7-3.5A 批量 AI 解析后端基础

## 本次范围

- 新增重命名预览批量 AI 结构化解析服务，复用现有单条 AI 解析能力。
- 新增 `POST /api/rename-previews/ai-parse/batch` 接口，返回每条预览的解析结果和批量汇总。
- 批量接口仅返回候选数据，不直接修改预览记录，不自动回填目标文件名。
- 聚合 AI 用量中的数值字段，便于后续前端展示 token 消耗提示。
- 增加后端 API 测试，覆盖批量成功、统计汇总、用量汇总和密钥不泄露。

## 接口摘要

请求：

```http
POST /api/rename-previews/ai-parse/batch
Content-Type: application/json
```

```json
{
  "rename_preview_ids": [1, 2]
}
```

响应包含：

- `total_count`：请求去重后的总数
- `success_count`：AI 解析成功数
- `failed_count`：异常或失败数
- `blocked_count`：被外部提交保护阻断数
- `skipped_count`：AI 未启用或配置不完整导致跳过数
- `usage`：聚合后的 AI 用量数值字段
- `items`：每条预览的 AI 解析结果
- `failed_items`：不存在或异常记录

## 验证

- `npm.cmd run backend:test`：通过，167 个测试全部成功。

## 后续

- 前端接入批量 AI 匹配按钮、确认提示、进度与结果摘要。
- 增加 TMDB 未匹配时自动转 AI 的链路编排。
- 在前端展示 token 用量与外部提交风险提示。
