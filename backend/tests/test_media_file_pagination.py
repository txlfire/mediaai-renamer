"""扫描结果有界分页、稳定排序、接口兼容和注入边界回归。"""

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from api_test_support import authenticatedClient
from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.scan_service import list_media_files, listMediaFilesPage


class MediaFilePaginationTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        root = Path(self.tempDir.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
                                    logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) VALUES (1,'synthetic','/synthetic',1,'now','now')")
            for job in (1, 2):
                connection.execute("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,created_at) "
                                   "VALUES (?,1,'completed',100,0,'now')", (job,))
            connection.executemany("INSERT INTO media_files (media_source_id,scan_job_id,file_path,file_name,extension,"
                "file_size,modified_at,created_at) VALUES (1,?,?,?,?,?,'2026-10-01T00:00:00+00:00','now')",
                [(1 if index <= 1000 else 2, f"/synthetic/movie{index}.mkv", f"movie{index}.mkv", ".mkv", index) for index in range(1, 1021)])
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def test_page_is_bounded_and_sorting_is_global_with_stable_ties(self):
        page = listMediaFilesPage(self.settings, scanJobId=1, page=2, pageSize=50)
        self.assertEqual(1000, page["total"])
        self.assertEqual(list(range(950, 900, -1)), [item.id for item in page["items"]])
        sortedPage = listMediaFilesPage(self.settings, scanJobId=1, page=2, pageSize=10, sortBy="file_size", sortOrder="asc")
        self.assertEqual(list(range(11, 21)), [item.file_size for item in sortedPage["items"]])

    def test_invalid_page_or_sort_never_reaches_dynamic_sql(self):
        for args in ({"page": 0}, {"pageSize": 0}, {"pageSize": 201}, {"page": 1.5}, {"pageSize": True},
                     {"sortBy": "id; DROP TABLE media_files"}, {"sortOrder": "desc;DROP TABLE users"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                listMediaFilesPage(self.settings, **args)
        self.assertEqual(1020, listMediaFilesPage(self.settings)["total"])

    def test_last_page_and_empty_filters_are_normalized(self):
        page = listMediaFilesPage(self.settings, scanJobId=2, page=999, pageSize=10)
        self.assertEqual(2, page["page"])
        self.assertEqual(10, len(page["items"]))
        empty = listMediaFilesPage(self.settings, mediaSourceId=2, scanJobId=1, page=100)
        self.assertEqual((1, 0, []), (empty["page"], empty["total"], empty["items"]))

    def test_http_legacy_list_is_unchanged_and_new_page_validates_inputs(self):
        client = authenticatedClient(create_app(self.settings))
        page = client.get("/api/media-files/page?scan_job_id=1&page=2&page_size=10&sort_by=id&sort_order=asc")
        self.assertEqual(200, page.status_code)
        self.assertEqual(1000, page.json()["total"])
        self.assertEqual(11, page.json()["items"][0]["id"])
        legacy = client.get("/api/media-files?scan_job_id=2")
        self.assertIsInstance(legacy.json(), list)
        self.assertEqual(20, len(legacy.json()))
        self.assertEqual(20, len(list_media_files(self.settings, scan_job_id=2)))
        for query in ("page_size=0", "page_size=201", "page=0", "sort_by=raw_sql", "sort_order=DROP", "scan_job_id=-1"):
            with self.subTest(query=query):
                self.assertEqual(422, client.get("/api/media-files/page?" + query).status_code)

    def test_default_job_sort_uses_the_composite_index(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            plan = connection.execute("EXPLAIN QUERY PLAN SELECT id FROM media_files WHERE scan_job_id=? "
                "ORDER BY modified_at DESC,id DESC LIMIT ? OFFSET ?", (1, 50, 50)).fetchall()
        detail = " ".join(str(row[-1]) for row in plan)
        self.assertIn("idx_media_files_job_modified", detail)
        self.assertNotIn("TEMP B-TREE", detail)
