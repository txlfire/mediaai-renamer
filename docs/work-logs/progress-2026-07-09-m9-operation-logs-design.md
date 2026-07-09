# 2026-07-09 M9 操作运行日志设计

## 背景

当前 M9 已补充审计日志，可以追踪“谁做了什么”。但扫描、dry-run、真实重命名等长耗时操作仍缺少过程级日志，页面只能看到最终状态或少量失败摘要，用户无法实时观察操作过程，也难以在事后定位失败步骤。

## 设计结论

- 新增“操作运行日志”作为独立结构化日志，不直接复用后端 `app.log`、`batch.log` 等文件日志。
- 审计日志用于追责，操作运行日志用于过程观察和问题定位，两者职责分离。
- M9-3A 先覆盖扫描任务、重命名 dry-run 和真实重命名执行。
- 实时查看先采用前端轮询 `after_id` 增量日志，不在 M9 引入 WebSocket。

## 文档更新

- 更新 `docs/design/M9-design-manual.md`：
  - 增加审计日志和操作运行日志边界。
  - 增加 `operation_logs` 表设计、查询 API、实时弹窗策略和验收标准。
- 更新 `docs/development/m9/M9-权限历史回滚审计任务治理开发计划.md`：
  - 新增 M9-3A 操作运行日志和实时日志窗口小阶段。
  - 明确后端、前端和验收拆分。
- 更新 `docs/development/m9/M9-权限历史回滚审计任务治理验收清单.md`：
  - 新增操作运行日志验收章节。
  - 补充回滚和任务治理与操作日志的衔接项。

## 后续实现建议

1. 先实现 `operation_logs` 表、服务和查询接口。
2. 再接入扫描任务日志写入，并在扫描任务列表增加“查看日志”入口。
3. 再接入重命名 dry-run / execute 日志写入，并复用同一个日志弹窗组件。
4. M9-4 回滚实现时复用操作日志服务，不再单独设计日志机制。

## 追加约束

- 操作运行日志清理跟随系统“通用设置”的日志保留天数，避免日志表无限增长。
- 操作运行日志还必须支持容量上限，默认总量 128 MB、单任务 16 MB、单次清理 1000 条，避免短时间大量日志撑大 SQLite。
- 日志写入记录估算大小 `approx_bytes` 或等价字段，用于按容量快速清理。
- 日志清理只删除过程日志，不删除任务记录、统计和审计事件。
- 超过容量上限时优先清理最早日志，并尽量保留最终状态、error 和 warning；详细日志被部分清理时，摘要接口返回中文提示。
- 日志已清理时，摘要接口返回明确状态，前端“查看日志”按钮应置灰并显示原因；已打开弹窗时显示清理提示并停止轮询。
- 实时日志窗口默认轮询间隔不低于 1500 ms，高频写入、请求失败、页面不可见或弹窗关闭时必须降频或停止。
- 前端最多渲染最近 1000 条日志，较早日志折叠提示，避免长时间任务导致浏览器 DOM 和内存持续增长。

## 实现进展

- 已新增 `operation_logs` 表，数据库版本升级到 16。
- 已新增运行日志服务和 `/api/operation-logs` 查询、导出、清理接口。
- 已接入扫描任务、重命名 dry-run、重命名执行的关键阶段日志写入。
- 已在扫描任务列表和重命名确认弹窗增加“查看日志”入口，前端抽屉按 `after_id` 增量轮询，最多保留 1000 条渲染日志。
- 已在通用设置中增加运行日志总大小、单任务大小和清理批量配置。

## 当前限制

- 现有扫描接口仍是同步返回，前端在不知道任务 ID 前无法真正做到“扫描中立即弹窗实时查看”。本次实现先完成日志基础能力、任务日志回看和已知任务的轮询能力；如需扫描开始即实时弹窗，后续应将扫描启动拆成“创建任务立即返回 + 后台执行”。

## 验证记录

- `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_database_migrations backend.tests.test_operation_log_service -v`
- `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_scan_service backend.tests.test_rename_operations backend.tests.test_operation_log_service -v`
- `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest backend.tests.test_settings_service backend.tests.test_settings_api backend.tests.test_database_migrations backend.tests.test_operation_log_service -v`
- `npx vitest run frontend/src/api/client.test.ts`
- `npm run frontend:build`
