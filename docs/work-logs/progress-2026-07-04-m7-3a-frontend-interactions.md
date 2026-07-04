# M7-3A 前端交互闭环

日期：2026-07-04

版本：0.7.4

## 范围

- 扫描任务页新增全量扫描 / 增量扫描模式选择。
- 媒体源选择后读取后端扫描模式建议，并默认选中推荐模式。
- 扫描任务列表展示扫描模式以及新增、变更、跳过、缺失统计。
- 重命名预览列表新增单条排除和批量排除入口，排除后进入待处理文件列表。
- 重命名预览展示标题来源和上级文件夹标题，便于核对 M7 父目录标题识别效果。

## 后端接口

- `GET /api/scan-jobs/mode-suggestion?media_source_id={id}`
  - 根据扫描索引和最近任务状态返回推荐扫描模式。
- `POST /api/rename-previews/exclude`
  - 批量将重命名预览排除到待处理列表。

## 验证

- `npm.cmd run backend:test`
  - 166 项通过。
- `npm.cmd run frontend:test`
  - 55 项通过。
- `npm.cmd run frontend:build`
  - 通过，存在 Vite 大 chunk 常规警告。
- `npm.cmd run check:encoding`
  - 通过。
- `git diff --check`
  - 通过。

## 后续

- M7-3.5：AI 批量匹配模式，与 TMDB 匹配串联，并在执行前展示外部提交风险提示。
- M7 后续：增量扫描更多 UI 细节、规则拦截记录的操作流优化。
