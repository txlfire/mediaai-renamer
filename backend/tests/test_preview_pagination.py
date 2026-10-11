"""预览分页和批量范围读取必须有界，不能把当前页当成全部筛选结果。"""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from api_test_support import authenticatedClient
from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service import preview_service as service
from app.service.settings_service import update_setting_values


class PreviewPaginationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) VALUES (1,'synthetic','/synthetic',1,'now','now')")
            connection.execute("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES (1,1,'completed',100,0,'now')")
            connection.executemany("INSERT INTO media_files (id,media_source_id,scan_job_id,file_path,file_name,extension,file_size,modified_at,created_at) "
                "VALUES (?,1,1,?,? ,'.mkv',1,'now','now')", ((index, f"/synthetic/{index}", f"file-{index:04}") for index in range(1, 1001)))
            connection.executemany("INSERT INTO rename_previews "
                "(id,media_file_id,media_type,parsed_title,original_extension,suggested_name,status,created_at,updated_at) "
                "VALUES (?,?,'movie',? ,'.mkv',?,'generated','now','now')",
                ((index, index, f"title-{index:04}", f"target-{index:04}") for index in range(1, 1001)))
            for index, status in ((2, "edited"), (3, "needs_review"), (4, "renamed"), (5, "excluded")):
                connection.execute("UPDATE rename_previews SET status=? WHERE id=?", (status, index))
            for index, status in ((6, "failed"), (7, "low_confidence"), (8, "high_confidence")):
                connection.execute("UPDATE rename_previews SET metadata_match_status=? WHERE id=?", (status, index))
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.directory.cleanup()

    def test_page_only_converts_selected_rows_and_statistics_are_global(self):
        with patch.object(service, "_row_to_preview", wraps=service._row_to_preview) as convert:
            result = service.listRenamePreviewsPage(self.settings, page=2, page_size=50)
        self.assertEqual(999, result["total"])
        self.assertEqual(50, convert.call_count)
        self.assertEqual(list(range(52, 102)), [item.id for item in result["items"]])
        self.assertEqual(1, result["stats"]["needs_review"])
        self.assertEqual(1, result["stats"]["renamed"])
        self.assertEqual(999, sum(result["stats"].values()))

    def test_global_sort_filters_and_clamped_pages(self):
        result = service.listRenamePreviewsPage(self.settings, sort_by="current_target_name", sort_order="desc", page_size=10)
        self.assertEqual(1000, result["items"][0].id)
        edited = service.listRenamePreviewsPage(self.settings, status="edited", page=99)
        self.assertEqual((1, 1, 2), (edited["page"], edited["total"], edited["items"][0].id))
        keyword = service.listRenamePreviewsPage(self.settings, keyword="title-0999")
        self.assertEqual(999, keyword["items"][0].id)
        empty = service.listRenamePreviewsPage(self.settings, media_source_id=999, page=99)
        self.assertEqual((1, 0, []), (empty["page"], empty["total"], empty["items"]))

    def test_selection_rejects_over_limit_instead_of_returning_first_batch(self):
        result = service.selectRenamePreviewTargets(self.settings, action="rename", scan_job_id=1)
        self.assertGreater(result["total"], 200)
        self.assertTrue(result["over_limit"])
        self.assertEqual([], result["items"])
        self.assertFalse(result["operation_executed"])
        limited = service.selectRenamePreviewTargets(self.settings, action="rename", status="edited")
        self.assertEqual([2], [item["id"] for item in limited["items"]])
        self.assertEqual("filtered_results", limited["scope"])
        self.assertNotIn("file_path", limited["items"][0])

    def test_selection_eligibility_matches_existing_ui_and_is_not_page_scoped(self):
        update_setting_values(self.settings, {"operations.batch_limit": 2000})
        legacy = service.list_rename_previews(self.settings)
        for action, predicate in (("rename", lambda item: item.status in {"generated", "edited"}),
            ("multi_source", lambda item: item.status not in {"renamed", "excluded"}),
            ("ai", lambda item: item.status not in {"renamed", "excluded"} and item.metadata_match_status in {None, "", "failed", "low_confidence"}),
            ("tmdb", lambda item: not item.metadata_match_status)):
            with self.subTest(action=action):
                expected = [item.id for item in legacy if predicate(item)]
                result = service.selectRenamePreviewTargets(self.settings, action=action)
                self.assertEqual(expected, [item["id"] for item in result["items"]])
                self.assertEqual(len(expected), result["total"])
                self.assertFalse(result["over_limit"])

    def test_api_static_routes_validation_and_legacy_array(self):
        client = authenticatedClient(create_app(self.settings))
        result = client.get("/api/rename-previews/page?page=2&page_size=50")
        self.assertEqual(200, result.status_code)
        self.assertEqual(999, result.json()["total"])
        selected = client.get("/api/rename-previews/selection?action=rename&status=edited")
        self.assertEqual(200, selected.status_code)
        self.assertEqual(2, selected.json()["items"][0]["id"])
        for path, status in (("page?page=0", 422), ("page?page_size=201", 422),
            ("page?sort_by=unsafe", 400), ("page?sort_order=unsafe", 400), ("selection?action=unsafe", 400)):
            self.assertEqual(status, client.get("/api/rename-previews/" + path).status_code)
        self.assertIsInstance(client.get("/api/rename-previews").json(), list)
