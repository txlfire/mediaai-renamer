"""待处理队列分页不能遗漏恢复状态或改变旧数组契约。"""

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
from app.service import pending_file_service as service


class PendingPaginationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) VALUES (1,'synthetic','/synthetic',1,'now','now')")
            connection.execute("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES (1,1,'completed',100,0,'now')")
            connection.executemany("INSERT INTO pending_files (id,media_source_id,scan_job_id,file_path,file_name,extension,file_size,reason,status,created_at) "
                "VALUES (?,1,1,?,? ,'.mkv',1,'size_filtered','pending','now')",
                ((index, f"/synthetic/{index}", f"file-{index:04}") for index in range(1, 1001)))
            connection.execute("UPDATE pending_files SET status='removed' WHERE id=1")
            connection.execute("UPDATE pending_files SET status='moving' WHERE id IN (2,3)")
            for _ in range(2):
                connection.execute("INSERT INTO local_file_actions (media_source_id,action_type,record_id,source_path,target_path,source_key,target_key,"
                    "identity_json,status,created_at,updated_at) VALUES (1,'pending_move',2,'/synthetic/2','/target/2','s','t','{}','recovery_required','now','now')")
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.directory.cleanup()

    def test_bounded_conversion_global_statistics_and_recovery_projection(self):
        with patch.object(service, "_row_to_pending_file", wraps=service._row_to_pending_file) as convert:
            result = service.listPendingFilesPage(self.settings, page=2, page_size=50)
        self.assertEqual(50, convert.call_count)
        self.assertEqual(999, result["total"])
        self.assertEqual({"pending": 997, "moving": 1, "recovery_required": 1}, result["stats"])
        self.assertEqual(list(range(52, 102)), [item.id for item in result["items"]])
        first = service.listPendingFilesPage(self.settings)
        self.assertEqual("recovery_required", first["items"][0].status)
        self.assertEqual("moving", first["items"][1].status)

    def test_filters_last_page_empty_and_invalid_bounds(self):
        result = service.listPendingFilesPage(self.settings, scan_job_id=1, page=1000, page_size=50)
        self.assertEqual((20, 49), (result["page"], len(result["items"])))
        empty = service.listPendingFilesPage(self.settings, media_source_id=99, page=1000)
        self.assertEqual((1, 0, []), (empty["page"], empty["total"], empty["items"]))
        for arguments in ({"page": 0}, {"page_size": 0}, {"page_size": 201}):
            with self.assertRaises(ValueError): service.listPendingFilesPage(self.settings, **arguments)

    def test_api_static_route_validation_and_legacy_array(self):
        client = authenticatedClient(create_app(self.settings))
        response = client.get("/api/pending-files/page?scan_job_id=1&page=2&page_size=50")
        self.assertEqual(200, response.status_code)
        self.assertEqual(50, len(response.json()["items"]))
        self.assertEqual(999, response.json()["total"])
        for query in ("page=0", "page_size=201"):
            self.assertEqual(422, client.get("/api/pending-files/page?" + query).status_code)
        old = client.get("/api/pending-files").json()
        self.assertIsInstance(old, list)
        self.assertEqual(999, len(old))
        self.assertEqual("recovery_required", old[0]["status"])
