# 2026-07-09 M9-4 重命名历史和回滚

## 完成范围

- 新增重命名回滚服务，支持基于成功重命名明细创建回滚计划。
- 新增回滚 dry-run，校验当前文件、回滚目标、批次内目标重复和共享协议写权限。
- 新增回滚执行，执行前要求已完成 dry-run，不覆盖已有文件，单项失败不阻断其他可执行项。
- 回滚成功后同步 `media_files`、`rename_previews` 和 `scan_file_index`。
- 回滚计划创建、dry-run 和执行已写入审计事件。
- 回滚过程已写入 `operation_logs`，并复用运行日志抽屉查看。
- 前端在重命名批次弹窗中增加回滚计划、dry-run、执行、明细和回滚日志入口。

## 当前边界

- 回滚入口先放在重命名批次弹窗内；M9-5 任务治理阶段再增加统一任务入口。
- 回滚只基于系统已有的成功重命名记录，不处理外部程序移动、覆盖或删除后的强制恢复。
- 回滚执行后不恢复命名预览的原始候选名称，只标记为 `rolled_back` 并同步当前媒体文件路径。

## 验证记录

- `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_rename_rollback backend.tests.test_rename_operations -v`
- `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_rename_rollback backend.tests.test_rename_operations backend.tests.test_audit_api backend.tests.test_database_migrations -v`
- `npx vitest run frontend/src/api/client.test.ts frontend/src/stores/renameOperation.test.ts`
- `npm.cmd run frontend:test`
- `npm.cmd run frontend:build`
