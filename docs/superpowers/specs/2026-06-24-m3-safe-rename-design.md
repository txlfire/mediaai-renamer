# M3 安全重命名设计规格

## 1. 目标

M3 在 M2 命名预览基础上提供安全重命名能力。用户先选择预览记录，后端执行 dry-run 冲突检测；只有检测通过并由用户确认后，后端才执行真实文件重命名。

## 2. 范围

已确认的 M3 第一版范围：

- 只在原目录内修改文件名。
- 不移动目录。
- 不覆盖已有文件。
- 不处理跨盘、跨共享路径移动。
- 不调用外部元数据服务或 AI。
- 不做撤销功能，先保证记录可回溯。

## 3. 后端流程

1. 前端提交一组 `rename_preview_id`。
2. 后端读取 `rename_previews` 与对应 `media_files`。
3. 生成每项的源路径和目标路径：
   - 源路径来自 `media_files.file_path`。
   - 目标路径为源文件所在目录加 `current_target_name`。
4. dry-run 检测：
   - 预览记录是否存在。
   - 预览状态是否为 `generated` 或 `edited`。
   - 源文件是否存在。
   - 目标文件名是否为空或包含目录分隔符。
   - 目标文件是否已存在，且不是同一个源文件。
   - 同一批次内目标路径是否重复。
5. dry-run 写入重命名批次和明细记录，但不修改真实文件。
6. 用户确认执行时，后端只执行 dry-run 批次中状态为 `ready` 的项目。
7. 单文件失败记录失败原因，继续处理后续项目。
8. 执行完成后返回批次摘要和明细。

## 4. 数据库设计

新增 `rename_operations` 表：

- `id`
- `status`
- `mode`
- `total_count`
- `ready_count`
- `conflict_count`
- `renamed_count`
- `failed_count`
- `created_at`
- `updated_at`

新增 `rename_operation_items` 表：

- `id`
- `operation_id`
- `rename_preview_id`
- `source_path`
- `target_path`
- `status`
- `message`
- `created_at`
- `updated_at`

状态约定：

- 批次状态：`dry_run`、`completed`、`partial_failed`、`failed`
- 明细状态：`ready`、`conflict`、`renamed`、`failed`

## 5. API 设计

dry-run：

```text
POST /api/rename-operations/dry-run
```

请求：

```json
{
  "rename_preview_ids": [1, 2, 3]
}
```

执行：

```text
POST /api/rename-operations/{operation_id}/execute
```

查询批次：

```text
GET /api/rename-operations/{operation_id}
```

## 6. 前端设计

命名预览页面新增：

- 表格多选框。
- “冲突检测”按钮。
- dry-run 结果弹窗。
- 结果汇总：总数、可执行、冲突。
- 明细表：状态、原文件名、目标文件名、原因。
- 当且仅当存在可执行项时显示“确认重命名”。
- 执行后在弹窗内展示执行结果，并刷新命名预览列表。

## 7. 安全要求

- dry-run 绝不修改真实文件。
- 执行接口只接受已有 dry-run 批次 ID，不直接接受文件路径。
- 默认不覆盖文件。
- 源路径和目标路径必须在同一目录。
- 目标文件名不能包含 `/` 或 `\`。
- 失败项不阻断其他 ready 项。

## 8. 测试策略

后端测试：

- dry-run 不修改真实文件。
- 源文件缺失标记为 `conflict`。
- 目标文件已存在标记为 `conflict`。
- 同批次目标路径重复标记为 `conflict`。
- execute 只重命名 `ready` 项。
- execute 后记录 `renamed` 或 `failed` 状态。

前端测试：

- API 客户端调用 dry-run、execute、get operation 接口。
- store 能保存当前批次、执行状态和错误信息。

