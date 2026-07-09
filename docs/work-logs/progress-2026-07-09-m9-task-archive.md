# 2026-07-09 M9+ 任务治理归档增强

## 结论

M9+ 维护版 `0.9.1` 补齐任务治理手动归档入口。归档只影响任务治理列表展示，不删除扫描任务、扫描结果、重命名批次、回滚计划或真实文件。

## 变更范围

- 后端新增 `task_archives` 表，数据库 schema version 提升到 `17`。
- 任务治理聚合接口新增 `include_archived` 查询参数，默认隐藏已归档任务。
- 新增 `PATCH /api/tasks/{task_type}/{task_id}/archive`，支持归档和恢复。
- 归档和恢复写入审计事件 `task.governance`。
- 前端任务治理页新增“显示已归档”筛选项和行内归档/恢复操作。
- README、M9 用户手册、完整用户手册、M9 开发计划、验收清单和验收报告已同步。

## 验证计划

- `npm.cmd run backend:test`
- `npm.cmd run frontend:test`
- `npm.cmd run frontend:build`
- `npm.cmd run check:encoding`
- `git diff --check`

## 后续

M10 从下一阶段开始，不混入 `v0.9.0` 正式发布说明。M10 计划继续围绕多站点元数据源和后续治理增强推进。
