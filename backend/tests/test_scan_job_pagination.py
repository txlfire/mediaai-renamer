"""扫描任务分页与跨历史页活跃任务过滤回归。"""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from api_test_support import authenticatedClient
from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.scan_service import listScanJobsPage, list_scan_jobs


class ScanJobPaginationTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        root = Path(self.tempDir.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
                                    logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at) "
                               "VALUES (1,'synthetic','/synthetic',1,'now','now')")
            connection.executemany("INSERT INTO scan_jobs (id,media_source_id,status,batch_size,batch_interval_seconds,"
                                   "scanned_count,created_at) VALUES (?,1,?,100,0,?,'now')",
                                   [(index, 'running' if index == 1 else 'queued' if index == 2 else 'completed',
                                     index // 2) for index in range(1, 121)])
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def test_page_and_stable_sort_are_global(self):
        result = listScanJobsPage(self.settings, page=2, pageSize=10)
        self.assertEqual(120, result['total'])
        self.assertEqual(list(range(110, 100, -1)), [item.id for item in result['items']])
        ties = listScanJobsPage(self.settings, page=2, pageSize=10, sortBy='scanned_count', sortOrder='asc')
        self.assertEqual(list(range(11, 21)), [item.id for item in ties['items']])

    def test_active_filter_does_not_depend_on_history_page(self):
        history = listScanJobsPage(self.settings, pageSize=10)
        self.assertTrue(all(item.status == 'completed' for item in history['items']))
        active = listScanJobsPage(self.settings, mediaSourceId=1, activeOnly=True)
        self.assertEqual(2, active['total'])
        self.assertEqual([2, 1], [item.id for item in active['items']])

    def test_out_of_range_and_empty_page(self):
        result = listScanJobsPage(self.settings, page=999, pageSize=50)
        self.assertEqual((3, 20), (result['page'], len(result['items'])))
        empty = listScanJobsPage(self.settings, mediaSourceId=99, page=99)
        self.assertEqual((1, 0, []), (empty['page'], empty['total'], empty['items']))

    def test_invalid_parameters_and_sql_injection(self):
        for args in ({'page': 0}, {'page': True}, {'pageSize': 201}, {'pageSize': 0},
                     {'sortBy': 'id;DROP TABLE scan_jobs'}, {'sortOrder': 'asc;DROP TABLE users'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                listScanJobsPage(self.settings, **args)
        self.assertEqual(120, len(list_scan_jobs(self.settings)))

    def test_api_route_precedes_dynamic_id_and_preserves_legacy(self):
        client = authenticatedClient(create_app(self.settings))
        # 应用初始化会恢复没有执行令牌的旧running任务，测试请求前再设置活跃状态。
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE scan_jobs SET status='running' WHERE id=1")
            connection.commit()
        result = client.get('/api/scan-jobs/page?active_only=true&media_source_id=1')
        self.assertEqual(200, result.status_code)
        self.assertEqual(2, result.json()['total'])
        self.assertEqual(120, len(client.get('/api/scan-jobs').json()))
        for query in ('page=0', 'page_size=201', 'sort_by=raw_sql', 'media_source_id=-1', 'active_only=unknown'):
            with self.subTest(query=query):
                self.assertEqual(422, client.get('/api/scan-jobs/page?' + query).status_code)
