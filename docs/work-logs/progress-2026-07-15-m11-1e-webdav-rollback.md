# M11-1E WebDAV 回滚 MOVE 工作日志

日期：2026-07-15

## 背景

M11-1D 已完成 WebDAV 真实 MOVE 重命名最小闭环。真实远程写操作启用后，需要补齐回滚计划对 WebDAV 的反向 MOVE 支持，避免远程文件重命名后只能手工恢复。

## 本次完成

- 回滚 dry-run 支持 WebDAV URL，不再把远程 URL 当作本地 `Path` 做存在性判断。
- WebDAV 回滚执行接入远程写操作锁，锁粒度沿用 `media-source:{id}:write`。
- WebDAV 回滚执行写入 `remote_operation_items`，`operation_type` 为 `rollback`，并使用回滚计划和明细生成幂等键。
- WebDAV 回滚成功后恢复 `media_files`、`rename_previews` 和已有扫描索引状态。
- 新增 WebDAV 回滚服务测试，覆盖 dry-run、反向 MOVE、远程操作明细和媒体记录恢复。
- 版本号提升至 `0.11.10`。

## 验证

- `.\.venv\Scripts\python.exe -m unittest backend.tests.test_rename_rollback.RenameRollbackTest.test_webdav_rollback_dry_run_and_execute_uses_remote_move`
- `.\.venv\Scripts\python.exe -m unittest backend.tests.test_rename_rollback`

## 后续

- WebDAV 失败恢复和集成环境验证仍需后续阶段补齐。
- SFTP / S3 真实协议能力仍按 M11 计划继续推进。
