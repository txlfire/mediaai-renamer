"""1万/10万合成记录下，页响应和对象构造保持有界；不操作正式数据。"""

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
from time import perf_counter
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service import scan_service
from app.service import task_governance_service
from app.service import preview_service, pending_file_service


class PaginationScaleTest(unittest.TestCase):
    def test_preview_and_pending_pages_remain_bounded_at_scale(self):
        for count in (10000, 100000):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tempDir:
                root = Path(tempDir)
                settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
                    logging=LoggingSettings(log_dir=root / "logs", console_output=False))
                ensure_database(settings)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) VALUES (1,'synthetic','/synthetic',1,'now','now')")
                    connection.execute("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,created_at) VALUES (1,1,'completed',100,0,'now')")
                    connection.executemany("INSERT INTO media_files (id,media_source_id,scan_job_id,file_path,file_name,extension,file_size,modified_at,created_at) "
                        "VALUES (?,1,1,?,? ,'.mkv',1,'now','now')", ((index, f"/synthetic/{index}", f"file-{index:06}") for index in range(1, count + 1)))
                    connection.executemany("INSERT INTO rename_previews (id,media_file_id,media_type,parsed_title,original_extension,suggested_name,status,created_at,updated_at) "
                        "VALUES (?,?,'movie',? ,'.mkv',?,'generated','now','now')", ((index, index, f"title-{index:06}", f"target-{index:06}") for index in range(1, count + 1)))
                    connection.executemany("INSERT INTO pending_files (id,media_source_id,scan_job_id,file_path,file_name,extension,file_size,reason,status,created_at) "
                        "VALUES (?,1,1,?,? ,'.mkv',1,'size_filtered','pending','now')", ((index, f"/pending/{index}", f"pending-{index:06}") for index in range(1, count + 1)))
                    connection.commit()
                for name, service, reader, converter in (
                    ("preview", preview_service, preview_service.listRenamePreviewsPage, "_row_to_preview"),
                    ("pending", pending_file_service, pending_file_service.listPendingFilesPage, "_row_to_pending_file")):
                    started = perf_counter()
                    with patch.object(service, converter, wraps=getattr(service, converter)) as convert:
                        result = reader(settings, scan_job_id=1, page=count // 50, page_size=50)
                    elapsed = perf_counter() - started
                    self.assertEqual(count, result["total"])
                    self.assertEqual(50, convert.call_count)
                    self.assertEqual(list(range(count - 49, count + 1)), [item.id for item in result["items"]])
                    payloadBytes = len(json.dumps({**result, "items": [asdict(item) for item in result["items"]]}).encode())
                    self.assertLess(payloadBytes, 150000)
                    print(f'{name}_scale rows={count} page_size=50 response_bytes={payloadBytes} query_seconds={elapsed:.4f}')

    def test_governance_deep_pages_remain_bounded_at_scale(self):
        for count in (10000, 100000):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tempDir:
                root = Path(tempDir)
                settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
                    logging=LoggingSettings(log_dir=root / "logs", console_output=False))
                ensure_database(settings)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) "
                                       "VALUES (1,'synthetic','/synthetic',1,'now','now')")
                    connection.executemany("INSERT INTO scan_jobs "
                        "(id,media_source_id,status,batch_size,batch_interval_seconds,created_at) "
                        "VALUES (?,1,'completed',100,0,'2026-10-07')", ((index,) for index in range(1, count + 1)))
                    connection.commit()
                queries = []
                original = task_governance_service._connect
                def traced(config):
                    connection = original(config)
                    connection.set_trace_callback(queries.append)
                    return connection
                started = perf_counter()
                with patch.object(task_governance_service, "_connect", side_effect=traced):
                    result = task_governance_service.listTaskGovernancePage(settings, page=count // 50, page_size=50)
                elapsed = perf_counter() - started
                self.assertEqual(count, result["total"])
                self.assertEqual(list(range(50, 0, -1)), [item.task_id for item in result["items"]])
                self.assertLessEqual(len(queries), 6)
                payloadBytes = len(json.dumps({**result, "items": [asdict(item) for item in result["items"]]}).encode())
                self.assertLess(payloadBytes, 60000)
                print(f'governance_scale rows={count} page_size=50 queries={len(queries)} response_bytes={payloadBytes} query_seconds={elapsed:.4f}')

    def test_scan_result_pages_at_ten_thousand_and_hundred_thousand_rows(self):
        for count in (10000, 100000):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tempDir:
                root = Path(tempDir)
                settings = AppSettings(data_dir=root, database_path=root / 'app.sqlite3',
                                       logging=LoggingSettings(log_dir=root / 'logs', console_output=False))
                ensure_database(settings)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) "
                                       "VALUES (1,'synthetic','/synthetic',1,'now','now')")
                    connection.execute("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,created_at) "
                                       "VALUES (1,1,'completed',100,0,'now')")
                    connection.executemany("INSERT INTO media_files (media_source_id,scan_job_id,file_path,file_name,extension,"
                        "file_size,modified_at,created_at) VALUES (1,1,?,?,'.mkv',?,'2026-10-06T00:00:00+00:00','now')",
                        ((f'/synthetic/{index:06}.mkv', f'{index:06}.mkv', index) for index in range(1, count + 1)))
                    connection.commit()
                started = perf_counter()
                with patch.object(scan_service, '_row_to_media_file', wraps=scan_service._row_to_media_file) as convert:
                    result = scan_service.listMediaFilesPage(settings, scanJobId=1, page=count // 50, pageSize=50)
                elapsed = perf_counter() - started
                self.assertEqual(count, result['total'])
                self.assertEqual(50, len(result['items']))
                self.assertEqual(50, convert.call_count)
                self.assertEqual(list(range(50, 0, -1)), [item.id for item in result['items']])
                payloadBytes = len(json.dumps({**result, 'items': [asdict(item) for item in result['items']]}).encode('utf-8'))
                self.assertLess(payloadBytes, 25000)
                print(f'pagination_scale rows={count} page_size=50 response_bytes={payloadBytes} query_seconds={elapsed:.4f}')
