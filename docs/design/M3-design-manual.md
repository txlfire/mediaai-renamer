# MediaAI Renamer M3 设计手册

版本：v0.3.0 正式版
阶段：M3 安全重命名
日期：2026-06-26

## 1. 阶段结论

M3 在 M2 命名预览的基础上，完成第一版安全重命名闭环。

本阶段支持用户选择命名预览记录，先执行 dry-run 冲突检测，再由用户确认执行真实文件重命名。系统只在原目录内修改文件名，不移动目录，不覆盖已有文件，并记录每一次重命名批次和明细结果。

## 2. 已包含范围

- 命名预览多选。
- dry-run 冲突检测。
- 冲突检测结果弹窗。
- 用户确认后的真实重命名。
- 重命名批次和明细记录。
- 成功后同步更新媒体文件路径和命名预览状态。
- 已重命名状态的中文展示和数量统计。
- 执行失败项记录失败原因，不阻断其他可执行项。

## 3. 未包含范围

- 跨目录移动。
- 跨磁盘或跨网络共享移动。
- 覆盖已有文件。
- 自动撤销和回滚。
- 外部元数据匹配。
- DeepSeek / AI 解析。
- 用户权限审批流程。

## 4. 安全原则

- dry-run 不修改真实文件。
- 执行接口只接受已有 dry-run 批次 ID，不直接接受任意文件路径。
- 只处理 dry-run 中状态为 `ready` 的明细项。
- 目标文件名不能包含目录分隔符。
- 目标路径必须和源路径位于同一目录。
- 目标文件已存在时标记为冲突，不覆盖。
- 批次内目标路径重复时标记为冲突。
- 单个文件执行失败时记录失败，不影响后续文件继续处理。

## 5. 后端设计

新增服务：

- `backend/app/service/rename_operation_service.py`

新增 API：

```text
POST /api/rename-operations/dry-run
GET /api/rename-operations/{operation_id}
POST /api/rename-operations/{operation_id}/execute
```

新增数据表：

- `rename_operations`：保存重命名批次状态和数量汇总。
- `rename_operation_items`：保存每个文件的源路径、目标路径、状态和消息。

批次状态：

- `dry_run`
- `completed`
- `partial_failed`
- `failed`

明细状态：

- `ready`
- `conflict`
- `renamed`
- `failed`

## 6. 前端设计

命名预览页面新增：

- 表格多选。
- 冲突检测入口。
- 冲突检测结果弹窗。
- 批次数量汇总。
- 重命名明细表格。
- 确认重命名入口。
- 执行完成后的结果刷新。

按钮和表格沿用 M2 的列表规范：操作按钮只保留图标，悬停显示名称；长文本按配置截断并通过气泡展示完整内容；表格优先纵向滚动，不出现横纵双滚动。

## 7. 验证记录

发布前需要执行：

```powershell
$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest discover backend\tests -v
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts frontend/src/stores/pagination.test.ts frontend/src/stores/tableSort.test.ts frontend/src/stores/preview.test.ts frontend/src/stores/renameOperation.test.ts frontend/src/utils/displayFormat.test.ts frontend/src/utils/localDirectory.test.ts frontend/src/api/client.test.ts
npm.cmd run frontend:build
```

已知提示：

- 前端构建可能保留 Vite chunk size 提示，这是包体积提示，不影响 v0.3.0 发布结果。
