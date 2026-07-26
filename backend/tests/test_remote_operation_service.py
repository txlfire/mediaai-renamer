import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source
from app.service.remote_operation_service import (
    RemoteOperationLockConflict,
    RemoteOperationLockError,
    acquire_remote_operation_lock,
    create_remote_operation_item,
    heartbeat_remote_operation_lock,
    list_remote_operation_items,
    release_remote_operation_lock,
    update_remote_operation_item_status,
)


class RemoteOperationServiceTest(unittest.TestCase):
    """远程操作锁和幂等记录服务测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def create_source(self, settings: AppSettings) -> int:
        media_dir = settings.data_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        source = create_media_source(settings, "media", media_dir, True)
        return source.id

    def test_acquire_lock_blocks_competing_active_lease_and_release_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)

            first = acquire_remote_operation_lock(
                settings,
                media_source_id=source_id,
                lock_key=f"media-source:{source_id}:write",
                owner="admin",
                task_type="rename_operation",
                task_id=1,
                ttl_seconds=60,
            )

            with self.assertRaises(RemoteOperationLockConflict):
                acquire_remote_operation_lock(
                    settings,
                    media_source_id=source_id,
                    lock_key=f"media-source:{source_id}:write",
                    owner="other",
                    task_type="rename_operation",
                    task_id=2,
                    ttl_seconds=60,
                )

            self.assertTrue(release_remote_operation_lock(settings, first.lock_key, first.lease_token))
            second = acquire_remote_operation_lock(
                settings,
                media_source_id=source_id,
                lock_key=f"media-source:{source_id}:write",
                owner="other",
                task_type="rename_operation",
                task_id=2,
                ttl_seconds=60,
            )

            self.assertNotEqual(first.lease_token, second.lease_token)
            self.assertEqual("other", second.owner)

    def test_expired_lock_can_be_reacquired_and_stale_token_cannot_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)

            first = acquire_remote_operation_lock(
                settings,
                media_source_id=source_id,
                lock_key=f"media-source:{source_id}:write",
                owner="admin",
                ttl_seconds=1,
            )
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute(
                    "UPDATE remote_operation_locks SET expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00+00:00", first.id),
                )
                connection.commit()

            second = acquire_remote_operation_lock(
                settings,
                media_source_id=source_id,
                lock_key=f"media-source:{source_id}:write",
                owner="admin",
                ttl_seconds=60,
            )

            self.assertNotEqual(first.lease_token, second.lease_token)
            with self.assertRaises(RemoteOperationLockError):
                heartbeat_remote_operation_lock(settings, first.lock_key, first.lease_token)

    def test_create_remote_operation_item_is_idempotent_by_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)

            first = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:source-a:target-b",
                source_path="/remote/source-a.mkv",
                target_path="/remote/target-b.mkv",
                source_version="etag-a",
                target_version=None,
                recovery={"step": "prepared"},
            )
            second = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:source-a:target-b",
                source_path="/remote/source-a.mkv",
                target_path="/remote/target-b.mkv",
                source_version="etag-a",
                target_version=None,
                recovery={"step": "ignored"},
            )

            self.assertEqual(first.id, second.id)
            self.assertEqual("pending", second.status)
            self.assertEqual({"step": "prepared"}, second.recovery)

    def test_update_remote_operation_item_status_records_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)
            item = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:source-a:target-b",
                source_path="/remote/source-a.mkv",
                target_path="/remote/target-b.mkv",
            )

            updated = update_remote_operation_item_status(
                settings,
                item.id,
                "completed",
                target_version="etag-b",
                recovery={"completed": True},
            )

            self.assertEqual("completed", updated.status)
            self.assertEqual("etag-b", updated.target_version)
            self.assertEqual({"completed": True}, updated.recovery)

    def test_list_remote_operation_items_returns_newest_first_with_source_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)
            first = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:list:first",
                source_path="/remote/first.mkv",
                target_path="/remote/first-safe.mkv",
            )
            second = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:list:second",
                source_path="/remote/second.mkv",
                target_path="/remote/second-safe.mkv",
            )
            third = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rollback",
                idempotency_key="rollback:list:third",
                source_path="/remote/third-safe.mkv",
                target_path="/remote/third.mkv",
            )

            page = list_remote_operation_items(settings, page=1, page_size=2)

            self.assertEqual(3, page.total)
            self.assertEqual(1, page.page)
            self.assertEqual(2, page.page_size)
            self.assertEqual([third.id, second.id], [item.id for item in page.items])
            self.assertEqual("media", page.items[0].media_source_name)
            self.assertNotEqual(first.id, page.items[0].id)

    def test_list_remote_operation_items_filters_source_type_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            source_id = self.create_source(settings)
            rename_item = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rename",
                idempotency_key="rename:filter:item",
                source_path="/remote/rename.mkv",
                target_path="/remote/rename-safe.mkv",
            )
            rollback_item = create_remote_operation_item(
                settings,
                media_source_id=source_id,
                operation_type="rollback",
                idempotency_key="rollback:filter:item",
                source_path="/remote/rollback-safe.mkv",
                target_path="/remote/rollback.mkv",
            )
            update_remote_operation_item_status(settings, rename_item.id, "completed")
            update_remote_operation_item_status(
                settings,
                rollback_item.id,
                "failed",
                error_message="模拟恢复失败",
            )

            page = list_remote_operation_items(
                settings,
                media_source_id=source_id,
                operation_type="rollback",
                status="failed",
            )

            self.assertEqual(1, page.total)
            self.assertEqual(rollback_item.id, page.items[0].id)
            self.assertEqual("模拟恢复失败", page.items[0].error_message)

    def test_list_remote_operation_items_normalizes_pagination_bounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)

            page = list_remote_operation_items(settings, page=0, page_size=1000)

            self.assertEqual(1, page.page)
            self.assertEqual(100, page.page_size)
            self.assertEqual([], page.items)
            self.assertEqual(0, page.total)


if __name__ == "__main__":
    unittest.main()
