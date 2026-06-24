# M3 安全重命名实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 基于 M2 命名预览实现原目录内安全重命名，先 dry-run 冲突检测，再由用户确认执行真实重命名。

**架构：** 后端新增 `rename_operations` 和 `rename_operation_items` 持久化批次；新增 `rename_operation_service.py` 封装 dry-run 与执行逻辑；新增 API 路由给前端调用。前端在命名预览页增加多选、冲突检测弹窗和确认执行入口。

**技术栈：** FastAPI、SQLite、Python unittest、Vue 3、Pinia、Element Plus、Vitest。

---

## 文件结构

- 创建：`backend/app/service/rename_operation_service.py`
  - 负责 dry-run 冲突检测、执行真实重命名、查询批次。
- 创建：`backend/app/api/rename_operations.py`
  - 暴露 dry-run、execute、get operation API。
- 修改：`backend/app/core/database.py`
  - 初始化 `rename_operations`、`rename_operation_items` 表。
- 修改：`backend/app/schema/media.py`
  - 增加 `RenameOperation`、`RenameOperationItem`、`RenameOperationSummary` 数据模型。
- 创建：`backend/tests/test_rename_operations.py`
  - 覆盖 dry-run 和 execute 关键路径。
- 修改：`backend/app/main.py`
  - 注册 `rename_operations` 路由。
- 修改：`frontend/src/api/client.ts`
  - 增加 M3 API 类型和函数。
- 修改：`frontend/src/api/client.test.ts`
  - 验证 M3 API 路径。
- 创建：`frontend/src/stores/renameOperation.ts`
  - 保存当前 dry-run 批次、执行结果、加载状态和错误信息。
- 创建：`frontend/src/stores/renameOperation.test.ts`
  - 覆盖 store 调用流程。
- 修改：`frontend/src/views/RenamePreviewsView.vue`
  - 增加多选、冲突检测按钮、结果弹窗和确认重命名。
- 修改：`docs/work-logs/progress-2026-06-24.md`
  - 记录 M3 启动、实现和验证情况。

## 任务 1：后端数据库和模型

**文件：**
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/schema/media.py`
- 测试：`backend/tests/test_rename_operations.py`

- [ ] **步骤 1：编写失败的数据库测试**

在 `backend/tests/test_rename_operations.py` 中创建测试：

```python
"""M3 rename operation tests."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database


class RenameOperationDatabaseTest(unittest.TestCase):
    def test_database_creates_rename_operation_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = AppSettings(
                data_dir=root,
                database_path=root / "mediaai.sqlite3",
                logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            )
            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertIn("rename_operations", table_names)
            self.assertIn("rename_operation_items", table_names)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_operations.py -v
```

预期：失败，提示缺少 `rename_operations` 表。

- [ ] **步骤 3：实现数据库表和 dataclass**

在 `backend/app/core/database.py` 中新增两段建表 SQL。

在 `backend/app/schema/media.py` 中新增 dataclass：

```python
@dataclass(frozen=True)
class RenameOperationItem:
    id: int
    operation_id: int
    rename_preview_id: int
    source_path: str
    target_path: str
    status: str
    message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RenameOperation:
    id: int
    status: str
    mode: str
    total_count: int
    ready_count: int
    conflict_count: int
    renamed_count: int
    failed_count: int
    created_at: str
    updated_at: str
    items: list[RenameOperationItem]
```

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：通过。

## 任务 2：dry-run 冲突检测

**文件：**
- 创建：`backend/app/service/rename_operation_service.py`
- 测试：`backend/tests/test_rename_operations.py`

- [ ] **步骤 1：编写 dry-run 失败测试**

在 `backend/tests/test_rename_operations.py` 增加 fixture 和测试：

```python
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run


class RenameOperationDryRunTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.source = self.media_dir / "Movie.2024.1080p.mkv"
        self.source.write_text("movie", encoding="utf-8")
        self.existing = self.media_dir / "Movie.2024.mkv"
        self.existing.write_text("existing", encoding="utf-8")
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
        )
        ensure_database(self.settings)
        self._insert_media_file()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_media_file(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                "VALUES (1, 'test', ?, 1, 'now', 'now')",
                (str(self.media_dir),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES (1, 1, 1, ?, ?, '.mkv', 5, 'now', 'now')",
                (str(self.source), self.source.name),
            )
            connection.commit()

    def test_dry_run_detects_existing_target_without_renaming(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]

        operation = create_rename_dry_run(self.settings, [preview.id])

        self.assertEqual("dry_run", operation.status)
        self.assertEqual(1, operation.conflict_count)
        self.assertEqual("conflict", operation.items[0].status)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.existing.exists())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_operations.py -v
```

预期：失败，提示缺少 `rename_operation_service`。

- [ ] **步骤 3：实现 dry-run 服务**

创建 `backend/app/service/rename_operation_service.py`，实现：

- `create_rename_dry_run(settings, rename_preview_ids)`
- `get_rename_operation(settings, operation_id)`

检测规则：

- 预览不存在：忽略或记录 conflict。
- 状态不是 `generated` 或 `edited`：`conflict`。
- 源文件不存在：`conflict`。
- 目标文件名包含目录分隔符：`conflict`。
- 目标文件已存在且不是同一个源路径：`conflict`。
- 批次内目标路径重复：重复项 `conflict`。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：通过。

## 任务 3：execute 真实重命名

**文件：**
- 修改：`backend/app/service/rename_operation_service.py`
- 测试：`backend/tests/test_rename_operations.py`

- [ ] **步骤 1：编写 execute 失败测试**

新增测试：

```python
def test_execute_renames_ready_items_only(self):
    generate_rename_previews(self.settings)
    preview = list_rename_previews(self.settings)[0]
    update_rename_preview(self.settings, preview.id, "Movie.Safe")
    operation = create_rename_dry_run(self.settings, [preview.id])

    executed = execute_rename_operation(self.settings, operation.id)

    self.assertEqual(1, executed.renamed_count)
    self.assertFalse(self.source.exists())
    self.assertTrue((self.media_dir / "Movie.Safe.mkv").exists())
```

- [ ] **步骤 2：运行测试验证失败**

预期：失败，提示 `execute_rename_operation` 未定义。

- [ ] **步骤 3：实现 execute**

在服务中实现 `execute_rename_operation(settings, operation_id)`：

- 读取指定批次及明细。
- 仅处理 `ready` 项。
- 调用 `Path(source_path).rename(target_path)`。
- 成功更新 item 为 `renamed`。
- 单项异常更新 item 为 `failed` 和异常消息。
- 汇总更新 operation 状态和计数。

- [ ] **步骤 4：运行测试验证通过**

运行 `backend\tests\test_rename_operations.py`。预期：通过。

## 任务 4：后端 API

**文件：**
- 创建：`backend/app/api/rename_operations.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_rename_operations.py`

- [ ] **步骤 1：编写 API 失败测试**

新增 API 测试：

```python
from fastapi.testclient import TestClient
from app.main import create_app


def test_rename_operation_api_dry_run_and_execute(self):
    generate_rename_previews(self.settings)
    preview = list_rename_previews(self.settings)[0]
    update_rename_preview(self.settings, preview.id, "Movie.Safe")
    client = TestClient(create_app(self.settings))

    dry_run_response = client.post(
        "/api/rename-operations/dry-run",
        json={"rename_preview_ids": [preview.id]},
    )
    self.assertEqual(200, dry_run_response.status_code)
    operation_id = dry_run_response.json()["id"]

    execute_response = client.post(f"/api/rename-operations/{operation_id}/execute")
    self.assertEqual(200, execute_response.status_code)
    self.assertEqual(1, execute_response.json()["renamed_count"])
```

- [ ] **步骤 2：运行测试验证失败**

预期：404。

- [ ] **步骤 3：实现 API 并注册路由**

API：

- `POST /api/rename-operations/dry-run`
- `GET /api/rename-operations/{operation_id}`
- `POST /api/rename-operations/{operation_id}/execute`

- [ ] **步骤 4：运行测试验证通过**

运行 `backend\tests\test_rename_operations.py`。预期：通过。

## 任务 5：前端 API 和 store

**文件：**
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/api/client.test.ts`
- 创建：`frontend/src/stores/renameOperation.ts`
- 创建：`frontend/src/stores/renameOperation.test.ts`

- [ ] **步骤 1：编写 API 和 store 失败测试**

在 `client.test.ts` 增加对 M3 API 路径的断言。

在 `renameOperation.test.ts` 中测试 store 调用 dry-run 和 execute 后保存批次。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
.\node_modules\.bin\vitest.cmd run frontend/src/api/client.test.ts frontend/src/stores/renameOperation.test.ts
```

预期：失败，提示函数或 store 不存在。

- [ ] **步骤 3：实现 API 和 store**

新增类型：

- `RenameOperation`
- `RenameOperationItem`

新增函数：

- `createRenameDryRun`
- `fetchRenameOperation`
- `executeRenameOperation`

新增 store：

- `currentOperation`
- `loading`
- `errorMessage`
- `runDryRun(ids)`
- `executeCurrentOperation()`

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令。预期：通过。

## 任务 6：命名预览页面接入

**文件：**
- 修改：`frontend/src/views/RenamePreviewsView.vue`
- 修改：`frontend/src/styles.css`

- [ ] **步骤 1：实现页面交互**

修改命名预览页：

- `el-table` 增加 `type="selection"` 列。
- 保存 `selectedPreviewIds`。
- 工具栏新增“冲突检测”按钮。
- dry-run 后弹出结果对话框。
- 对话框展示汇总和明细。
- ready 项大于 0 时显示“确认重命名”。
- 执行成功后刷新预览列表。

- [ ] **步骤 2：运行前端构建**

运行：

```powershell
npm.cmd run frontend:build
```

预期：构建通过。

## 任务 7：文档、完整验证和提交

**文件：**
- 修改：`docs/work-logs/progress-2026-06-24.md`
- 后续完成后创建：`docs/design/M3-design-manual.md`
- 后续完成后创建：`docs/manuals/M3-user-manual-cn.md`

- [ ] **步骤 1：补工作日志**

记录 M3 功能、接口、测试和构建结果。

- [ ] **步骤 2：完整验证**

运行：

```powershell
$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest discover backend\tests -v
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts frontend/src/stores/pagination.test.ts frontend/src/stores/tableSort.test.ts frontend/src/stores/preview.test.ts frontend/src/stores/renameOperation.test.ts frontend/src/utils/displayFormat.test.ts frontend/src/utils/localDirectory.test.ts frontend/src/api/client.test.ts
npm.cmd run frontend:build
```

- [ ] **步骤 3：提交**

```powershell
git add -A
git commit -m "feat: add M3 safe rename"
```

