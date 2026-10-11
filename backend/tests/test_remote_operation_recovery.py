"""WebDAV 远程操作失败恢复测试。"""

from __future__ import annotations

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from api_test_support import authenticatedClient as TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.remote_operation_recovery_service import (
    RemoteOperationRecoveryConflict,
    recover_remote_operation,
)
from app.service.remote_operation_service import (
    RemoteOperationLockError,
    RemoteOperationLockConflict,
    acquire_remote_operation_lock,
    create_remote_operation_item,
    update_remote_operation_item_status,
)
from app.service.shared_protocols.base import ConnectionTestResult, RemoteFileEntry
from app.service.audit_service import AuditWriteContext
from app.service.file_action_audit_service import createFileActionAuditIntent, flushFileActionAuditIntents


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
        infoPatch = patch(
            "app.service.shared_protocols.webdav.WebDavProtocol.get_file_info",
            side_effect=lambda path, context=None: RemoteFileEntry(
                path=path, name=path.rsplit("/", 1)[-1], extension=".mkv", file_size=2048,
                modified_at="2026-01-01T00:00:00+00:00", version='"synthetic-etag"',
            ),
        )
        infoPatch.start()
        self.addCleanup(infoPatch.stop)

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

    def test_completed_history_checks_business_without_replaying_move(self):
        remoteItemId = self._create_failed_remote_rename()
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "ready")),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", return_value=ConnectionTestResult(True, "moved")) as mover,
        ):
            recover_remote_operation(self.settings, remoteItemId)
            mover.reset_mock()
            repeated = recover_remote_operation(self.settings, remoteItemId)
            self.assertEqual("already_completed", repeated.action)
            mover.assert_not_called()
            with closing(sqlite3.connect(self.settings.database_path)) as connection:
                connection.execute("UPDATE media_files SET file_path = 'subsequent-path' WHERE id = 1")
                connection.commit()
            with self.assertRaisesRegex(RemoteOperationRecoveryConflict, "不自动重放"):
                recover_remote_operation(self.settings, remoteItemId)
            mover.assert_not_called()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("completed", connection.execute(
                "SELECT status FROM remote_operation_items WHERE id = ?", (remoteItemId,),
            ).fetchone()[0])
            self.assertEqual("subsequent-path", connection.execute("SELECT file_path FROM media_files WHERE id = 1").fetchone()[0])

    def test_partial_recovery_with_ready_items_keeps_batch_interrupted(self):
        remoteItemId = self._create_failed_remote_rename()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO rename_operation_items (operation_id, rename_preview_id, source_path, target_path, "
                "status, created_at, updated_at) VALUES (1, 999, 'other', 'other-target', 'ready', 'now', 'now')"
            )
            connection.commit()
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", return_value=ConnectionTestResult(True, "ready")),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file", return_value=ConnectionTestResult(True, "moved")),
        ):
            recover_remote_operation(self.settings, remoteItemId)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(("interrupted", 1, 1), connection.execute(
                "SELECT status, ready_count, renamed_count FROM rename_operations WHERE id = 1",
            ).fetchone())

    def test_remote_applied_state_can_be_reconciled_without_second_move(self):
        remoteItemId = self._create_failed_remote_rename()
        update_remote_operation_item_status(self.settings, remoteItemId, "remote_applied")
        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", side_effect=[
                ConnectionTestResult(False, "源文件不存在"), ConnectionTestResult(True, "可恢复"),
            ]),
            patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover,
        ):
            result = recover_remote_operation(self.settings, remoteItemId, owner="admin")
        mover.assert_not_called()
        self.assertEqual("reconciled", result.action)
        self.assertEqual("completed", result.item.status)

    def test_recovery_rejects_unknown_source_version_or_changed_target_version(self):
        for scenario in ("legacy", "replaced"):
            with self.subTest(scenario=scenario):
                remoteItemId = self._create_failed_remote_rename(f"-{scenario}")
                with closing(sqlite3.connect(self.settings.database_path)) as connection:
                    connection.execute(
                        "UPDATE remote_operation_items SET source_version = ?, status = 'failed' WHERE id = ?",
                        (None if scenario == "legacy" else '"different-etag"', remoteItemId),
                    )
                    connection.commit()
                readiness = [ConnectionTestResult(True, "可执行")] if scenario == "legacy" else [
                    ConnectionTestResult(False, "源文件不存在"), ConnectionTestResult(True, "反向可执行"),
                ]
                with (
                    patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", side_effect=readiness),
                    patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover,
                ):
                    with self.assertRaisesRegex(RemoteOperationRecoveryConflict, "版本"):
                        recover_remote_operation(self.settings, remoteItemId, owner="admin")
                mover.assert_not_called()
                with closing(sqlite3.connect(self.settings.database_path)) as connection:
                    self.assertEqual("recovery_required", connection.execute(
                        "SELECT status FROM remote_operation_items WHERE id = ?", (remoteItemId,),
                    ).fetchone()[0])

    def test_recovery_business_failure_does_not_mark_completed(self):
        remoteItemId = self._create_failed_remote_rename()
        update_remote_operation_item_status(self.settings, remoteItemId, "remote_applied")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            originalPath = connection.execute("SELECT file_path FROM media_files WHERE id = 1").fetchone()[0]

        def failSync(connection, item, updatedAt):
            connection.execute("UPDATE media_files SET file_path = 'partial' WHERE id = 1")
            raise sqlite3.OperationalError("注入同步故障")

        with (
            patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready", side_effect=[
                ConnectionTestResult(False, "源文件不存在"), ConnectionTestResult(True, "可恢复"),
            ]),
            patch("app.service.remote_operation_recovery_service._sync_rename_records", side_effect=failSync),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                recover_remote_operation(self.settings, remoteItemId, owner="admin")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("recovery_required", connection.execute(
                "SELECT status FROM remote_operation_items WHERE id = ?", (remoteItemId,),
            ).fetchone()[0])
            self.assertEqual(originalPath, connection.execute(
                "SELECT file_path FROM media_files WHERE id = 1",
            ).fetchone()[0])

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

    def test_expired_move_lease_is_fenced_without_sending_second_move(self):
        remoteItemId = self._create_failed_remote_rename()
        moveStarted = Event()
        allowMoveReturn = Event()
        moveCalls: list[str] = []
        firstErrors: list[BaseException] = []

        def blockingMove(sourcePath, targetPath, context):
            moveCalls.append(sourcePath)
            moveStarted.set()
            self.assertTrue(allowMoveReturn.wait(timeout=5))
            return ConnectionTestResult(True, "迟到的 MOVE 成功响应")

        def runFirstRecovery():
            try:
                recover_remote_operation(self.settings, remoteItemId, owner="first")
            except BaseException as exc:
                firstErrors.append(exc)

        with (
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                return_value=ConnectionTestResult(True, "可执行"),
            ),
            patch(
                "app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                side_effect=blockingMove,
            ),
        ):
            firstThread = Thread(target=runFirstRecovery, daemon=True)
            firstThread.start()
            self.assertTrue(moveStarted.wait(timeout=5))
            with closing(sqlite3.connect(self.settings.database_path)) as connection:
                connection.execute(
                    "UPDATE remote_operation_locks SET expires_at = '2000-01-01T00:00:00+00:00' "
                    "WHERE lock_key = 'media-source:1:write'"
                )
                connection.commit()

            with self.assertRaisesRegex(RemoteOperationLockConflict, "人工核对"):
                recover_remote_operation(self.settings, remoteItemId, owner="second")

            allowMoveReturn.set()
            firstThread.join(timeout=5)

        self.assertFalse(firstThread.is_alive())
        self.assertEqual(1, len(moveCalls))
        self.assertEqual(1, len(firstErrors))
        self.assertIsInstance(firstErrors[0], RemoteOperationLockError)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            remoteRow = connection.execute(
                "SELECT status, recovery_phase, recovery_generation, recovery_token "
                "FROM remote_operation_items WHERE id = ?",
                (remoteItemId,),
            ).fetchone()
            businessStatus = connection.execute(
                "SELECT status FROM rename_operation_items WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual(("recovery_required", "review_required", 2, None), remoteRow)
        self.assertEqual("failed", businessStatus)

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

    def test_remote_operation_api_returns_filtered_paginated_list_without_credentials(self):
        rename_item_id = self._create_failed_remote_rename()
        rollback_item_id = self._create_failed_remote_rollback()
        client = TestClient(create_app(self.settings))

        response = client.get(
            "/api/remote-operations",
            params={
                "page": 1,
                "page_size": 10,
                "media_source_id": 1,
                "operation_type": "rollback",
                "status": "failed",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(1, payload["total"])
        self.assertEqual(1, payload["page"])
        self.assertEqual(10, payload["page_size"])
        self.assertEqual(rollback_item_id, payload["items"][0]["id"])
        self.assertEqual("webdav", payload["items"][0]["media_source_name"])
        self.assertNotEqual(rename_item_id, payload["items"][0]["id"])
        serialized = response.text.lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("encrypted_secret", serialized)

    def test_remote_operation_api_rejects_invalid_page_size_and_filter_values(self):
        client = TestClient(create_app(self.settings))

        oversized_response = client.get("/api/remote-operations?page_size=101")
        invalid_type_response = client.get("/api/remote-operations?operation_type=copy")
        invalid_status_response = client.get("/api/remote-operations?status=unknown")

        self.assertEqual(422, oversized_response.status_code)
        self.assertEqual(422, invalid_type_response.status_code)
        self.assertEqual(422, invalid_status_response.status_code)

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
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute("SELECT actor_name,result FROM audit_events "
                "WHERE action='recover_remote_operation' ORDER BY id").fetchall()
        self.assertEqual([("api-review", "pending"), ("api-review", "failed")], rows)

    def test_recovery_initial_audit_failure_never_calls_protocol(self):
        itemId = self._create_failed_remote_rename()
        client = TestClient(create_app(self.settings))
        with patch("app.service.file_action_audit_service.record_audit_event",
                   side_effect=sqlite3.OperationalError("synthetic audit failure")), \
             patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready") as readiness, \
             patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover:
            with self.assertRaises(sqlite3.OperationalError):
                client.post(f"/api/remote-operations/{itemId}/recover")
            readiness.assert_not_called()
            mover.assert_not_called()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM file_action_audit_intents").fetchone()[0])
            self.assertEqual("failed", connection.execute("SELECT status FROM remote_operation_items WHERE id=?",
                                                         (itemId,)).fetchone()[0])

    def test_recovery_final_audit_failure_preserves_result_and_flush_never_moves(self):
        from app.service.audit_service import record_audit_event
        itemId = self._create_failed_remote_rename()
        client = TestClient(create_app(self.settings))

        def failFinal(*args, **kwargs):
            if kwargs.get("result") != "pending":
                raise sqlite3.OperationalError("synthetic final audit failure")
            return record_audit_event(*args, **kwargs)

        with patch("app.service.file_action_audit_service.record_audit_event", side_effect=failFinal), \
             patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                   return_value=ConnectionTestResult(True, "ready")), \
             patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                   return_value=ConnectionTestResult(True, "moved")) as mover:
            response = client.post(f"/api/remote-operations/{itemId}/recover")
        self.assertEqual(200, response.status_code)
        self.assertEqual("completed", response.json()["item"]["status"])
        self.assertEqual("pending", response.headers["X-MediaAI-Audit-State"])
        mover.assert_called_once()
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover:
            self.assertEqual(1, flushFileActionAuditIntents(self.settings)["completed"])
            self.assertEqual(0, flushFileActionAuditIntents(self.settings)["completed"])
            mover.assert_not_called()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute("SELECT actor_name,result,detail_json FROM audit_events "
                "WHERE action='recover_remote_operation' AND result='success'").fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual(("api-review", "success"), rows[0][:2])
        self.assertIn('"action": "retried"', rows[0][2])

    def test_recovery_without_saved_outcome_does_not_infer_old_failure_or_replay(self):
        itemId = self._create_failed_remote_rename()
        createFileActionAuditIntent(self.settings, "remote_operation_recovery", itemId, AuditWriteContext())
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover:
            report = flushFileActionAuditIntents(self.settings)
            self.assertEqual(0, report["completed"])
            self.assertEqual(1, report["pending"])
            mover.assert_not_called()

    def test_recovery_without_saved_outcome_does_not_finalize_from_completed_business(self):
        itemId = self._create_failed_remote_rename()
        createFileActionAuditIntent(self.settings, "remote_operation_recovery", itemId, AuditWriteContext())
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.check_rename_ready",
                   return_value=ConnectionTestResult(True, "ready")), \
             patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file",
                   return_value=ConnectionTestResult(True, "moved")):
            recover_remote_operation(self.settings, itemId)
        with patch("app.service.shared_protocols.webdav.WebDavProtocol.move_file") as mover:
            report = flushFileActionAuditIntents(self.settings)
            self.assertEqual(0, report["completed"])
            self.assertEqual(1, report["pending"])
            mover.assert_not_called()

    def _create_failed_remote_rename(self, keySuffix: str = "") -> int:
        item = create_remote_operation_item(
            self.settings,
            media_source_id=1,
            operation_type="rename",
            idempotency_key=f"rename-operation:1:item:1{keySuffix}",
            source_path="https://nas.example/dav/Movie.Original.mkv",
            target_path="https://nas.example/dav/Movie.Safe.mkv",
            source_version='"synthetic-etag"',
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
            source_version='"synthetic-etag"',
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
