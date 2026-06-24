# M1 本地目录扫描与页面框架实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现 M1 本地 / 已挂载目录扫描，并建立左侧可伸缩菜单、右侧操作台、左下主题切换和日志弹出页的前端框架。

**架构：** 后端新增 schema、service、utils 和 API 模块，数据库初始化统一维护媒体源、扫描任务和扫描结果表。前端新增应用布局、主题状态、媒体源 / 扫描任务 / 扫描结果页面和日志抽屉，所有请求继续通过 `frontend/src/api/`。

**技术栈：** Python 3.11、FastAPI、SQLite、unittest、Vue 3、TypeScript、Pinia、Element Plus、Axios、Vite。

---

## 文件结构

- 修改：`backend/app/core/config.py`，新增扫描配置模型和默认值。
- 修改：`backend/app/core/database.py`，创建 M1 数据表。
- 创建：`backend/app/schema/media.py`，定义媒体源、扫描任务、媒体文件、日志响应模型。
- 创建：`backend/app/service/media_source_service.py`，封装媒体源 CRUD 和路径校验。
- 创建：`backend/app/service/scan_service.py`，封装视频识别、分批扫描和结果写入。
- 创建：`backend/app/service/log_service.py`，封装日志读取和 TXT 导出内容。
- 创建：`backend/app/utils/media_file.py`，封装视频扩展名识别。
- 创建：`backend/app/api/media_sources.py`，提供媒体源 API。
- 创建：`backend/app/api/scan_jobs.py`，提供扫描任务和扫描结果 API。
- 创建：`backend/app/api/logs.py`，提供日志查看和导出 API。
- 修改：`backend/app/main.py`，注册 M1 API 路由。
- 创建：`backend/tests/test_media_file.py`，测试视频格式识别。
- 创建：`backend/tests/test_media_sources.py`，测试媒体源路径校验和 CRUD。
- 创建：`backend/tests/test_scan_service.py`，测试分批扫描和结果写入。
- 创建：`backend/tests/test_logs_api.py`，测试日志读取和导出。
- 修改：`frontend/src/api/client.ts`，扩展 M1 API 封装。
- 修改：`frontend/src/stores/app.ts`，新增主题、侧栏和系统状态。
- 创建：`frontend/src/stores/media.ts`，保存媒体源、扫描任务、扫描结果状态。
- 修改：`frontend/src/App.vue`，实现主布局。
- 修改：`frontend/src/router/index.ts`，配置 M1 三个页面。
- 修改：`frontend/src/views/HomeView.vue` 或替换为默认重定向页面。
- 创建：`frontend/src/views/MediaSourcesView.vue`，媒体源页面。
- 创建：`frontend/src/views/ScanJobsView.vue`，扫描任务页面。
- 创建：`frontend/src/views/ScanResultsView.vue`，扫描结果页面。
- 创建：`frontend/src/components/LogDrawer.vue`，日志弹出页。
- 修改：`frontend/src/styles.css`，实现亮色、暗色和跟随系统主题变量。
- 创建或修改：`frontend/src/api/client.test.ts`，覆盖主题无关的 API 客户端行为。
- 创建：`frontend/src/stores/app.test.ts`，测试主题模式解析和持久化。
- 修改：`docs/work-logs/progress-2026-06-24.md`，记录实现和验证结果。
- 修改：`docs/development/development-guide.md`，同步 M1 启动和验证说明。

## 任务 1：扫描配置和视频格式识别

**文件：**
- 修改：`backend/app/core/config.py`
- 创建：`backend/app/utils/media_file.py`
- 测试：`backend/tests/test_media_file.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""媒体文件工具测试。"""

import unittest

from app.core.config import ScanSettings
from app.utils.media_file import is_video_file


class MediaFileUtilsTest(unittest.TestCase):
    """媒体文件识别和扫描配置测试。"""

    def test_is_video_file_accepts_common_extensions_case_insensitive(self):
        """常见视频扩展名应被识别，且大小写不敏感。"""

        self.assertTrue(is_video_file("Movie.MKV"))
        self.assertTrue(is_video_file("episode.rmvb"))
        self.assertTrue(is_video_file("clip.m2ts"))

    def test_is_video_file_rejects_non_video_files(self):
        """非视频文件不应被识别为视频。"""

        self.assertFalse(is_video_file("poster.jpg"))
        self.assertFalse(is_video_file("subtitle.ass"))
        self.assertFalse(is_video_file("README"))

    def test_scan_settings_rejects_invalid_values(self):
        """扫描批大小必须大于 0，批间隔不得小于 0。"""

        with self.assertRaises(ValueError):
            ScanSettings(batch_size=0)
        with self.assertRaises(ValueError):
            ScanSettings(batch_interval_seconds=-1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_media_file -v`

预期：FAIL 或 ERROR，原因是 `ScanSettings` 或 `app.utils.media_file` 尚不存在。

- [ ] **步骤 3：实现最少代码**

在 `backend/app/core/config.py` 增加：

```python
@dataclass(frozen=True)
class ScanSettings:
    """扫描配置。"""

    batch_size: int = 100
    batch_interval_seconds: float = 1

    def __post_init__(self):
        if self.batch_size <= 0:
            raise ValueError("扫描批大小必须大于 0")
        if self.batch_interval_seconds < 0:
            raise ValueError("扫描批间隔不得小于 0")
```

并在 `AppSettings` 增加：

```python
scan: ScanSettings = ScanSettings()
```

在 `backend/app/utils/media_file.py` 增加：

```python
"""媒体文件识别工具。"""

from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v",
    ".ts", ".m2ts", ".rmvb", ".mpg", ".mpeg", ".webm",
}


def is_video_file(file_name: str | Path) -> bool:
    """判断文件名是否属于 M1 支持的视频格式。"""

    return Path(file_name).suffix.lower() in VIDEO_EXTENSIONS
```

- [ ] **步骤 4：运行测试验证通过**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_media_file -v`

预期：PASS。

## 任务 2：数据库表结构和媒体源服务

**文件：**
- 修改：`backend/app/core/database.py`
- 创建：`backend/app/schema/media.py`
- 创建：`backend/app/service/media_source_service.py`
- 测试：`backend/tests/test_media_sources.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""媒体源服务测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import (
    create_media_source,
    list_media_sources,
)


class MediaSourceServiceTest(unittest.TestCase):
    """媒体源保存和路径校验测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_create_media_source_persists_valid_directory(self):
        """有效目录应保存为媒体源并可查询。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(settings, "电影", media_dir, True)
            sources = list_media_sources(settings)

            self.assertEqual("电影", created.name)
            self.assertEqual(str(media_dir), created.path)
            self.assertEqual(1, len(sources))

    def test_create_media_source_rejects_missing_directory(self):
        """不存在的路径应返回明确错误。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with self.assertRaises(ValueError):
                create_media_source(settings, "缺失", root / "missing", True)

    def test_database_creates_m1_tables(self):
        """数据库初始化应创建 M1 所需表。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with sqlite3.connect(settings.database_path) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertIn("media_sources", table_names)
            self.assertIn("scan_jobs", table_names)
            self.assertIn("media_files", table_names)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_media_sources -v`

预期：FAIL 或 ERROR，原因是服务和表结构尚不存在。

- [ ] **步骤 3：实现最少代码**

新增 Pydantic / dataclass 响应模型，数据库创建三张表，实现 `create_media_source()` 和 `list_media_sources()`。

数据库 SQL 必须包含唯一、稳定字段：

```sql
CREATE TABLE IF NOT EXISTS media_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_media_sources -v`

预期：PASS。

## 任务 3：分批扫描服务

**文件：**
- 创建：`backend/app/service/scan_service.py`
- 测试：`backend/tests/test_scan_service.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""扫描服务测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source
from app.service.scan_service import list_media_files, list_scan_jobs, run_full_scan


class ScanServiceTest(unittest.TestCase):
    """全量扫描和分批扫描测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=2, batch_interval_seconds=0),
        )

    def test_run_full_scan_persists_only_video_files_with_batch_config(self):
        """全量扫描应按批配置执行，并只保存视频文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("a", encoding="utf-8")
            (media_dir / "b.MP4").write_text("b", encoding="utf-8")
            (media_dir / "poster.jpg").write_text("poster", encoding="utf-8")
            nested = media_dir / "season"
            nested.mkdir()
            (nested / "c.rmvb").write_text("c", encoding="utf-8")

            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "测试", media_dir, True)

            job = run_full_scan(settings, source.id)
            files = list_media_files(settings)
            jobs = list_scan_jobs(settings)

            self.assertEqual("completed", job.status)
            self.assertEqual(4, job.scanned_count)
            self.assertEqual(3, job.video_count)
            self.assertEqual(2, job.batch_size)
            self.assertEqual(0, job.batch_interval_seconds)
            self.assertEqual(3, len(files))
            self.assertEqual(1, len(jobs))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_scan_service -v`

预期：FAIL 或 ERROR，原因是扫描服务尚不存在。

- [ ] **步骤 3：实现最少代码**

实现：

- `run_full_scan(settings, source_id)`
- `list_scan_jobs(settings)`
- `list_media_files(settings)`

扫描流程：

1. 查询媒体源。
2. 创建 `running` 任务。
3. 使用 `Path.rglob("*")` 遍历文件。
4. 每处理 `settings.scan.batch_size` 个文件后，按 `settings.scan.batch_interval_seconds` 休眠。
5. 视频文件写入 `media_files`。
6. 更新任务为 `completed` 或 `failed`。

- [ ] **步骤 4：运行测试验证通过**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_scan_service -v`

预期：PASS。

## 任务 4：M1 API

**文件：**
- 创建：`backend/app/api/media_sources.py`
- 创建：`backend/app/api/scan_jobs.py`
- 创建：`backend/app/api/logs.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_logs_api.py`，扩展 `backend/tests/test_health.py` 或新增 API 测试。

- [ ] **步骤 1：编写失败的 API 测试**

测试应覆盖：

- `POST /api/media-sources` 可新增媒体源。
- `GET /api/media-sources` 可查询媒体源。
- `POST /api/scan-jobs` 可触发扫描。
- `GET /api/media-files` 可查询扫描结果。
- `GET /api/logs` 可返回日志文本列表。
- `GET /api/logs/export` 返回 `text/plain`。

- [ ] **步骤 2：运行测试验证失败**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest backend.tests.test_logs_api -v`

预期：FAIL 或 ERROR，原因是 API 尚未注册。

- [ ] **步骤 3：实现最少 API**

所有 API 从 `request.app.state.settings` 或当前加载配置获取 settings。若现有应用未保存 settings，修改 `create_app()` 将 settings 挂到 `app.state.settings`。

- [ ] **步骤 4：运行测试验证通过**

运行：`$env:PYTHONPATH = "backend"; .\.venv\Scripts\python.exe -m unittest discover backend\tests -v`

预期：PASS。

## 任务 5：前端主框架和主题切换

**文件：**
- 修改：`frontend/src/stores/app.ts`
- 创建：`frontend/src/stores/app.test.ts`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/styles.css`
- 修改：`frontend/src/router/index.ts`

- [ ] **步骤 1：编写失败的主题测试**

在 `frontend/src/stores/app.test.ts` 中测试：

- 默认主题模式为 `system`。
- 设置 `light`、`dark`、`system` 会写入本地存储。
- `system` 模式会根据系统色彩偏好得到实际主题。

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run frontend/src/stores/app.test.ts`

预期：FAIL，原因是主题 store 尚未实现。

- [ ] **步骤 3：实现主框架和主题 store**

实现：

- 左侧可伸缩侧栏。
- Logo、标题、菜单。
- 左下提示语、系统状态、版本、主题切换、退出按钮。
- 右侧 `<router-view />` 操作台。
- 主题 CSS 变量。

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run frontend/src/stores/app.test.ts`

预期：PASS。

## 任务 6：前端 M1 页面和日志抽屉

**文件：**
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/api/client.test.ts`
- 创建：`frontend/src/stores/media.ts`
- 创建：`frontend/src/views/MediaSourcesView.vue`
- 创建：`frontend/src/views/ScanJobsView.vue`
- 创建：`frontend/src/views/ScanResultsView.vue`
- 创建：`frontend/src/components/LogDrawer.vue`

- [ ] **步骤 1：编写失败的 API 客户端测试**

测试应覆盖 M1 API URL：

- `fetchMediaSources()`
- `createMediaSource()`
- `createScanJob()`
- `fetchScanJobs()`
- `fetchMediaFiles()`
- `fetchLogs()`

- [ ] **步骤 2：运行测试验证失败**

运行：`npm run frontend:test`

预期：FAIL，原因是 M1 API 客户端尚未实现。

- [ ] **步骤 3：实现页面和 API 客户端**

实现：

- 媒体源页面表单和列表。
- 扫描任务页面源选择、启动按钮、任务列表、日志按钮。
- 扫描结果表格。
- 日志抽屉查看和 TXT 导出按钮。

- [ ] **步骤 4：运行测试验证通过**

运行：`npm run frontend:test`

预期：PASS。

## 任务 7：文档、验证和收尾

**文件：**
- 修改：`docs/work-logs/progress-2026-06-24.md`
- 修改：`docs/development/development-guide.md`
- 修改：`README.md`

- [ ] **步骤 1：更新文档**

记录：

- M1 新增 API。
- 默认端口：后端 `8970`，前端 Web `8971`。
- 分批扫描配置。
- 验证命令和结果。

- [ ] **步骤 2：运行完整验证**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
npm run frontend:test
npm run frontend:build
```

预期：全部 PASS。

- [ ] **步骤 3：记录限制**

如果当前环境没有 Docker，记录 Docker Compose 未在本机验证。

- [ ] **步骤 4：检查工作区**

运行：`git status --short`

预期：只包含本次 M1 相关文件。
