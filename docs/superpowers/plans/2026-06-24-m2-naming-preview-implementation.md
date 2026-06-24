# M2 标准命名与预览实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 基于 M1 扫描结果生成可编辑的标准命名预览，且预览阶段绝不修改真实文件。

**架构：** 后端新增本地文件名解析、命名模板和预览持久化服务；数据库新增 `rename_previews` 表；前端新增“命名预览”菜单和操作台页面。M2 不接外部 API，不执行真实重命名。

**技术栈：** Python 3.11、FastAPI、SQLite、unittest、Vue 3、TypeScript、Pinia、Element Plus、Vitest。

---

## 文件结构

后端新增或修改：

- 创建：`backend/app/utils/filename_parser.py`  
  负责文件名清洗、电影/剧集基础识别，返回纯数据结构。
- 创建：`backend/app/utils/naming_template.py`  
  负责根据解析结果生成标准目标文件名。
- 修改：`backend/app/schema/media.py`  
  新增 `ParsedMediaName`、`RenamePreview`、`PreviewGenerationSummary` 数据模型。
- 修改：`backend/app/core/database.py`  
  初始化 `rename_previews` 表。
- 创建：`backend/app/service/preview_service.py`  
  负责生成、查询、编辑预览记录。
- 创建：`backend/app/api/rename_previews.py`  
  提供生成、查询、编辑 API。
- 修改：`backend/app/main.py`  
  注册命名预览路由。
- 创建：`backend/tests/test_filename_parser.py`  
  覆盖本地解析规则。
- 创建：`backend/tests/test_naming_template.py`  
  覆盖命名模板和非法字符处理。
- 创建：`backend/tests/test_rename_previews.py`  
  覆盖预览生成、查询、编辑和不修改真实文件。

前端新增或修改：

- 修改：`frontend/src/api/client.ts`  
  新增命名预览 API 封装和类型。
- 修改：`frontend/src/api/client.test.ts`  
  覆盖新增 API 请求。
- 创建：`frontend/src/stores/preview.ts`  
  管理预览列表、筛选条件、生成和编辑动作。
- 创建：`frontend/src/stores/preview.test.ts`  
  覆盖预览数据转换和状态更新。
- 修改：`frontend/src/router/index.ts`  
  新增 `/rename-previews` 路由。
- 修改：`frontend/src/App.vue`  
  左侧菜单新增“命名预览”。
- 创建：`frontend/src/views/RenamePreviewsView.vue`  
  新增命名预览操作台页面。
- 修改：`frontend/src/styles.css`  
  补齐命名预览页在亮色/暗色主题下的表格、状态和编辑弹窗样式。

文档和日志：

- 修改：`docs/work-logs/progress-2026-06-24.md`  
  追加 M2 启动记录、规格和计划位置。
- 后续 M2 完成后创建：`docs/design/M2-design-manual.md`、`docs/manuals/M2-user-manual-cn.md`、`docs/manuals/M2-user-manual.md`。

## 任务 1：后端解析器

**文件：**
- 创建：`backend/app/utils/filename_parser.py`
- 修改：`backend/app/schema/media.py`
- 测试：`backend/tests/test_filename_parser.py`

- [ ] **步骤 1：编写失败的解析器测试**

在 `backend/tests/test_filename_parser.py` 中写入以下行为测试：

```python
import unittest

from app.utils.filename_parser import parse_media_filename


class FilenameParserTest(unittest.TestCase):
    def test_parse_movie_with_year_and_noise(self):
        result = parse_media_filename("The.Matrix.1999.1080p.BluRay.x264.mkv")

        self.assertEqual(result.media_type, "movie")
        self.assertEqual(result.title, "The Matrix")
        self.assertEqual(result.year, 1999)
        self.assertIsNone(result.season)
        self.assertIsNone(result.episode)

    def test_parse_episode_with_sxxexx(self):
        result = parse_media_filename("Show.Name.S02E03.2160p.WEB-DL.mkv")

        self.assertEqual(result.media_type, "episode")
        self.assertEqual(result.title, "Show Name")
        self.assertEqual(result.season, 2)
        self.assertEqual(result.episode, 3)

    def test_parse_chinese_episode_without_season_defaults_to_one(self):
        result = parse_media_filename("庆余年.第12集.1080p.mp4")

        self.assertEqual(result.media_type, "episode")
        self.assertEqual(result.title, "庆余年")
        self.assertEqual(result.season, 1)
        self.assertEqual(result.episode, 12)

    def test_unknown_when_title_is_empty(self):
        result = parse_media_filename("1080p.WEB-DL.x265.mkv")

        self.assertEqual(result.media_type, "unknown")
        self.assertEqual(result.title, "")
        self.assertIn("无法识别标题", result.message)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行解析器测试确认失败**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_filename_parser.py -v
```

预期：失败，原因是 `app.utils.filename_parser` 尚不存在。

- [ ] **步骤 3：实现最小解析器**

在 `backend/app/schema/media.py` 增加：

```python
@dataclass(frozen=True)
class ParsedMediaName:
    """本地文件名解析结果。"""

    media_type: str
    title: str
    year: int | None
    season: int | None
    episode: int | None
    message: str | None = None
```

创建 `backend/app/utils/filename_parser.py`，实现：

```python
"""本地文件名解析工具。"""

from pathlib import Path
import re

from app.schema.media import ParsedMediaName

NOISE_TOKENS = {
    "720p",
    "1080p",
    "2160p",
    "web",
    "webdl",
    "web-dl",
    "bluray",
    "bdrip",
    "hdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "aac",
    "ddp",
}


def _strip_extension(file_name: str) -> str:
    return Path(file_name).stem


def _clean_title(value: str) -> str:
    value = re.sub(r"[\[\(【].*?[\]\)】]", " ", value)
    value = re.sub(r"[._\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    tokens = []
    for token in value.split(" "):
        normalized = token.lower().replace(".", "").replace("-", "")
        if normalized in {item.replace("-", "") for item in NOISE_TOKENS}:
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def parse_media_filename(file_name: str) -> ParsedMediaName:
    stem = _strip_extension(file_name)

    episode_patterns = [
        re.compile(r"(?P<title>.*?)[ ._\-]*[sS](?P<season>\d{1,2})[eE](?P<episode>\d{1,3})"),
        re.compile(r"(?P<title>.*?)[ ._\-]*Season[ ._\-]*(?P<season>\d{1,2})[ ._\-]*Episode[ ._\-]*(?P<episode>\d{1,3})", re.IGNORECASE),
        re.compile(r"(?P<title>.*?)[ ._\-]*第(?P<season>\d{1,2})季[ ._\-]*第(?P<episode>\d{1,3})集"),
        re.compile(r"(?P<title>.*?)[ ._\-]*第(?P<episode>\d{1,3})集"),
        re.compile(r"(?P<title>.*?)[ ._\-]*[eE](?P<episode>\d{1,3})"),
    ]
    for pattern in episode_patterns:
        match = pattern.search(stem)
        if match:
            title = _clean_title(match.group("title"))
            season_text = match.groupdict().get("season")
            season = int(season_text) if season_text else 1
            episode = int(match.group("episode"))
            if not title:
                return ParsedMediaName("unknown", "", None, None, None, "无法识别标题")
            return ParsedMediaName("episode", title, None, season, episode)

    year_match = re.search(r"(19\d{2}|20\d{2})", stem)
    if year_match:
        title = _clean_title(stem[: year_match.start()])
        if not title:
            return ParsedMediaName("unknown", "", int(year_match.group(1)), None, None, "无法识别标题")
        return ParsedMediaName("movie", title, int(year_match.group(1)), None, None)

    title = _clean_title(stem)
    if not title:
        return ParsedMediaName("unknown", "", None, None, None, "无法识别标题")
    return ParsedMediaName("unknown", title, None, None, None, "缺少年份或季集信息")
```

- [ ] **步骤 4：运行解析器测试确认通过**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_filename_parser.py -v
```

预期：4 项测试通过。

## 任务 2：后端命名模板

**文件：**
- 创建：`backend/app/utils/naming_template.py`
- 测试：`backend/tests/test_naming_template.py`

- [ ] **步骤 1：编写失败的模板测试**

在 `backend/tests/test_naming_template.py` 中写入：

```python
import unittest

from app.schema.media import ParsedMediaName
from app.utils.naming_template import build_preview_name


class NamingTemplateTest(unittest.TestCase):
    def test_build_movie_name(self):
        parsed = ParsedMediaName("movie", "The Matrix", 1999, None, None)

        self.assertEqual(build_preview_name(parsed, ".mkv"), "The.Matrix.1999.mkv")

    def test_build_episode_name(self):
        parsed = ParsedMediaName("episode", "Show Name", None, 2, 3)

        self.assertEqual(build_preview_name(parsed, ".mp4"), "Show.Name.S02E03.mp4")

    def test_remove_windows_illegal_characters(self):
        parsed = ParsedMediaName("movie", 'A:B/C*D?', 2020, None, None)

        self.assertEqual(build_preview_name(parsed, ".mkv"), "A.B.C.D.2020.mkv")

    def test_unknown_uses_title_only(self):
        parsed = ParsedMediaName("unknown", "Loose Title", None, None, None)

        self.assertEqual(build_preview_name(parsed, ".avi"), "Loose.Title.avi")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行模板测试确认失败**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_naming_template.py -v
```

预期：失败，原因是 `app.utils.naming_template` 尚不存在。

- [ ] **步骤 3：实现最小模板生成器**

创建 `backend/app/utils/naming_template.py`：

```python
"""标准命名模板工具。"""

import re

from app.schema.media import ParsedMediaName

ILLEGAL_FILENAME_CHARS = r'[<>:"/\\|?*]'


def _normalize_title(value: str) -> str:
    value = re.sub(ILLEGAL_FILENAME_CHARS, " ", value)
    value = re.sub(r"[._\-\s]+", ".", value)
    return value.strip(".")


def build_preview_name(parsed: ParsedMediaName, extension: str) -> str:
    normalized_extension = extension.lower()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"

    title = _normalize_title(parsed.title)
    if not title:
        return normalized_extension.lstrip(".")

    if parsed.media_type == "episode" and parsed.season and parsed.episode:
        return f"{title}.S{parsed.season:02d}E{parsed.episode:02d}{normalized_extension}"
    if parsed.media_type == "movie" and parsed.year:
        return f"{title}.{parsed.year}{normalized_extension}"
    return f"{title}{normalized_extension}"
```

- [ ] **步骤 4：运行模板测试确认通过**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_naming_template.py -v
```

预期：4 项测试通过。

## 任务 3：预览数据表和服务

**文件：**
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/schema/media.py`
- 创建：`backend/app/service/preview_service.py`
- 测试：`backend/tests/test_rename_previews.py`

- [ ] **步骤 1：编写失败的预览服务测试**

在 `backend/tests/test_rename_previews.py` 中创建测试，准备临时数据库、插入 `media_files`，验证生成预览和编辑：

```python
import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, ScanSettings
from app.core.database import ensure_database
from app.service.preview_service import (
    generate_rename_previews,
    list_rename_previews,
    update_rename_preview,
)


class RenamePreviewServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.media_file = root / "The.Matrix.1999.1080p.mkv"
        self.media_file.write_text("sample", encoding="utf-8")
        self.settings = AppSettings(
            app_name="test",
            version="0.1.0",
            host="127.0.0.1",
            port=8970,
            data_dir=root,
            database_path=root / "test.sqlite",
            log_dir=root / "logs",
            config_dir=root / "config",
            scan=ScanSettings(batch_size=100, batch_interval_seconds=0),
        )
        ensure_database(self.settings)
        self._insert_media_file()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _insert_media_file(self):
        import sqlite3

        with sqlite3.connect(self.settings.database_path) as connection:
            connection.execute(
                "INSERT INTO media_sources (id, name, path, enabled, created_at, updated_at) "
                "VALUES (1, 'test', ?, 1, 'now', 'now')",
                (str(self.media_file.parent),),
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, file_size, modified_at, created_at) "
                "VALUES (1, 1, 1, ?, ?, '.mkv', 6, 'now', 'now')",
                (str(self.media_file), self.media_file.name),
            )

    def test_generate_preview_does_not_modify_real_file(self):
        summary = generate_rename_previews(self.settings)
        previews = list_rename_previews(self.settings)

        self.assertEqual(summary.generated_count, 1)
        self.assertEqual(previews[0].suggested_name, "The.Matrix.1999.mkv")
        self.assertTrue(self.media_file.exists())
        self.assertEqual(self.media_file.name, "The.Matrix.1999.1080p.mkv")

    def test_update_preview_sets_edited_name(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]

        updated = update_rename_preview(self.settings, preview.id, "Matrix.Custom")

        self.assertEqual(updated.edited_name, "Matrix.Custom.mkv")
        self.assertEqual(updated.status, "edited")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行预览服务测试确认失败**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_previews.py -v
```

预期：失败，原因是预览服务和数据模型尚不存在。

- [ ] **步骤 3：新增模型和数据库表**

在 `backend/app/schema/media.py` 增加：

```python
@dataclass(frozen=True)
class RenamePreview:
    """命名预览记录。"""

    id: int
    media_file_id: int
    file_path: str
    file_name: str
    media_type: str
    parsed_title: str
    parsed_year: int | None
    season: int | None
    episode: int | None
    original_extension: str
    suggested_name: str
    edited_name: str | None
    status: str
    message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PreviewGenerationSummary:
    """命名预览生成摘要。"""

    generated_count: int
    needs_review_count: int
    edited_kept_count: int
```

在 `backend/app/core/database.py` 的数据库初始化中增加 `rename_previews` 表：

```python
connection.execute(
    "CREATE TABLE IF NOT EXISTS rename_previews "
    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "media_file_id INTEGER NOT NULL UNIQUE, "
    "media_type TEXT NOT NULL, "
    "parsed_title TEXT NOT NULL, "
    "parsed_year INTEGER, "
    "season INTEGER, "
    "episode INTEGER, "
    "original_extension TEXT NOT NULL, "
    "suggested_name TEXT NOT NULL, "
    "edited_name TEXT, "
    "status TEXT NOT NULL, "
    "message TEXT, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "FOREIGN KEY(media_file_id) REFERENCES media_files(id))"
)
```

- [ ] **步骤 4：实现预览服务**

创建 `backend/app/service/preview_service.py`，提供：

```python
def generate_rename_previews(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    media_file_ids: list[int] | None = None,
    overwrite_edited: bool = False,
) -> PreviewGenerationSummary:
    """读取媒体文件并写入命名预览记录。"""


def list_rename_previews(
    settings: AppSettings,
    media_source_id: int | None = None,
    scan_job_id: int | None = None,
    status: str | None = None,
    media_type: str | None = None,
    keyword: str | None = None,
) -> list[RenamePreview]:
    """按筛选条件查询命名预览记录。"""


def update_rename_preview(
    settings: AppSettings,
    preview_id: int,
    target_name: str,
) -> RenamePreview:
    """保存用户编辑后的目标文件名。"""
```

实现要求：

- 生成时读取 `media_files`，调用 `parse_media_filename` 和 `build_preview_name`。
- 状态规则：解析结果为 `unknown` 或消息非空时为 `needs_review`，否则为 `generated`。
- 对已编辑记录，`overwrite_edited=False` 时保留 `edited_name`，计入 `edited_kept_count`。
- `update_rename_preview` 禁止目录路径，空文件名报错；缺少扩展名时补回 `original_extension`。
- 所有时间使用 UTC ISO 8601 字符串。

- [ ] **步骤 5：运行预览服务测试确认通过**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_previews.py -v
```

预期：2 项测试通过。

## 任务 4：预览 API

**文件：**
- 创建：`backend/app/api/rename_previews.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_rename_previews.py`

- [ ] **步骤 1：补充失败的 API 测试**

在 `backend/tests/test_rename_previews.py` 增加 API 测试类，使用 FastAPI `TestClient`：

```python
from fastapi.testclient import TestClient
from app.main import create_app


class RenamePreviewApiTest(RenamePreviewServiceTest):
    def test_generate_and_list_preview_api(self):
        app = create_app(self.settings)
        client = TestClient(app)

        response = client.post("/api/rename-previews/generate", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generated_count"], 1)

        list_response = client.get("/api/rename-previews")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["current_target_name"], "The.Matrix.1999.mkv")

    def test_update_preview_api(self):
        app = create_app(self.settings)
        client = TestClient(app)
        client.post("/api/rename-previews/generate", json={})
        preview_id = client.get("/api/rename-previews").json()[0]["id"]

        response = client.put(
            f"/api/rename-previews/{preview_id}",
            json={"target_name": "Matrix.Custom"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_target_name"], "Matrix.Custom.mkv")
```

- [ ] **步骤 2：运行 API 测试确认失败**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_previews.py -v
```

预期：API 路由不存在导致失败。

- [ ] **步骤 3：实现 API 路由并注册**

创建 `backend/app/api/rename_previews.py`，使用 Pydantic 请求模型：

```python
class GenerateRenamePreviewsRequest(BaseModel):
    media_source_id: int | None = None
    scan_job_id: int | None = None
    media_file_ids: list[int] | None = None
    overwrite_edited: bool = False


class UpdateRenamePreviewRequest(BaseModel):
    target_name: str
```

路由：

- `POST /rename-previews/generate`
- `GET /rename-previews`
- `PUT /rename-previews/{preview_id}`

响应转换必须包含 `current_target_name` 字段，值为 `edited_name or suggested_name`。

在 `backend/app/main.py` 注册：

```python
from app.api import rename_previews

api_router.include_router(rename_previews.router)
```

- [ ] **步骤 4：运行 API 测试确认通过**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest backend\tests\test_rename_previews.py -v
```

预期：服务和 API 测试全部通过。

## 任务 5：前端 API 与状态

**文件：**
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/api/client.test.ts`
- 创建：`frontend/src/stores/preview.ts`
- 创建：`frontend/src/stores/preview.test.ts`

- [ ] **步骤 1：编写失败的前端 API 测试**

在 `frontend/src/api/client.test.ts` 增加：

```typescript
it("generates rename previews", async () => {
  mockFetch({ generated_count: 2, needs_review_count: 1, edited_kept_count: 0 });

  const result = await generateRenamePreviews({ scan_job_id: 1 });

  expect(result.generated_count).toBe(2);
  expect(fetch).toHaveBeenCalledWith(
    "/api/rename-previews/generate",
    expect.objectContaining({ method: "POST" }),
  );
});
```

在 `frontend/src/stores/preview.test.ts` 写入 store 行为测试：

```typescript
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, it, vi } from "vitest";

import { usePreviewStore } from "./preview";

vi.mock("../api/client", () => ({
  fetchRenamePreviews: vi.fn(async () => [
    { id: 1, current_target_name: "A.2020.mkv", status: "generated" },
  ]),
}));

beforeEach(() => {
  setActivePinia(createPinia());
});

it("loads previews into state", async () => {
  const store = usePreviewStore();

  await store.loadPreviews();

  expect(store.previews).toHaveLength(1);
  expect(store.previews[0].current_target_name).toBe("A.2020.mkv");
});
```

- [ ] **步骤 2：运行前端测试确认失败**

运行：

```powershell
npm.cmd run frontend:test
.\node_modules\.bin\vitest.cmd run frontend/src/stores/preview.test.ts
```

预期：失败，原因是新增 API 和 store 尚不存在。

- [ ] **步骤 3：实现前端 API 和 store**

在 `frontend/src/api/client.ts` 增加类型和函数：

```typescript
export interface RenamePreview {
  id: number;
  media_file_id: number;
  file_path: string;
  file_name: string;
  media_type: string;
  parsed_title: string;
  parsed_year: number | null;
  season: number | null;
  episode: number | null;
  suggested_name: string;
  edited_name: string | null;
  current_target_name: string;
  status: string;
  message: string | null;
  updated_at: string;
}

export interface GenerateRenamePreviewsRequest {
  media_source_id?: number;
  scan_job_id?: number;
  media_file_ids?: number[];
  overwrite_edited?: boolean;
}
```

新增：

- `generateRenamePreviews(request)`
- `fetchRenamePreviews(filters)`
- `updateRenamePreview(previewId, targetName)`

创建 `frontend/src/stores/preview.ts`，包含：

- `previews`
- `loading`
- `filters`
- `loadPreviews`
- `generatePreviews`
- `updatePreview`
- `stats` 计算属性

- [ ] **步骤 4：运行前端 API 和 store 测试确认通过**

运行：

```powershell
npm.cmd run frontend:test
.\node_modules\.bin\vitest.cmd run frontend/src/stores/preview.test.ts
```

预期：新增测试通过，既有测试不回归。

## 任务 6：前端命名预览页面

**文件：**
- 修改：`frontend/src/router/index.ts`
- 修改：`frontend/src/App.vue`
- 创建：`frontend/src/views/RenamePreviewsView.vue`
- 修改：`frontend/src/styles.css`

- [ ] **步骤 1：新增路由和菜单**

在 `frontend/src/router/index.ts` 引入并注册：

```typescript
import RenamePreviewsView from "../views/RenamePreviewsView.vue";

{
  path: "/rename-previews",
  name: "rename-previews",
  component: RenamePreviewsView,
}
```

在 `frontend/src/App.vue` 左侧菜单中，在“扫描结果”后添加“命名预览”菜单项，并使用现有菜单图标模式。

- [ ] **步骤 2：创建命名预览页面**

创建 `frontend/src/views/RenamePreviewsView.vue`，页面包含：

- 顶部操作区：生成预览按钮、状态筛选、类型筛选、刷新按钮。
- 统计区：总数、可使用、需检查、已编辑。
- 表格：原文件名、类型、解析标题、年份、季集、目标文件名、状态、操作。
- 编辑弹窗：编辑目标文件名，保存后调用 store。

状态文案：

- `generated`：可使用
- `edited`：已编辑
- `needs_review`：需检查

页面不出现“执行重命名”按钮。

- [ ] **步骤 3：补齐主题样式**

在 `frontend/src/styles.css` 增加命名预览页样式，复用现有变量：

- 表格标题行在暗色主题下使用深灰背景。
- 状态标签在亮色/暗色下都可读。
- 编辑弹窗和输入框跟随全局主题。
- 页面宽度变化时表格不挤压侧栏。

- [ ] **步骤 4：构建前端确认无类型错误**

运行：

```powershell
npm.cmd run frontend:build
```

预期：构建通过。允许保留既有第三方 `#__PURE__` 注释警告和包体积提示。

## 任务 7：全量验证和工作日志

**文件：**
- 修改：`docs/work-logs/progress-2026-06-24.md`

- [ ] **步骤 1：运行后端完整测试**

运行：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
```

预期：全部通过，并包含 M2 新增测试。

- [ ] **步骤 2：运行前端测试和构建**

运行：

```powershell
npm.cmd run frontend:test
.\node_modules\.bin\vitest.cmd run frontend/src/stores/app.test.ts
.\node_modules\.bin\vitest.cmd run frontend/src/stores/preview.test.ts
npm.cmd run frontend:build
```

预期：测试和构建通过。

- [ ] **步骤 3：追加 M2 工作日志**

在 `docs/work-logs/progress-2026-06-24.md` 追加：

```markdown
## 2026-06-24 M2 标准命名与预览启动

- 确认 M2 范围：本地文件名解析、标准命名模板、预览记录保存、预览项手动编辑。
- 明确非范围：不调用外部 API，不调用 AI，不执行真实重命名，不做冲突检测。
- 新增 M2 设计规格：`docs/superpowers/specs/2026-06-24-m2-naming-preview-design.md`。
- 新增 M2 实施计划：`docs/superpowers/plans/2026-06-24-m2-naming-preview-implementation.md`。
```

- [ ] **步骤 4：人工预览检查**

启动或刷新本地前端预览后检查：

- 左侧菜单出现“命名预览”。
- 点击后进入命名预览操作台。
- 亮色/暗色主题下表格、按钮、弹窗文字可读。
- 页面没有“执行重命名”入口。

## 验收清单

- [ ] 后端可从 `media_files` 生成 `rename_previews`。
- [ ] 电影默认命名为 `{title}.{year}{extension}`。
- [ ] 剧集默认命名为 `{title}.S01E02{extension}`。
- [ ] `needs_review` 项可显示并可编辑。
- [ ] 编辑目标文件名只保存预览记录，不修改真实文件。
- [ ] 前端左侧菜单新增“命名预览”。
- [ ] 前端页面可生成、筛选、查看、编辑命名预览。
- [ ] 亮色/暗色主题均可读。
- [ ] 全部后端测试、前端测试和前端构建通过。
