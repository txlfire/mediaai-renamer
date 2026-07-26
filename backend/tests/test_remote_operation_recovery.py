"""WebDAV 远程操作失败恢复测试。"""

from __future__ import annotations

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.remote_operation_recovery_service import (
    RemoteOperationRecoveryConflict,
    recover_remote_operation,
)
from app.service.remote_operation_service import (
    acquire_remote_operation_lock,
    create_remote_operation_item,
    update_remote_operation_item_status,
)
from app.service.shared_protocols.base import ConnectionTestResult


class RemoteOperationRecoveryTest(unittest.TestCase):
    """远程重命名与回滚失败恢复行为。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = AppSettings(
            data_dir=self.root,
            database_path=self.root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=self.root / "logs", console_output=False),
        )
        ensure_database(self.settings)
        self._insert_webdav_rename_records()

    def tearDown(self):
        shutdown_logging()
        self.temp_dir.cleanup()

    def test_failed_rename_recovery_retries_move_and_repairs_business_records(self):
        remote_item_id = self._create_failed_remote_rename()
        readiness = ConnectionTestResult(True, "WebDAV MOVE dry-run 可执行")
        move_result = ConnectionTestResult(True, "WebDAV MOVE 执行成功")

        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                return_value=readiness,
            ),
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                return_value=move_result,
            ) as move_mock,
        ):
            result = recover_remote_operation(self.settings, remote_item_id, owner="admin")

        self.assertEqual("retried", result.action)
        self.assertEqual("completed", result.item.status)
        move_mock.assert_called_once()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            media_row = connection.execute(
                "SELECT file_path, file_name FROM media_files WHERE id = 1"
            ).fetchone()
            preview_row = connection.execute(
                "SELECT status FROM rename_previews WHERE id = 1"
            ).fetchone()
            operation_row = connection.execute(
                "SELECT status, renamed_count, failed_count FROM rename_operations WHERE id = 1"
            ).fetchone()
            operation_item_row = connection.execute(
                "SELECT status, message FROM rename_operation_items WHERE id = 1"
            ).fetchone()

        self.assertEqual(("https://nas.example/dav/Movie.Safe.mkv", "Movie.Safe.mkv"), media_row)
        self.assertEqual(("renamed",), preview_row)
        self.assertEqual(("completed", 1, 0), operation_row)
        self.assertEqual(("renamed", None), operation_item_row)

    def test_recovery_reconciles_database_when_remote_move_already_succeeded(self):
        remote_item_id = self._create_failed_remote_rename()
        source_missing = ConnectionTestResult(False, "WebDAV 源文件不存在")
        reverse_ready = ConnectionTestResult(True, "WebDAV MOVE dry-run 可执行")

        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                side_effect=[source_missing, reverse_ready],
            ),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as move_mock,
        ):
            result = recover_remote_operation(self.settings, remote_item_id, owner="admin")

        self.assertEqual("reconciled", result.action)
        self.assertEqual("completed", result.item.status)
        move_mock.assert_not_called()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            operation_item_status = connection.execute(
                "SELECT status FROM rename_operation_items WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual("renamed", operation_item_status)

    def test_recovery_marks_ambiguous_remote_state_for_manual_handling(self):
        remote_item_id = self._create_failed_remote_rename()
        both_exist = ConnectionTestResult(False, "WebDAV 目标文件已存在")

        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                side_effect=[both_exist, both_exist],
            ),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as move_mock,
        ):
            with self.assertRaises(RemoteOperationRecoveryConflict):
                recover_remote_operation(self.settings, remote_item_id, owner="admin")

        move_mock.assert_not_called()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            remote_row = connection.execute(
                "SELECT status, error_message FROM remote_operation_items WHERE id = ?",
                (remote_item_id,),
            ).fetchone()
        self.assertEqual("recovery_required", remote_row[0])
        self.assertIn("无法确认", remote_row[1])

    def test_failed_rollback_recovery_retries_reverse_move_and_repairs_plan(self):
        remote_item_id = self._create_failed_remote_rollback()
        readiness = ConnectionTestResult(True, "WebDAV MOVE dry-run 可执行")
        move_result = ConnectionTestResult(True, "WebDAV MOVE 执行成功")

        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                return_value=readiness,
            ),
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                return_value=move_result,
            ),
        ):
            result = recover_remote_operation(self.settings, remote_item_id, owner="admin")

        self.assertEqual("retried", result.action)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            media_row = connection.execute(
                "SELECT file_path, file_name FROM media_files WHERE id = 1"
            ).fetchone()
            preview_row = connection.execute(
                "SELECT status, message FROM rename_previews WHERE id = 1"
            ).fetchone()
            rollback_item_row = connection.execute(
                "SELECT status, message, executed_at FROM rename_rollback_items WHERE id = 1"
            ).fetchone()
            plan_row = connection.execute(
                "SELECT status, executable_count, conflict_count FROM rename_rollback_plans WHERE id = 1"
            ).fetchone()

        self.assertEqual(
            ("https://nas.example/dav/Movie.Original.mkv", "Movie.Original.mkv"),
            media_row,
        )
        self.assertEqual(("rolled_back", "已回滚"), preview_row)
        self.assertEqual("rolled_back", rollback_item_row[0])
        self.assertIsNone(rollback_item_row[1])
        self.assertTrue(rollback_item_row[2])
        self.assertEqual(("executed", 1, 0), plan_row)

    def test_remote_operation_api_returns_detail_and_recovery_result(self):
        remote_item_id = self._create_failed_remote_rename()
        client = TestClient(create_app(self.settings))
        readiness = ConnectionTestResult(True, "WebDAV MOVE dry-run 可执行")
        move_result = ConnectionTestResult(True, "WebDAV MOVE 执行成功")

        detail_response = client.get(f"/api/remote-operations/{remote_item_id}")
        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                return_value=readiness,
            ),
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                return_value=move_result,
            ),
        ):
            recover_response = client.post(f"/api/remote-operations/{remote_item_id}/recover")

        self.assertEqual(200, detail_response.status_code)
        self.assertEqual("failed", detail_response.json()["status"])
        self.assertEqual(200, recover_response.status_code)
        self.assertEqual("retried", recover_response.json()["action"])
        self.assertEqual("completed", recover_response.json()["item"]["status"])

    def test_remote_operation_api_returns_conflict_when_write_lock_is_active(self):
        remote_item_id = self._create_failed_remote_rename()
        acquire_remote_operation_lock(
            self.settings,
            media_source_id=1,
            lock_key="media-source:1:write",
            owner="other",
            task_type="rename_operation",
            task_id=99,
            ttl_seconds=60,
        )
        client = TestClient(create_app(self.settings))

        response = client.post(f"/api/remote-operations/{remote_item_id}/recover")

        self.assertEqual(409, response.status_code)
        self.assertIn("正在执行写操作", response.json()["detail"])

    def _create_failed_remote_rename(self) -> int:
        item = create_remote_operation_item(
            self.settings,
            media_source_id=1,
            operation_type="rename",
            idempotency_key="rename-operation:1:item:1",
            source_path="https://nas.example/dav/Movie.Original.mkv",
            target_path="https://nas.example/dav/Movie.Safe.mkv",
            recovery={
                "operation_id": 1,
                "operation_item_id": 1,
                "rename_preview_id": 1,
            },
        )
        update_remote_operation_item_status(
            self.settings,
            item.id,
            "failed",
            error_message="模拟网络中断",
        )
        return item.id

    def _create_failed_remote_rollback(self) -> int:
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "UPDATE media_files SET file_path = ?, file_name = ? WHERE id = 1",
                ("https://nas.example/dav/Movie.Safe.mkv", "Movie.Safe.mkv"),
            )
            connection.execute(
                "UPDATE rename_previews SET status = 'renamed' WHERE id = 1"
            )
            connection.execute(
                "UPDATE rename_operations SET status = 'completed', renamed_count = 1, "
                "failed_count = 0 WHERE id = 1"
            )
            connection.execute(
                "UPDATE rename_operation_items SET status = 'renamed', message = NULL WHERE id = 1"
            )
            connection.execute(
                "INSERT INTO rename_rollback_plans "
                "(id, operation_id, status, item_count, executable_count, conflict_count, "
                "created_at, updated_at) VALUES "
                "(1, 1, 'failed', 1, 0, 1, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_rollback_items "
                "(id, plan_id, operation_item_id, current_path, rollback_path, status, "
                "message, created_at, updated_at) VALUES "
                "(1, 1, 1, 'https://nas.example/dav/Movie.Safe.mkv', "
                "'https://nas.example/dav/Movie.Original.mkv', 'failed', "
                "'模拟网络中断', 'now', 'now')"
            )
            connection.commit()
        item = create_remote_operation_item(
            self.settings,
            media_source_id=1,
            operation_type="rollback",
            idempotency_key="rollback-plan:1:item:1",
            source_path="https://nas.example/dav/Movie.Safe.mkv",
            target_path="https://nas.example/dav/Movie.Original.mkv",
            recovery={
                "operation_id": 1,
                "operation_item_id": 1,
                "rollback_plan_id": 1,
                "rollback_item_id": 1,
            },
        )
        update_remote_operation_item_status(
            self.settings,
            item.id,
            "failed",
            error_message="模拟网络中断",
        )
        return item.id

    def _insert_webdav_rename_records(self) -> None:
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO media_sources "
                "(id, name, path, path_type, protocol, auth_type, enabled, created_at, updated_at) "
                "VALUES (1, 'webdav', 'https://nas.example/dav/', 'webdav', 'webdav', "
                "'none', 1, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES "
                "(1, 1, 1, 'https://nas.example/dav/Movie.Original.mkv', "
                "'Movie.Original.mkv', '.mkv', 2048, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_previews "
                "(id, media_file_id, media_type, parsed_title, original_extension, suggested_name, "
                "edited_name, status, created_at, updated_at) VALUES "
                "(1, 1, 'movie', 'Movie', '.mkv', 'Movie.Safe.mkv', "
                "'Movie.Safe.mkv', 'generated', 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operations "
                "(id, status, mode, total_count, ready_count, conflict_count, renamed_count, "
                "failed_count, created_at, updated_at) VALUES "
                "(1, 'failed', 'safe_rename', 1, 0, 0, 0, 1, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(id, operation_id, rename_preview_id, source_path, target_path, status, "
                "message, created_at, updated_at) VALUES "
                "(1, 1, 1, 'https://nas.example/dav/Movie.Original.mkv', "
                "'https://nas.example/dav/Movie.Safe.mkv', 'failed', "
                "'模拟网络中断', 'now', 'now')"
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()
