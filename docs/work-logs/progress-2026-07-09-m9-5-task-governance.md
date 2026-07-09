# 2026-07-09 M9-5 任务治理进展

## 完成内容

- 后端任务治理接口改为 `task:manage` 权限访问。
- 前端新增“任务治理”菜单入口，仅对具备 `task:manage` 权限的用户显示。
- 任务治理页面支持任务类型、状态、媒体源和时间范围筛选。
- 任务治理列表聚合扫描任务、重命名批次和回滚计划，并保留目标页面跳转和操作日志入口。
- 公共分页和表格排序类型补充 `task-governance`，修复前端构建类型错误。
- 更新 M9 开发计划和验收清单，标记已完成的任务治理能力。

## 验证

- `npm.cmd run backend:test -- test_task_governance.py`：通过，实际执行后端全量测试，230 项通过。
- `npm.cmd run frontend:test`：通过，74 项通过。
- `npm.cmd run frontend:build`：通过，存在 Vite chunk size 警告，不影响构建产物。
- `npm.cmd run check:encoding`：通过。
- `git diff --check`：通过。

## 待处理

- 任务归档入口尚未实现，验收清单保持未完成。
- M9 全量回归仍需在阶段收尾时统一执行。
