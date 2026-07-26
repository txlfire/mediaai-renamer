# M11-1F WebDAV 失败恢复工作日志

日期：2026-07-26

## 背景

M11-1D 和 M11-1E 已分别完成 WebDAV 真实重命名与反向 MOVE 回滚。网络中断或数据库提交失败时，远端文件状态可能与本地业务记录不一致，因此需要基于幂等明细安全判定并恢复。

## 本次完成

- 新增远程操作明细查询接口：`GET /api/remote-operations/{item_id}`。
- 新增远程操作恢复接口：`POST /api/remote-operations/{item_id}/recover`。
- 恢复过程继续使用 `media-source:{id}:write` 写锁，并记录恢复操作者、动作和时间。
- 当源存在且目标不存在时，安全重试 WebDAV `MOVE`。
- 当源不存在且目标存在时，不重复发送 `MOVE`，仅补齐媒体文件、命名预览、重命名批次或回滚计划状态。
- 当源和目标状态无法唯一判断时，停止自动写入并将明细标记为 `recovery_required`。
- 重命名和回滚远程明细补充业务条目 ID，旧记录仍可通过幂等键兼容解析。
- 恢复成功或冲突均写入审计日志；有效远程写锁冲突返回 HTTP `409`。
- 版本号提升至 `0.11.11`。

## 测试覆盖

- 失败重命名安全重试 MOVE 并修复业务记录。
- 远端已完成 MOVE 时仅补齐数据库。
- 远端状态冲突时停止自动恢复。
- 失败回滚安全重试反向 MOVE 并修复回滚计划。
- 远程操作查询与恢复 API 契约。
- 有效远程写锁冲突返回 HTTP `409`。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest backend.tests.test_remote_operation_recovery -v`：5 项通过。
- `npm.cmd run backend:test`：298 项通过。
- `npm.cmd run check:encoding`：通过。
- `git diff --check`：通过。

## 后续

- 建立容器化 WebDAV 协议集成环境，验证真实连接、扫描、重命名、断连恢复和回滚。
- 完成恢复状态与入口的前端展示。
- 继续推进 M11-2 SFTP。
