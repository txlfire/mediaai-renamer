"""独立写者参与分页快照，验证rollback journal和WAL而不改变生产库设置。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings
from app.core.database import ensure_database
from app.service import audit_service, remote_operation_service
from app.service.media_source_service import create_media_source


class PaginationSnapshotTest(unittest.TestCase):
    def verifySnapshot(self, journalMode, module, table, loadPage):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppSettings(data_dir=root, database_path=root / "snapshot.sqlite3")
            ensure_database(settings)
            source = create_media_source(settings, "synthetic", root, True)
            # 创建来源本身会产生审计，只让本用例的两条样本参与快照断言。
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("DELETE FROM audit_events")
                connection.commit()
            for index in range(2):
                audit_service.record_audit_event(settings, event_type="synthetic", action="test",
                    result="success", summary=f"分页样本{index}")
                remote_operation_service.create_remote_operation_item(settings, media_source_id=source.id,
                    operation_type="rename", idempotency_key=f"snapshot-{index}",
                    source_path=f"/synthetic/{index}", target_path=f"/synthetic/target-{index}", source_version="etag")
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(journalMode, connection.execute(f"PRAGMA journal_mode={journalMode}").fetchone()[0])
                sampleIds = [row[0] for row in connection.execute(f"SELECT id FROM {table} ORDER BY id DESC")]

            def writeDuringRead():
                with closing(sqlite3.connect(settings.database_path, timeout=0)) as writer:
                    try:
                        writer.execute(f"DELETE FROM {table} WHERE id=?", (sampleIds[0],))
                        writer.commit()
                        return "committed"
                    except sqlite3.OperationalError as error:
                        writer.rollback()
                        if "locked" not in str(error).lower():
                            raise
                        return "locked"

            writerResults = []
            with ThreadPoolExecutor(max_workers=1) as executor:
                class SnapshotCursor(sqlite3.Cursor):
                    def fetchone(cursor):
                        row = super().fetchone()
                        # COUNT已完成，但分页SELECT尚未开始；此时插入独立线程的写入。
                        if cursor.description and not writerResults:
                            writerResults.append(executor.submit(writeDuringRead).result(timeout=5))
                        return row

                class SnapshotConnection(sqlite3.Connection):
                    def execute(connection, sql, parameters=()):
                        if "COUNT(*)" in sql:
                            return connection.cursor(factory=SnapshotCursor).execute(sql, parameters)
                        return super().execute(sql, parameters)

                def readerConnection(_settings):
                    connection = sqlite3.connect(settings.database_path, factory=SnapshotConnection)
                    connection.row_factory = sqlite3.Row
                    return connection

                with patch.object(module, "_connect", side_effect=readerConnection):
                    page = loadPage(settings, page=99, page_size=10)
                self.assertEqual((2, 1, sampleIds), (page.total, page.page, [item.id for item in page.items]))
                self.assertEqual(["committed" if journalMode == "wal" else "locked"], writerResults)

            # 读事务关闭后写入必须可提交，避免把锁遗留误判为快照通过。
            with closing(sqlite3.connect(settings.database_path, timeout=0)) as connection:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(1 if journalMode == "wal" else 2, count)
                connection.execute(f"DELETE FROM {table} WHERE id=?", (sampleIds[0],))
                connection.commit()
            refreshed = loadPage(settings, page=99, page_size=10)
            self.assertEqual((1, 1, sampleIds[1:]), (refreshed.total, refreshed.page, [item.id for item in refreshed.items]))

    def test_audit_snapshot_with_rollback_journal_writer(self):
        self.verifySnapshot("delete", audit_service, "audit_events", audit_service.list_audit_events)

    def test_audit_snapshot_with_wal_writer(self):
        self.verifySnapshot("wal", audit_service, "audit_events", audit_service.list_audit_events)

    def test_remote_snapshot_with_rollback_journal_writer(self):
        self.verifySnapshot("delete", remote_operation_service, "remote_operation_items",
            remote_operation_service.list_remote_operation_items)

    def test_remote_snapshot_with_wal_writer(self):
        self.verifySnapshot("wal", remote_operation_service, "remote_operation_items",
            remote_operation_service.list_remote_operation_items)
