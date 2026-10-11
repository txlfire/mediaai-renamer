"""媒体源分页必须保留旧接口并隐藏凭据，详情构造仅随页大小增长。"""

from contextlib import closing
from dataclasses import asdict
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
from app.service import media_source_service as service
from app.service.media_source_secret import encrypt_secret


class MediaSourcePaginationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        encrypted = encrypt_secret(self.settings, "private-secret")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO media_sources (id,name,path,enabled,created_at,updated_at,encrypted_secret) "
                "VALUES (?,?,?,?, 'now','now',?)",
                ((index, f"source-{index}", f"/synthetic/{index}", index % 2, encrypted) for index in range(1, 1021)))
            connection.execute("UPDATE media_sources SET name='literal%_source' WHERE id=1")
            connection.execute("UPDATE media_sources SET deleted_at='now' WHERE id=1020")
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.directory.cleanup()

    def test_page_only_converts_selected_rows_and_omits_credentials(self):
        with patch.object(service, "_row_to_media_source", wraps=service._row_to_media_source) as convert:
            result = service.listMediaSourcesPage(self.settings, page=2, pageSize=50)
        self.assertEqual(1019, result["total"])
        self.assertEqual(50, convert.call_count)
        self.assertEqual(list(range(51, 101)), [item.id for item in result["items"]])
        self.assertNotIn("private-secret", str([asdict(item) for item in result["items"]]))
        self.assertTrue(result["items"][0].has_secret)

    def test_search_escapes_wildcards_and_enabled_only_filters(self):
        literal = service.listMediaSourcesPage(self.settings, query="%_")
        self.assertEqual(1, literal["total"])
        self.assertEqual(1, literal["items"][0].id)
        enabled = service.listMediaSourcesPage(self.settings, enabledOnly=True)
        self.assertEqual(510, enabled["total"])
        self.assertTrue(all(item.enabled for item in enabled["items"]))

    def test_page_clamps_and_validates(self):
        last = service.listMediaSourcesPage(self.settings, page=999, pageSize=50)
        self.assertEqual((21, 19), (last["page"], len(last["items"])))
        empty = service.listMediaSourcesPage(self.settings, query="missing", page=999)
        self.assertEqual((1, 0, []), (empty["page"], empty["total"], empty["items"]))
        for kwargs in ({"page": 0}, {"pageSize": 0}, {"pageSize": 201}, {"query": "x" * 201}):
            with self.assertRaises(ValueError):
                service.listMediaSourcesPage(self.settings, **kwargs)

    def test_api_route_and_legacy_array_are_compatible(self):
        client = authenticatedClient(create_app(self.settings))
        result = client.get("/api/media-sources/page?page=2&page_size=10")
        self.assertEqual(200, result.status_code)
        self.assertEqual(11, result.json()["items"][0]["id"])
        self.assertEqual(1019, result.json()["total"])
        for query in ("page=0", "page_size=201", "q=" + "x" * 201):
            self.assertEqual(422, client.get("/api/media-sources/page?" + query).status_code)
        legacy = client.get("/api/media-sources")
        self.assertIsInstance(legacy.json(), list)
        self.assertEqual(1019, len(legacy.json()))
        selected = client.get("/api/media-sources/1019")
        self.assertEqual(200, selected.status_code)
        self.assertEqual(1019, selected.json()["id"])
        self.assertIsNone(selected.json()["secret"])
        self.assertNotIn("encrypted_secret", selected.json())
        self.assertEqual(404, client.get("/api/media-sources/1020").status_code)
        self.assertEqual(404, client.get("/api/media-sources/999999").status_code)
